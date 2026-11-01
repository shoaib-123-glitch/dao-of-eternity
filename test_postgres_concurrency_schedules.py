"""Offline schedule regressions using real handlers and transaction-lock doubles.

No database-backed case body, driver connection or SQL execution is used. The
Python lock ledger supplies synthetic blocking evidence; it does not establish
PostgreSQL compatibility, isolation, rollback or successful live execution.
"""
import ast
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg

import bonds_v022
import combat
import combat_store
import postgres_cases as cases
from storage import Repository
from test_persistence_regressions import (
    BondConnection, BondDatabase, CombatConnection, CombatDatabase, FixedClock, GUILD, USER,
)


class EvidenceConnection:
    """Mixin: ledger of Python lock owners/waiters, never a catalog/SQL emulator."""
    def __init__(self, db):
        super().__init__(db)
        self.pid = len(db.connections) + 1
        self.acquisitions = []
        db.connections.append(self)

    def get_server_pid(self):
        return self.pid

    async def lock(self, key):
        if key in self.held:
            return
        owner = self.db.owners.get(key)
        if owner is not None:
            blocker = owner
            if key == 'combat':
                prior = next(iter(self.db.waiters), None)
                if self.db.wait_graph == 'queue' and prior is not None:
                    blocker = prior
                elif self.db.wait_graph == 'external':
                    blocker = 999  # Never an allowed intermediate in the proof.
                elif self.db.wait_graph == 'cycle' and prior is not None:
                    self.db.waiters[prior] = self.pid
                    blocker = prior
            self.db.waiters[self.pid] = blocker
        try:
            await super().lock(key)
        finally:
            self.db.waiters.pop(self.pid, None)
        self.db.owners[key] = self.pid
        self.acquisitions.append(key)

    @asynccontextmanager
    async def transaction(self):
        async with super().transaction():
            try:
                yield
            finally:
                for key in self.held:
                    self.db.owners.pop(key, None)

    async def fetchval(self, sql, *args):
        if sql == cases.BLOCKING_SQL:
            self.db.probe_started.set()
            if self.db.pause_probe is not None:
                await self.db.pause_probe.wait()
            blocked = self.db.waiters.get(args[0]) == args[1]
            self.db.proofs.append((*args, blocked))
            return blocked if self.db.report_blocking else False
        return await super().fetchval(sql, *args)


class HouseholdConnection(EvidenceConnection, BondConnection):
    async def fetchval(self, sql, *args):
        if 'FROM dao_household_actions' in sql and args[1] == self.db.failure_user:
            raise self.db.failure
        return await super().fetchval(sql, *args)


class SessionConnection(EvidenceConnection, CombatConnection):
    async def fetchrow(self, sql, *args):
        if 'FROM combat_sessions' in sql:
            if self.db.missing_row:
                return None
            # The mutation tests can remove the clause or bypass its effect.
            if 'FOR UPDATE' in sql and (self.pid == 1 or not self.db.bypass_session_lock):
                await self.lock('combat')
            return {'battle': json.dumps(self.db.battle)}
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if sql.startswith('UPDATE combat_sessions'):
            if 'combat' not in self.held and 'combat' in self.db.owners:
                self.db.later_update_waits += 1
            await self.lock('combat')  # A later UPDATE must not masquerade as the SELECT proof.
        return await super().execute(sql, *args)


class EvidenceDatabase:
    def evidence_setup(self):
        self.connections, self.proofs = [], []
        self.owners, self.waiters = {}, {}
        self.active = 0
        self.probe_started = asyncio.Event()
        self.pause_probe = None
        self.report_blocking = True
        self.wait_graph = 'direct'

    @asynccontextmanager
    async def acquire(self):
        self.active += 1
        try:
            yield self.connection_type(self)
        finally:
            self.active -= 1


class HouseholdDatabase(EvidenceDatabase, BondDatabase):
    connection_type = HouseholdConnection

    def __init__(self, invitations, *, existing=True):
        BondDatabase.__init__(self, invitations)
        self.evidence_setup()
        self.failure_user, self.failure = None, None
        if existing:
            self.houses = {uid: bonds_v022.normal(None) for pair in invitations for uid in pair}


