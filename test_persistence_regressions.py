"""Offline handler regressions: no configuration, bot, or database connections.

The connection doubles model transaction-scoped row locks and explicit task
interleavings, not PostgreSQL isolation, rollback, or SQL execution.
"""
import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import beast_v022
import bonds_v022
import combat_store
import inventory
import sect_halls
from combat import begin
from game import default_state
from storage import Repository


T = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
GUILD = 7
USER = 101


class FixedClock(datetime):
    @staticmethod
    def now(tz):
        return T + timedelta(hours=2)


def mock_repository(con):
    con.transaction.return_value.__aenter__.return_value = None
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = con
    return Repository(pool)


class LockingConnection:
    def __init__(self, db):
        self.db = db
        self.held = {}

    async def lock(self, key):
        if key not in self.held:
            lock = self.db.locks.setdefault(key, asyncio.Lock())
            await lock.acquire()
            self.held[key] = lock
            self.db.lock_order.append(key)

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        finally:
            for lock in reversed(list(self.held.values())):
                lock.release()
            self.held.clear()


class CombatConnection(LockingConnection):
    async def fetchval(self, sql, *args):
        if sql.startswith('SELECT 1 FROM characters'):
            return 1
        if sql.startswith('SELECT root FROM character_origins'):
            return 'Lightning'
        if 'FROM combat_actions' in sql or 'FROM living_dao_actions' in sql:
            return self.db.actions.get(args)
        if sql.startswith('SELECT data FROM living_dao_players'):
            if 'FOR UPDATE' in sql:
                await self.lock('living')
            snapshot = json.dumps(self.db.living) if self.db.living else None
            if self.db.pause_read:
                self.db.combat_read.set()
                await self.db.resume_combat.wait()
            return snapshot
        if sql.startswith('SELECT state FROM characters'):
            return json.dumps(default_state(T))
        raise AssertionError(f'Unexpected fetchval: {sql}')

    async def fetchrow(self, sql, *args):
        if 'FROM combat_sessions' in sql:
            await self.lock('combat')
            return {'battle': json.dumps(self.db.battle)}
        if 'FROM living_dao_players' in sql:
            self.db.companion_attempt.set()
            await self.lock('living')
            return {'data': json.dumps(self.db.living)}
        if 'FROM player_inventory' in sql:
            await self.lock('inventory')
            return deepcopy(self.db.stock)
        raise AssertionError(f'Unexpected fetchrow: {sql}')

    async def execute(self, sql, *args):
        if sql.startswith('INSERT INTO combat_sessions') or sql.startswith('INSERT INTO player_inventory'):
            return
        if sql.startswith('UPDATE living_dao_players'):
            await self.lock('living')  # UPDATE also takes a row lock.
            self.db.living = json.loads(args[2])
        elif sql.startswith('UPDATE combat_sessions'):
            self.db.battle = json.loads(args[2])
        elif sql.startswith('UPDATE player_inventory'):
            self.db.stock = {'holdings': json.loads(args[2]), 'marks': args[3],
                             'equipped': json.loads(args[4])}
        elif sql.startswith('INSERT INTO combat_actions') or sql.startswith('INSERT INTO living_dao_actions'):
            self.db.actions[args[:3]] = args[3]
        else:
            raise AssertionError(f'Unexpected execute: {sql}')


class CombatDatabase:
    def __init__(self):
        self.locks = {}
        self.lock_order = []
        self.battle = {}
        self.stock = inventory.empty()
        self.living = beast_v022.settle({'companion': 'storm_hawk', 'bond': 2}, T)
        self.living['beast_cultivation']['qi'] = beast_v022.cap(1)
        self.actions = {}
        self.pause_read = False
        self.combat_read = asyncio.Event()
        self.resume_combat = asyncio.Event()
        self.companion_attempt = asyncio.Event()

    @asynccontextmanager
    async def acquire(self):
        yield CombatConnection(self)

    def repository(self):
        return SimpleNamespace(pool=self)


class CombatPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_battle_start_preserves_concurrent_beast_advance(self):
        db = CombatDatabase()
        db.pause_read = True
        repo = db.repository()
        real_settle = beast_v022.settle
        with patch('beast_v022.settle', side_effect=lambda state, now: real_settle(state, T)):
            battle = asyncio.create_task(combat_store.execute(repo, GUILD, USER, 1001, 'start'))
            await asyncio.wait_for(db.combat_read.wait(), 2)
            companion = asyncio.create_task(beast_v022.action(repo, GUILD, USER, 1002, 'advance'))
            await asyncio.wait_for(db.companion_attempt.wait(), 2)
            db.resume_combat.set()
            replies = await asyncio.wait_for(asyncio.gather(battle, companion), 2)
        self.assertIn('Stage 2', replies[1])
        self.assertEqual(db.living['beast_cultivation']['stage'], 2)
        self.assertEqual(db.living['bond'], 2)
        self.assertEqual(db.battle['status'], 'active')

    async def test_battle_start_locks_companion_before_inventory(self):
        db = CombatDatabase()
        await combat_store.execute(db.repository(), GUILD, USER, 1001, 'start')
        self.assertLess(db.lock_order.index('living'), db.lock_order.index('inventory'))

    async def test_duplicate_victory_pays_once(self):
        db = CombatDatabase()
        db.battle = begin()
        replies = await asyncio.wait_for(asyncio.gather(*(
            combat_store.execute(db.repository(), GUILD, USER, 1001, 'auto')
            for _ in range(2)
        )), 2)
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(db.battle['status'], 'victory')
        self.assertEqual(db.stock['marks'], 8)
        self.assertEqual(db.stock['holdings'], {'wolf_fang': 1})

    async def test_concurrent_turns_preserve_both_actions(self):
        db = CombatDatabase()
        db.battle = begin()
        await asyncio.wait_for(asyncio.gather(*(
            combat_store.execute(db.repository(), GUILD, USER, event, 'turn', action='strike')
            for event in (1001, 1002)
        )), 2)
        self.assertEqual(db.battle['turn'], 2)
        self.assertEqual(db.battle['mastery']['strike'], 2)
        self.assertEqual(db.battle['enemy_hp'], 38)

    async def test_start_without_companion_record(self):
        db = CombatDatabase()
        db.living = None
        await combat_store.execute(db.repository(), GUILD, USER, 1001, 'start')
        self.assertIsNone(db.living)
        self.assertIsNone(db.battle['beast'])
        self.assertEqual(db.battle['status'], 'active')


class SectTrialHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hall = sect_halls.unpack(None)
        self.actions = {}
        self.con = MagicMock()
        self.con.fetchval = AsyncMock(side_effect=self.fetchval)
        self.con.fetchrow = AsyncMock(side_effect=self.fetchrow)
        self.con.execute = AsyncMock(side_effect=self.execute)
        self.repo = mock_repository(self.con)

    async def fetchval(self, sql, *args):
        if 'FROM sect_hall_actions' in sql:
            return self.actions.get(args)
        if 'FROM living_dao_players' in sql:
            return json.dumps({'sect': 'Azure'})
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        if 'FROM living_dao_sects' in sql:
            return {'founder_id': USER, 'contribution': 100}
        if 'FROM sect_hall_state' in sql:
            return {'data': json.dumps(self.hall)}
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if sql.startswith('UPDATE sect_hall_state'):
            self.hall = json.loads(args[2])
        elif sql.startswith('INSERT INTO sect_hall_actions'):
            self.actions[args[:3]] = args[3]
        elif not sql.startswith('INSERT INTO sect_hall_state'):
            raise AssertionError(sql)

    async def test_eligible_trial_awards_and_persists_once(self):
        stock = inventory.empty()
        with patch('sect_halls.datetime', FixedClock), \
                patch('inventory._locked', AsyncMock(return_value=stock)), \
                patch('inventory._save', AsyncMock()) as save:
            reply = await sect_halls.act(self.repo, GUILD, USER, 1001, 'trial')
            repeated = await sect_halls.act(self.repo, GUILD, USER, 1001, 'trial')
        self.assertEqual(reply, repeated)
        self.assertEqual(stock['marks'], 10)
        self.assertEqual(self.hall['trials'], 1)
        self.assertEqual(self.hall['last_trial'], FixedClock.now(timezone.utc).isoformat())
        save.assert_awaited_once()

    async def test_trial_cooldown_does_not_pay(self):
        self.hall['last_trial'] = (FixedClock.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with patch('sect_halls.datetime', FixedClock), \
                patch('inventory._save', AsyncMock()) as save:
            with self.assertRaisesRegex(ValueError, 'recovering'):
                await sect_halls.act(self.repo, GUILD, USER, 1001, 'trial')
        save.assert_not_awaited()
        self.assertEqual(self.hall['trials'], 0)


class BondConnection(LockingConnection):
    async def fetchval(self, sql, *args):
        if sql.startswith('SELECT 1 FROM characters'):
            return 1
        if sql.startswith('SELECT root FROM character_origins'):
            return 'Lightning'
        if 'FROM dao_household_actions' in sql:
            return self.db.actions.get(args)
        if sql.startswith('SELECT data FROM dao_households'):
            await self.lock(('household', args[1]))
            return json.dumps(self.db.houses[args[1]])
        if 'FROM dao_player_bonds' in sql:
            participants = set(args[1:])
            active = any(bond['status'] == 'active' and
                         (participants == set(pair) if '((proposer=' in sql
                          else participants.intersection(pair))
                         for pair, bond in self.db.bonds.items())
            # Both unprotected acceptances can observe the same empty result.
            await asyncio.sleep(0)
            return 1 if active else None
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        if 'FROM dao_households' in sql:
            await self.lock(('household', args[1]))
            return {'data': json.dumps(self.db.houses[args[1]])}
        if 'FROM dao_player_bonds' in sql:
            await self.lock(('invitation', args[1], args[2]))
            return deepcopy(self.db.bonds.get(args[1:]))
        if 'FROM dao_dual_requests' in sql:
            await self.lock(('dual_request', args[1], args[2]))
            return deepcopy(self.db.dual_requests.get(args[1:]))
        if 'FROM characters' in sql:
            await self.lock(('character', args[1]))
            return {'state': json.dumps(self.db.characters[args[1]])}
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if sql.startswith('INSERT INTO dao_households'):
            if args[1] not in self.db.houses:
                await self.lock(('household', args[1]))
                self.db.houses.setdefault(args[1], bonds_v022.normal(None))
        elif sql.startswith('UPDATE dao_households'):
            self.db.houses[args[1]] = json.loads(args[2])
        elif sql.startswith('UPDATE dao_player_bonds'):
            self.db.bonds[args[1:3]]['status'] = args[3]
        elif sql.startswith('INSERT INTO dao_household_actions'):
            self.db.actions[args[:3]] = args[3]
        elif sql.startswith('UPDATE characters'):
            self.db.characters[args[1]] = json.loads(args[2])
        elif sql.startswith('UPDATE dao_dual_requests'):
            self.db.dual_requests[args[1:3]]['status'] = args[3]
        else:
            raise AssertionError(sql)


class BondDatabase:
    def __init__(self, invitations):
        self.locks = {}
        self.lock_order = []
        self.houses = {}
        self.actions = {}
        self.dual_requests = {}
        self.characters = {uid: default_state(T) for pair in invitations for uid in pair}
        self.bonds = {pair: {'role': 'spouse', 'status': 'pending',
                             'created_at': T} for pair in invitations}

    @asynccontextmanager
    async def acquire(self):
        yield BondConnection(self)

    def repository(self):
        return SimpleNamespace(pool=self)


class PartnershipHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def accept_together(self, invitations, *, existing_households=False):
        db = BondDatabase(invitations)
        if existing_households:
            db.houses = {uid: bonds_v022.normal(None) for pair in invitations for uid in pair}
        with patch('bonds_v022.datetime', FixedClock):
            replies = await asyncio.wait_for(asyncio.gather(*(
                bonds_v022.apply(db.repository(), GUILD, recipient, event, 'accept', target=proposer)
                for event, (proposer, recipient) in enumerate(invitations, 1001)
            ), return_exceptions=True), 2)
        self.assertEqual(sum(isinstance(reply, str) for reply in replies), 1, replies)
        self.assertEqual(sum(isinstance(reply, ValueError) for reply in replies), 1, replies)
        self.assertEqual(sum(bond['status'] == 'active' for bond in db.bonds.values()), 1)
        return db

    async def test_shared_proposer_can_only_gain_one_partner(self):
        await self.accept_together(((101, 202), (101, 303)))

    async def test_shared_recipient_can_only_gain_one_partner(self):
        await self.accept_together(((101, 303), (202, 303)))

    async def test_shared_proposer_with_existing_households(self):
        await self.accept_together(((101, 202), (101, 303)), existing_households=True)

    async def test_opposite_invitations_complete_without_deadlock(self):
        await self.accept_together(((101, 202), (202, 101)))

    async def test_repeated_acceptance_returns_saved_response(self):
        db = BondDatabase(((101, 202),))
        with patch('bonds_v022.datetime', FixedClock):
            replies = await asyncio.wait_for(asyncio.gather(*(
                bonds_v022.apply(db.repository(), GUILD, 202, 1001, 'accept', target=101)
                for _ in range(2)
            )), 2)
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(db.bonds[(101, 202)]['status'], 'active')

    async def test_acceptance_requires_pending_consent(self):
        db = BondDatabase(((101, 202),))
        db.bonds[(101, 202)]['status'] = 'declined'
        with patch('bonds_v022.datetime', FixedClock):
            with self.assertRaisesRegex(ValueError, 'No pending invitation'):
                await bonds_v022.apply(db.repository(), GUILD, 202, 1001, 'accept', target=101)
        self.assertEqual(db.bonds[(101, 202)]['status'], 'declined')

    async def test_opposite_dual_acceptances_do_not_deadlock_or_double_boost(self):
        db = BondDatabase(((101, 202),))
        db.bonds[(101, 202)]['status'] = 'active'
        db.dual_requests = {pair: {'status': 'pending', 'expires_at': T + timedelta(hours=3)}
                            for pair in ((101, 202), (202, 101))}
        repo = db.repository()
        repo.settle_character = AsyncMock()
        with patch('bonds_v022.datetime', FixedClock):
            replies = await asyncio.wait_for(asyncio.gather(*(
                bonds_v022.apply(repo, GUILD, recipient, event, 'dual_accept', target=proposer)
                for event, (proposer, recipient) in enumerate(db.dual_requests, 1001)
            ), return_exceptions=True), 2)
        self.assertEqual(sum(isinstance(reply, str) for reply in replies), 1, replies)
        self.assertEqual(sum(isinstance(reply, ValueError) for reply in replies), 1, replies)
        for uid in (101, 202):
            self.assertEqual(len(db.characters[uid]['dual_boons']), 1)
            self.assertEqual(db.houses[uid]['last_dual_at'], FixedClock.now(timezone.utc).isoformat())


class PassiveSettlementHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dual_root_preserves_historical_gathering_bonus_and_overflow(self):
        for level, start_hour, earned, remainder in ((1, 1, 90, 720000),
                                                     (2, 0, 96, 2880000),
                                                     (0, 0, 88, 0)):
            with self.subTest(level=level, start_hour=start_hour):
                state = default_state(T)
                state['player']['qi'] = 95
                state['legacy_feature'] = {'preserve': True}
                state['eternal'] = {'last_at': T.isoformat(), 'bank_qi': 0,
                                    'remainder': 0, 'total_earned': 0, 'scale_version': 2}
                con = MagicMock()
                con.fetchrow = AsyncMock(return_value={'state': json.dumps(state)})
                con.fetchval = AsyncMock(side_effect=lambda sql, *args:
                                         level if 'SELECT level' in sql else 'Lightning')
                con.execute = AsyncMock()
                repo = mock_repository(con)
                stock = {'marks': 100, 'holdings': {'dragon_scale': 2, 'heavenly_spark': 2},
                         'equipped': {}}
                now = FixedClock.now(timezone.utc)
                windows = [(T + timedelta(hours=start_hour), now, level)] if level else []
                with patch('storage.datetime', FixedClock), \
                        patch('sect_v022.gathering_windows', AsyncMock(return_value=windows)) as gathering, \
                        patch('sect_v022.member', AsyncMock(return_value=('Azure', 'Outer Disciple'))), \
                        patch('inventory._locked', AsyncMock(return_value=stock)), \
                        patch('inventory._save', AsyncMock()) as save:
                    await repo.choose_dual_root(GUILD, USER, 'Water')
                updates = [call for call in con.execute.await_args_list
                           if call.args[0].startswith('UPDATE characters')]
                self.assertEqual(len(updates), 1)
                saved = json.loads(updates[0].args[3])
                self.assertEqual(saved['eternal']['total_earned'], earned)
                self.assertEqual(saved['eternal']['remainder'], remainder)
                self.assertEqual(saved['player']['qi'], 100)
                self.assertEqual(saved['eternal']['bank_qi'], earned - 5)
                self.assertEqual(saved['sect_qi_level'], level)
                self.assertEqual(saved['secondary_root'], 'Water')
                self.assertEqual(saved['legacy_feature'], state['legacy_feature'])
                self.assertEqual(stock['marks'], 20)
                self.assertEqual(stock['holdings'], {'dragon_scale': 1, 'heavenly_spark': 1})
                gathering.assert_awaited_once_with(con, GUILD, USER, T, now)
                save.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