class SessionDatabase(EvidenceDatabase, CombatDatabase):
    connection_type = SessionConnection

    def __init__(self):
        CombatDatabase.__init__(self)
        self.evidence_setup()
        self.battle = combat.begin()
        self.bypass_session_lock = self.missing_row = False
        self.later_update_waits = 0


def unlocked_combat_handler():
    """Remove the lock clause from a memory-only copy; production files stay intact."""
    tree = ast.parse(inspect.getsource(combat_store.execute))
    matches = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == cases.COMBAT_LOCK_SQL:
            node.value = node.value.removesuffix(' FOR UPDATE')
            matches += 1
    if matches != 1:
        raise AssertionError('OFFLINE_MUTATION_TARGET_CHANGED')
    namespace = dict(vars(combat_store))
    exec(compile(tree, '<offline combat lock mutation>', 'exec'), namespace)
    return namespace['execute']


class ConcurrencyScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Any accidental real driver call must fail even when this module runs alone.
        for name in ('asyncpg.connect', 'asyncpg.create_pool'):
            guard = patch(name, side_effect=AssertionError('OFFLINE_ONLY'))
            guard.start()
            self.addCleanup(guard.stop)
        clock = patch('bonds_v022.datetime', FixedClock)
        clock.start()
        self.addCleanup(clock.stop)
        settlement = patch.object(Repository, 'settle_character', new_callable=AsyncMock)
        settlement.start()  # The offline lock double does not implement Hall settlement queries.
        self.addCleanup(settlement.stop)

    def assert_drained(self, db):
        self.assertEqual(db.active, 0)
        self.assertEqual(db.owners, {})
        self.assertEqual(db.waiters, {})
        self.assertTrue(all(not con.held for con in db.connections))

    async def households(self, *, dual=False, actor_first=False, first_actor=202,
                         existing=True, second_pair=None, failure=None):
        first = (101, 202) if first_actor == 202 else (202, 101)
        second = second_pair or first[::-1]
        db = HouseholdDatabase((first, second), existing=existing)
        if dual:
            db.bonds[first]['status'] = 'active'
            db.dual_requests = {pair: {'status': 'pending', 'expires_at': FixedClock.now(None) + timedelta(hours=1)}
                                for pair in (first, second)}
        if failure is not None:
            db.failure_user, db.failure = second[1], failure
        self.last_db = db
        operation = 'dual_accept' if dual else 'accept'
        with patch.object(bonds_v022, 'sorted', list if actor_first else sorted, create=True):
            results = await cases._household_pair(
                db, GUILD, first, second,
                lambda repo: bonds_v022.apply(repo, GUILD, first[1], 1001, operation, target=first[0]),
                lambda repo: bonds_v022.apply(repo, GUILD, second[1], 1002, operation, target=second[0]))
        self.assertIsInstance(results[0], str)
        self.assertIsInstance(results[1], ValueError)
        # The two production requests each completed BOTH actual locking reads.
        orders = [[key[1] for key in con.acquisitions if key[0] == 'household']
                  for con in db.connections]
        self.assertEqual([order for order in orders if order], [list(sorted(first)), list(sorted(second))])
        self.assertTrue(any(proof[2] for proof in db.proofs))
        self.assert_drained(db)
        return db

    async def test_opposing_partnership_records_both_orders(self):
        db = await self.households()
        self.assertEqual(sum(bond['status'] == 'active' for bond in db.bonds.values()), 1)
        self.assertEqual(len(db.actions), 1)

    async def test_missing_households_still_record_both_orders(self):
        await self.households(existing=False)

    async def test_shared_proposer_records_each_complete_order(self):
        await self.households(second_pair=(101, 303))

    async def test_opposing_dual_records_both_orders_and_one_boon(self):
        db = await self.households(dual=True)
        for state in db.characters.values():
            self.assertEqual(len(state['dual_boons']), 1)
        self.assertEqual(sum(r['status'] == 'accepted' for r in db.dual_requests.values()), 1)

    async def test_actor_first_partnership_first_request_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.households(actor_first=True)
        self.assert_drained(self.last_db)

    async def test_actor_first_partnership_second_request_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.households(actor_first=True, first_actor=101)
        self.assert_drained(self.last_db)

    async def test_actor_first_dual_first_request_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.households(dual=True, actor_first=True)
        self.assert_drained(self.last_db)

    async def test_actor_first_dual_second_request_is_rejected(self):
        with self.assertRaises(AssertionError):
            await self.households(dual=True, actor_first=True, first_actor=101)
        self.assert_drained(self.last_db)

    async def test_household_deadlock_is_not_an_expected_loser(self):
        with self.assertRaisesRegex(AssertionError, 'HOUSEHOLD_REQUEST_OUTCOMES'):
            await self.households(failure=asyncpg.DeadlockDetectedError('synthetic'))
        self.assert_drained(self.last_db)

    async def test_household_timeout_is_not_an_expected_loser(self):
        with self.assertRaisesRegex(AssertionError, 'HOUSEHOLD_REQUEST_OUTCOMES'):
            await self.households(failure=TimeoutError())
        self.assert_drained(self.last_db)

    async def test_household_incomplete_lock_trace_fails(self):
        async def skipped(repo):
            raise ValueError('synthetic early rejection')
        db = HouseholdDatabase(((101, 202), (202, 101)))
        with self.assertRaises(AssertionError):
            await cases._household_pair(
                db, GUILD, (101, 202), (202, 101),
                lambda repo: bonds_v022.apply(repo, GUILD, 202, 1001, 'accept', target=101), skipped)
        self.assert_drained(db)

    async def test_household_incomplete_request_after_release_fails(self):
        db = HouseholdDatabase(((101, 202), (202, 101)))
        async def incomplete(repo):
            await bonds_v022.apply(repo, GUILD, 202, 1001, 'accept', target=101)
            await asyncio.Event().wait()
        with patch.object(cases, 'SCHEDULE_TIMEOUT', 0.1), self.assertRaises(TimeoutError):
            await cases._household_pair(
                db, GUILD, (101, 202), (202, 101), incomplete,
                lambda repo: bonds_v022.apply(repo, GUILD, 101, 1002, 'accept', target=202))
        self.assert_drained(db)

    async def turns(self, db, handler=combat_store.execute):
        return await cases._combat_requests(db, GUILD, USER, *(
            lambda repo, event=event: handler(repo, GUILD, USER, event, 'turn', action='strike')
            for event in (2001, 2002)))

    async def test_combat_turns_both_prove_blocking_before_release(self):
        db = SessionDatabase()
        await self.turns(db)
        proofs = [proof for proof in db.proofs if proof[2]]
        self.assertEqual(len(proofs), 2)
        self.assertEqual(len({proof[0] for proof in proofs}), 2)
        self.assertTrue(all(proof[1] == db.connections[0].pid for proof in proofs))
        self.assertEqual((db.battle['turn'], db.battle['mastery']['strike'], db.battle['enemy_hp']), (2, 2, 38))
        self.assert_drained(db)

    async def test_combat_lock_queue_chain_reaches_independent_holder(self):
        db = SessionDatabase()
        db.wait_graph = 'queue'
        await self.turns(db)
        self.assertIn((3, 1, False), db.proofs)  # Second waiter is behind the first.
        self.assertIn((3, 2, True), db.proofs)
        self.assertIn((2, 1, True), db.proofs)
        self.assertEqual(db.battle['turn'], 2)
        self.assert_drained(db)

    async def test_unrooted_or_cyclic_wait_graph_is_rejected(self):
        for graph in ('external', 'cycle'):
            with self.subTest(graph=graph):
                db = SessionDatabase()
                db.wait_graph = graph
                with patch.object(cases, 'BLOCKING_TIMEOUT', 0.05), self.assertRaises(TimeoutError):
                    await self.turns(db)
                self.assert_drained(db)

    async def test_combat_duplicate_victory_proves_blocking_and_pays_once(self):
        db = SessionDatabase()
        replies = await cases._combat_requests(db, GUILD, USER, *(
            lambda repo: combat_store.execute(repo, GUILD, USER, 2003, 'auto') for _ in range(2)))
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(db.battle['status'], 'victory')
        self.assertEqual(db.stock['marks'], 12)  # First-clear bonus is paid only once.
        self.assertEqual(db.stock['holdings'], {'wolf_fang': 1})
        self.assertEqual(len(db.actions), 1)
        self.assertEqual(sum(proof[2] for proof in db.proofs), 2)
        self.assert_drained(db)

    async def test_combat_after_release_preserves_companion_progress(self):
        db = SessionDatabase()
        db.living['beast_cultivation']['stage'] = 2
        original = json.dumps(db.living, sort_keys=True)
        await self.turns(db)
        self.assertEqual(json.dumps(db.living, sort_keys=True), original)
        self.assert_drained(db)

    async def test_removed_production_session_lock_fails(self):
        db = SessionDatabase()
        with patch.object(cases, 'SCHEDULE_TIMEOUT', 0.1), self.assertRaises(AssertionError):
            await self.turns(db, unlocked_combat_handler())
        self.assert_drained(db)

    async def test_bypassed_session_read_cannot_use_later_update_blocking(self):
        db = SessionDatabase()
        db.bypass_session_lock = True
        with self.assertRaisesRegex(AssertionError, 'REQUEST_FINISHED_BEFORE_LOCK_PROOF'):
            await self.turns(db)
        self.assertGreater(db.later_update_waits, 0)
        self.assert_drained(db)

    async def test_missing_combat_row_rejects_before_starting_jobs(self):
        db = SessionDatabase()
        db.missing_row = True
        with self.assertRaisesRegex(AssertionError, 'COMBAT_LOCK_ROW_REQUIRED'):
            await self.turns(db)
        self.assertEqual(len(db.connections), 1)
        self.assert_drained(db)

    async def test_absent_blocking_evidence_times_out_and_drains(self):
        db = SessionDatabase()
        db.report_blocking = False
        with patch.object(cases, 'BLOCKING_TIMEOUT', 0.05), self.assertRaises(TimeoutError):
            await self.turns(db)
        self.assertTrue(db.proofs)
        self.assert_drained(db)

    async def test_request_incomplete_after_release_times_out_and_drains(self):
        db = SessionDatabase()
        async def incomplete(repo):
            await combat_store.execute(repo, GUILD, USER, 2001, 'turn', action='strike')
            await asyncio.Event().wait()
        with patch.object(cases, 'SCHEDULE_TIMEOUT', 0.1), self.assertRaises(TimeoutError):
            await cases._combat_requests(db, GUILD, USER, incomplete)
        self.assert_drained(db)

    async def test_combat_deadlock_after_release_fails_and_drains(self):
        db = SessionDatabase()
        async def failed(repo):
            await combat_store.execute(repo, GUILD, USER, 2001, 'turn', action='strike')
            raise asyncpg.DeadlockDetectedError('synthetic')
        with self.assertRaises(asyncpg.DeadlockDetectedError):
            await cases._combat_requests(db, GUILD, USER, failed)
        self.assert_drained(db)

    async def test_cancellation_during_proof_drains_all_requests(self):
        db = SessionDatabase()
        db.pause_probe = asyncio.Event()
        worker = asyncio.create_task(self.turns(db))
        await asyncio.wait_for(db.probe_started.wait(), 2)
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        self.assert_drained(db)

    async def test_before_hook_precedes_real_call_and_after_follows_it(self):
        events = []
        class Connection:
            async def fetchrow(self, sql, *args):
                events.append('actual')
                return {'actual': True}
        async def before(*args):
            events.append('before')
        async def after(*args):
            events.append('after')
        wrapped = cases.ObservedConnection(Connection(), after, before=before)
        self.assertEqual(await wrapped.fetchrow('synthetic'), {'actual': True})
        self.assertEqual(events, ['before', 'actual', 'after'])


if __name__ == '__main__':
    unittest.main()
