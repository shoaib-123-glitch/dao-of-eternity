"""Developer console: configuration gates, authorization, receipts, rollback and
every registered action against the real gameplay rules. In-memory fakes only;
no bot/config import, database, Docker or Discord connection.
"""
import ast
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import discord
import beast_v022
import combat
import combat_journey_v024
import companion_bonds_v024
import dev_console_v025 as console
import dev_console_ui_v025 as console_ui
import living_dao
import living_world_v024
import living_world_v025 as world
import private_playtest as launch
import security_observer_v025 as observer
import sect_activities_v023
import shattered_heavens_v023 as cultivation
from game import default_state
from test_living_dao_ui_v024 import interaction
from test_living_world_ui_v025 import Emitter, load
from test_living_world_v025 import prepare_choice
import test_private_playtest

T = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
G, DEV, TEST, OTHER, STRANGER = 1, 7, 8, 9, 10
DATABASE, SYSID = 'dao_playtest_v023_v025_private', 7390000000000000001
PROJECT = Path(__file__).resolve().parent
FORBIDDEN = ('living_dao_actions', 'processed_actions', 'combat_actions', 'sect_hall_actions', 'DELETE', 'DROP', 'TRUNCATE')


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return T


class FakeDB:
    """Dict-backed tables, one transaction at a time, full rollback on error."""
    def __init__(self):
        self.identity = {'database_name': DATABASE, 'system_identifier': str(SYSID), 'marker': console.MARKER}
        self.characters, self.living, self.inventory, self.combat = {}, {}, {}, {}
        self.halls, self.actions, self.sql = {}, {}, []
        self.schema_created = False
        self.fail = None
        self.lock = asyncio.Lock()

    def tables(self):
        return deepcopy((self.characters, self.living, self.inventory, self.combat, self.halls, self.actions))

    def restore(self, saved):
        (self.characters, self.living, self.inventory, self.combat, self.halls, self.actions) = saved

    def repository(self):
        db = self

        class Acquire:
            async def __aenter__(self):
                return Connection(db)

            async def __aexit__(self, *exc):
                return False
        return SimpleNamespace(pool=SimpleNamespace(acquire=Acquire))


class Connection:
    def __init__(self, db):
        self.db = db

    def transaction(self):
        db = self.db

        class Transaction:
            async def __aenter__(self):
                await db.lock.acquire()
                self.saved = db.tables()

            async def __aexit__(self, kind, error, trace):
                if kind is not None:
                    db.restore(self.saved)
                db.lock.release()
                return False
        return Transaction()

    def log(self, sql):
        self.db.sql.append(sql)
        if self.db.fail and self.db.fail in sql:
            raise RuntimeError('injected failure')

    async def fetchrow(self, sql, *args):
        self.log(sql)
        if sql == console.IDENTITY_SQL:
            return dict(self.db.identity)
        if sql.startswith('SELECT holdings,marks,equipped FROM player_inventory'):
            inv = self.db.inventory[args[1]]
            return {'holdings': json.dumps(inv['holdings']), 'marks': inv['marks'], 'equipped': json.dumps(inv['equipped'])}
        if sql.startswith('SELECT developer_id, target_id, action, response FROM dev_console_actions'):
            row = self.db.actions.get(args[1])
            return None if row is None else {key: row[key] for key in ('developer_id', 'target_id', 'action', 'response')}
        raise AssertionError('Unexpected SQL: ' + sql)

    async def fetchval(self, sql, *args):
        self.log(sql)
        tables = {'SELECT battle FROM combat_sessions': self.db.combat, 'SELECT state FROM characters': self.db.characters,
                  'SELECT data FROM living_dao_players': self.db.living}
        for prefix, table in tables.items():
            if sql.startswith(prefix):
                value = table.get(args[1])
                return None if value is None else json.dumps(value)
        if sql.startswith('SELECT data FROM sect_hall_state'):
            value = self.db.halls.get(args[1])
            return None if value is None else json.dumps(value)
        raise AssertionError('Unexpected SQL: ' + sql)

    async def execute(self, sql, *args):
        self.log(sql)
        if sql == console.SCHEMA:
            self.db.schema_created = True
        elif sql.startswith('INSERT INTO player_inventory'):
            self.db.inventory.setdefault(args[1], {'holdings': {}, 'marks': 0, 'equipped': {}})
        elif sql.startswith('UPDATE player_inventory'):
            self.db.inventory[args[1]] = {'holdings': json.loads(args[2]), 'marks': args[3], 'equipped': json.loads(args[4])}
        elif sql.startswith('UPDATE combat_sessions'):
            self.db.combat[args[1]] = json.loads(args[2])
        elif sql.startswith('UPDATE characters'):
            self.db.characters[args[1]] = json.loads(args[2])
        elif sql.startswith('UPDATE living_dao_players'):
            self.db.living[args[1]] = json.loads(args[2])
        elif sql.startswith('UPDATE sect_hall_state'):
            self.db.halls[args[1]] = json.loads(args[2])
        elif sql.startswith('INSERT INTO dev_console_actions'):
            if args[1] in self.db.actions:
                raise RuntimeError('duplicate receipt')
            self.db.actions[args[1]] = dict(zip(('developer_id', 'target_id', 'action', 'params', 'response'), args[2:]),
                                            created_at=T)
        else:
            raise AssertionError('Unexpected SQL: ' + sql)

    async def fetch(self, sql, *args):
        self.log(sql)
        if sql.startswith('SELECT event_id, developer_id, target_id, action, params, created_at FROM dev_console_actions'):
            rows = [dict(row, event_id=event) for event, row in sorted(self.db.actions.items(), reverse=True)]
            return rows[:args[1]]
        raise AssertionError('Unexpected SQL: ' + sql)


def settings(developers=(DEV,), targets=(TEST,)):
    return console.ConsoleSettings(G, DATABASE, SYSID, frozenset(developers), frozenset(targets))


def living(**fields):
    return {**living_dao.initial(), **fields}


def companion(**fields):
    return living(companion='jade_fox', bond=1, beast_cultivation=beast_v022.initial_cultivation(T), **fields)


class ConsoleCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDB()
        for user in (DEV, TEST, OTHER):
            self.db.characters[user] = default_state(T)
        await console.activate(self.db.repository(), settings())

    def tearDown(self):
        console.deactivate()
        observer.disable()

    async def run_action(self, key, params=None, target=DEV, event=100, developer=DEV):
        with patch.object(console, 'datetime', Clock):
            return await console.run(self.db.repository(), G, developer, event, key, target, params)

    def assert_untouched(self, before):
        self.assertEqual(self.db.tables(), before)


class EnvironmentTests(unittest.TestCase):
    def config(self, **changes):
        return replace(launch.PrivatePlaytestConfig.from_environment(test_private_playtest.environment()), **changes)

    def env(self, **changes):
        env = test_private_playtest.environment()
        env.update(DAO_DEV_CONSOLE=console.FLAG, DAO_DEV_CONSOLE_DATABASE=DATABASE,
                   DAO_DEV_CONSOLE_SYSTEM_IDENTIFIER=str(SYSID), DAO_DEV_CONSOLE_GUILD_ID='223456789012345678',
                   DAO_DEV_CONSOLE_DEVELOPER_IDS='111111111111111111', DAO_DEV_CONSOLE_TARGET_IDS='222222222222222222')
        env.update(changes)
        return {key: value for key, value in env.items() if value is not None}

    def test_absent_configuration_leaves_console_disabled(self):
        self.assertIsNone(console.settings_from_environment(self.config(), test_private_playtest.environment()))

    def test_complete_sandbox_configuration_is_accepted(self):
        value = console.settings_from_environment(self.config(), self.env())
        self.assertEqual(value.developers, frozenset({111111111111111111}))
        self.assertEqual(value.targets, frozenset({222222222222222222}))
        self.assertTrue(value.may_target(222222222222222222))
        self.assertFalse(value.may_target(333))
        self.assertEqual(console.settings_from_environment(self.config(), self.env(DAO_DEV_CONSOLE_TARGET_IDS=None)).targets, frozenset())

    def test_every_missing_or_mismatched_setting_fails_closed(self):
        cases = {
            'flag missing': dict(DAO_DEV_CONSOLE=None), 'flag wrong': dict(DAO_DEV_CONSOLE='true'),
            'database missing': dict(DAO_DEV_CONSOLE_DATABASE=None),
            'database mismatch': dict(DAO_DEV_CONSOLE_DATABASE='dao_playtest_v023_v025_other'),
            'v024 database': dict(DAO_DEV_CONSOLE_DATABASE='dao_playtest_v023_rc1'),
            'cluster mismatch': dict(DAO_DEV_CONSOLE_SYSTEM_IDENTIFIER='1'),
            'guild mismatch': dict(DAO_DEV_CONSOLE_GUILD_ID='5'),
            'no developers': dict(DAO_DEV_CONSOLE_DEVELOPER_IDS=''), 'developer missing': dict(DAO_DEV_CONSOLE_DEVELOPER_IDS=None),
            'too many developers': dict(DAO_DEV_CONSOLE_DEVELOPER_IDS='1,2,3,4'),
            'duplicate developer': dict(DAO_DEV_CONSOLE_DEVELOPER_IDS='5,5'),
            'non-decimal developer': dict(DAO_DEV_CONSOLE_DEVELOPER_IDS='5; DROP'),
            'too many targets': dict(DAO_DEV_CONSOLE_TARGET_IDS=','.join(str(n) for n in range(1, 12))),
            'candidate flag missing': dict(DAO_V025_CANDIDATE=None),
            'read-only launch': dict(DAO_PLAYTEST_AUTHORIZED='read-only-and-discord'),
            'production mode': dict(APP_ENV='production'),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(launch.PlaytestRejected) as caught:
                console.settings_from_environment(self.config(), self.env(**change))
            self.assertEqual(caught.exception.code, 'DEV_CONSOLE_CONFIGURATION_REJECTED')

    def test_v024_target_readonly_and_non_private_configs_are_rejected(self):
        for config in (self.config(database='dao_playtest_v023_rc1'), self.config(user='dao_playtest_rc1'),
                       self.config(readonly=True), 'postgresql://ambient', None):
            with self.subTest(config=getattr(config, 'database', config)), self.assertRaises(launch.PlaytestRejected):
                console.settings_from_environment(config, self.env())

    def test_rejection_codes_are_registered_reasons(self):
        for code in ('DEV_CONSOLE_CONFIGURATION_REJECTED', 'DEV_CONSOLE_SANDBOX_IDENTITY_REJECTED'):
            self.assertEqual(launch.PlaytestRejected(code).code, code)
        self.assertEqual(launch.StartupStage.SETUP_DEV_CONSOLE.value, 'SETUP_DEV_CONSOLE')


class ActivationTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        console.deactivate()

    async def test_sandbox_marker_and_identity_are_required_before_enabling(self):
        for field, value in (('marker', ''), ('marker', 'dao-v024-live'), ('database_name', 'dao_playtest_v023_rc1'),
                             ('system_identifier', '1')):
            db = FakeDB()
            db.identity[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(launch.PlaytestRejected) as caught:
                await console.activate(db.repository(), settings())
            self.assertEqual(caught.exception.code, 'DEV_CONSOLE_SANDBOX_IDENTITY_REJECTED')
            self.assertIsNone(console.active())
            self.assertFalse(db.schema_created)

    async def test_verified_sandbox_creates_only_the_audit_table(self):
        db = FakeDB()
        await console.activate(db.repository(), settings())
        self.assertIs(console.active(), console.active())
        self.assertTrue(db.schema_created)
        self.assertEqual(db.sql, [console.IDENTITY_SQL, console.SCHEMA])

    async def test_failed_reactivation_disables_an_active_console(self):
        db = FakeDB()
        await console.activate(db.repository(), settings())
        db.identity['marker'] = ''
        with self.assertRaises(launch.PlaytestRejected):
            await console.activate(db.repository(), settings())
        self.assertIsNone(console.active())

    async def test_invalid_settings_object_is_rejected(self):
        with self.assertRaises(launch.PlaytestRejected):
            await console.activate(FakeDB().repository(), {'guild_id': G})


class AuthorizationTests(ConsoleCase):
    async def test_inactive_console_rejects_everything_without_queries(self):
        console.deactivate()
        self.db.sql.clear()
        before = self.db.tables()
        self.assertFalse(console.authorized(G, DEV))
        with self.assertRaisesRegex(console.ConsoleRejected, 'unavailable'):
            await self.run_action('set_marks', {'marks': '5'})
        self.assert_untouched(before)
        self.assertEqual(self.db.sql, [])

    async def test_only_allowlisted_developers_in_the_sandbox_guild(self):
        self.assertTrue(console.authorized(G, DEV))
        for guild, user in ((G, TEST), (G, STRANGER), (2, DEV), (None, DEV), (G, True), (G, str(DEV)), (True, DEV)):
            with self.subTest(guild=guild, user=user):
                self.assertFalse(console.authorized(guild, user))
        before = self.db.tables()
        for developer in (TEST, STRANGER):
            with self.subTest(developer=developer), self.assertRaisesRegex(console.ConsoleRejected, 'unavailable'):
                await self.run_action('set_marks', {'marks': '5'}, developer=developer)
        self.assert_untouched(before)

    async def test_targets_are_limited_to_self_and_allowlisted_test_accounts(self):
        before = self.db.tables()
        for target in (OTHER, STRANGER, 0, None, '8'):
            with self.subTest(target=target), self.assertRaisesRegex(console.ConsoleRejected, 'allowlisted'):
                await self.run_action('set_marks', {'marks': '5'}, target=target)
        self.assert_untouched(before)
        await self.run_action('set_marks', {'marks': '5'}, target=TEST)
        self.assertEqual(self.db.inventory[TEST]['marks'], 5)

    async def test_sandbox_identity_is_rechecked_inside_every_action(self):
        self.db.identity['marker'] = ''
        before = self.db.tables()
        with self.assertRaisesRegex(console.ConsoleRejected, 'unavailable'):
            await self.run_action('set_marks', {'marks': '5'})
        self.assert_untouched(before)

    async def test_invalid_actions_parameters_and_events_never_query_rows(self):
        cases = (('drop_tables', {}, 100), ('set_marks', {'marks': '501'}, 100), ('set_marks', {'marks': '-1'}, 100),
                 ('set_marks', {'marks': '5', 'sql': 'x'}, 100), ('set_marks', {}, 100), ('set_marks', {'marks': True}, 100),
                 ('set_material', {'item': 'mistwood_blade', 'quantity': '1'}, 100),
                 ('set_material', {'item': 'wolf_fang', 'quantity': '21'}, 100), ('set_marks', {'marks': '5'}, 0),
                 ('set_marks', {'marks': '5'}, True), (None, {}, 100), ('set_marks', 'marks=5', 100))
        for key, params, event in cases:
            self.db.sql.clear()
            with self.subTest(key=key, params=params, event=event), self.assertRaises(console.ConsoleRejected):
                await self.run_action(key, params, event=event)
            self.assertEqual(self.db.sql, [])

    async def test_missing_character_is_rejected_without_writes(self):
        del self.db.characters[TEST]
        before = self.db.tables()
        with self.assertRaisesRegex(console.ConsoleRejected, '/start'):
            await self.run_action('practice_ready', target=TEST)
        self.assert_untouched(before)

    async def test_denials_are_observed_without_free_text(self):
        emitter = Emitter()
        observer.enable(emitter)
        with self.assertRaises(console.ConsoleRejected):
            await self.run_action('set_marks', {'marks': '5'}, developer=STRANGER)
        await self.run_action('set_marks', {'marks': '5'})
        self.assertEqual([call[0] for call in emitter.calls], ['unauthorized_command', 'command_completed'])
        self.assertEqual(emitter.calls[0][1], dict(actor=STRANGER, guild=G, operation='command', outcome='denied', reason='not_owner'))


class CooldownWorkflowTests(ConsoleCase):
    """Normal rule -> enforced -> developer reset -> retest -> rule enforced again."""

    async def test_cultivation_practice(self):
        for user in (DEV, OTHER):
            cultivation.practice(self.db.characters[user], 'Fire', T, roll=999)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            cultivation.practice(deepcopy(self.db.characters[DEV]), 'Fire', T, roll=999)
        reply = await self.run_action('practice_ready')
        self.assertIn('ready now', reply)
        char = deepcopy(self.db.characters[DEV])
        cultivation.practice(char, 'Fire', T, roll=999)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            cultivation.practice(char, 'Fire', T, roll=999)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            cultivation.practice(deepcopy(self.db.characters[OTHER]), 'Fire', T, roll=999)
        self.assertIn('already ready', await self.run_action('practice_ready', event=101))

    async def test_cultivation_focus(self):
        cultivation.focus(self.db.characters[DEV], 'insight', T)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            cultivation.focus(deepcopy(self.db.characters[DEV]), 'gather', T)
        await self.run_action('focus_ready')
        char = deepcopy(self.db.characters[DEV])
        cultivation.focus(char, 'gather', T)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            cultivation.focus(char, 'resonance', T)

    async def test_companion_bond_activity(self):
        state, _ = companion_bonds_v024.bond_action(companion(), 'listen', T, 999)
        self.db.living[DEV] = state
        self.db.living[OTHER] = deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            companion_bonds_v024.bond_action(state, 'listen', T, 999)
        await self.run_action('companion_activity_ready')
        again, _ = companion_bonds_v024.bond_action(self.db.living[DEV], 'listen', T, 999)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            companion_bonds_v024.bond_action(again, 'listen', T, 999)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            companion_bonds_v024.bond_action(self.db.living[OTHER], 'listen', T, 999)

    async def test_camp_recovery(self):
        battle = {'enemy': 'spirit_wolf', 'status': 'victory', 'max_hp': 95,
                  'trail_v024': {'hp': 10, 'maximum': 95, 'last_at': T.isoformat()}}
        combat_journey_v024.recover(battle, T)
        self.db.combat[DEV] = battle
        with self.assertRaisesRegex(ValueError, 'ready in'):
            combat_journey_v024.recover(deepcopy(battle), T)
        await self.run_action('recovery_ready')
        again = deepcopy(self.db.combat[DEV])
        combat_journey_v024.recover(again, T)
        self.assertEqual(again['trail_v024']['hp'], 95)
        self.assertTrue(self.db.combat[DEV]['trail_v024'].get('hp') == 67)

    async def test_exploration(self):
        self.db.living[DEV] = living(last_explore=T.isoformat())
        with self.assertRaisesRegex(ValueError, 'recovering'):
            living_dao.rate_limit(self.db.living[DEV]['last_explore'], T, 300)
        await self.run_action('exploration_ready')
        living_dao.rate_limit(self.db.living[DEV]['last_explore'], T, 300)
        self.assertIsNone(self.db.living[DEV]['last_explore'])

    async def test_companion_trip_returns_now(self):
        import expeditions_v026 as expeditions
        state, _ = expeditions.send(living(companion='jade_fox', bond=3), 'thunder_ridge', T)
        self.db.living[DEV] = state
        with self.assertRaisesRegex(ValueError, 'Nothing changed'):
            expeditions.welcome(self.db.living[DEV], T)
        reply = await self.run_action('expedition_return_now')
        self.assertIn('back now', reply)
        _, _, stones, _ = expeditions.welcome(self.db.living[DEV], T)
        self.assertEqual(stones, expeditions.ROUTES['thunder_ridge'][3])

    async def test_skip_all_waits_clears_every_timer_once(self):
        self.db.living[DEV] = living(last_explore=T.isoformat(), abode_v025={'levels': {'garden': 1}, 'harvest_day': T.date().isoformat()})
        reply = await self.run_action('all_cooldowns_ready')
        self.assertIn('exploration', reply)
        self.assertIn('garden harvest', reply)
        self.assertIsNone(self.db.living[DEV]['last_explore'])
        self.assertNotIn('harvest_day', self.db.living[DEV]['abode_v025'])
        again = await self.run_action('all_cooldowns_ready', event=101)
        self.assertIn('nothing changed', again)

    async def test_progress_controls_set_state_without_paying(self):
        import abode_v025 as abode, goals_v025 as goals, omen_v026 as omen
        self.db.living[DEV] = living()
        await self.run_action('complete_goals', {'scope': 'daily'})
        _, reply, reward = goals.claim(self.db.living[DEV], 'daily', T)
        self.assertGreater(reward, 0)
        await self.run_action('set_abode_level', {'room': 'training', 'level': '4'}, event=101)
        self.assertEqual(abode.battle_hp(self.db.living[DEV]), 8)
        await self.run_action('set_omen_share', {'count': '15'}, event=102)
        state, _, paid = omen.claim(self.db.living[DEV], 20, T)
        self.db.living[DEV] = state
        self.assertEqual(paid, 5)
        await self.run_action('reset_omen_claims', event=103)
        self.assertEqual(omen._share(self.db.living[DEV], T)['claimed'], 0)
        self.assertNotIn(DEV, self.db.inventory)  # None of these pay Spirit Stones.

    async def test_featured_and_rare_daily_caps(self):
        region = living_world_v024.chapter(G, T)['region']
        state, first, _ = living_world_v024.exploration(living(), G, region, T, 0)
        state, second, _ = living_world_v024.exploration(state, G, region, T, 0)
        self.assertEqual((first, second), (2, 0))
        self.db.living[DEV] = state
        await self.run_action('world_daily_ready')
        _, third, _ = living_world_v024.exploration(self.db.living[DEV], G, region, T, 0)
        self.assertEqual(third, 2)

    async def test_teacher_lesson(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'train_zhao', T)
        state, _ = world.train(state, 'jian', T)
        self.db.living[DEV] = living(**state)
        with self.assertRaisesRegex(ValueError, 'next UTC day'):
            world.train(self.db.living[DEV], 'yue', T)
        await self.run_action('teacher_lesson_ready')
        again, _ = world.train(self.db.living[DEV], 'yue', T)
        with self.assertRaisesRegex(ValueError, 'next UTC day'):
            world.train(again, 'zhao', T, ('guard', 'strike', 'feint'))

    async def test_story_lore_day(self):
        state, _ = world.encounter(living(), 'mistwood', T, 0)
        self.db.living[DEV] = state
        await self.run_action('story_lore_ready')
        again, _ = world.encounter(self.db.living[DEV], 'mistwood', T, 0)
        self.assertEqual(again['living_world_v025']['lore_count'], 2)

    async def test_own_sect_commission_only(self):
        hall = {'commissions': {}}
        hall, _, _ = sect_activities_v023.complete_commission(hall, DEV, 'patrol', T)
        hall, _, _ = sect_activities_v023.complete_commission(hall, OTHER, 'patrol', T)
        hall['renown'], hall['projects'] = 40, {'shared': {'contributors': [OTHER]}}
        self.db.halls['Azure'] = hall
        self.db.living[DEV] = living(sect='Azure')
        with self.assertRaisesRegex(ValueError, 'recovering'):
            sect_activities_v023.complete_commission(hall, DEV, 'archives', T)
        await self.run_action('commission_ready')
        after = self.db.halls['Azure']
        self.assertEqual(after['commissions'][str(OTHER)], hall['commissions'][str(OTHER)])
        self.assertEqual((after['renown'], after['projects']), (40, {'shared': {'contributors': [OTHER]}}))
        sect_activities_v023.complete_commission(after, DEV, 'archives', T)
        with self.assertRaisesRegex(ValueError, 'recovering'):
            sect_activities_v023.complete_commission(after, OTHER, 'archives', T)

    async def test_set_sect_merit_changes_only_own_record(self):
        import sect_merit_v025 as merit
        hall = merit._store({'renown': 7}, OTHER, {'merit': 12, 'bought': {}, 'titles': []})
        self.db.halls['Azure'] = hall
        self.db.living[DEV] = living(sect='Azure')
        await self.run_action('set_sect_merit', {'merit': '90'})
        after = self.db.halls['Azure']
        self.assertEqual(merit._member(after, DEV)['merit'], 90)
        self.assertEqual(merit._member(after, OTHER)['merit'], 12)
        self.assertEqual(after['renown'], 7)

    async def test_commission_reset_requires_a_sect_hall(self):
        self.db.living[DEV] = living()
        with self.assertRaisesRegex(console.ConsoleRejected, 'sect'):
            await self.run_action('commission_ready')


class ResourceTests(ConsoleCase):
    async def test_cultivation_qi_is_bounded_by_stage_capacity_without_daily_quota(self):
        before = deepcopy(self.db.characters[DEV]['player'])
        with self.assertRaisesRegex(console.ConsoleRejected, 'at most'):
            await self.run_action('set_cultivation_qi', {'qi': '600'})
        self.assertEqual(self.db.characters[DEV]['player'], before)
        await self.run_action('set_cultivation_qi', {'qi': '10'}, event=101)
        self.assertEqual(self.db.characters[DEV]['player']['qi'], 10)
        self.assertEqual(self.db.characters[DEV]['player']['earned_today'], before['earned_today'])

    async def test_cultivation_qi_refused_during_trial(self):
        self.db.characters[DEV]['trial'] = {'step': 1}
        with self.assertRaisesRegex(console.ConsoleRejected, 'trial'):
            await self.run_action('set_cultivation_qi', {'qi': '1'})

    async def test_marks_and_materials_are_set_not_accumulated(self):
        await self.run_action('set_marks', {'marks': '40'})
        await self.run_action('set_marks', {'marks': '40'}, event=101)
        await self.run_action('set_material', {'item': 'Dragon_Scale ', 'quantity': '3'}, event=102)
        self.assertEqual(self.db.inventory[DEV], {'holdings': {'dragon_scale': 3}, 'marks': 40, 'equipped': {}})

    async def test_camp_and_battle_resources_respect_maximums(self):
        self.db.combat[DEV] = {'enemy': 'spirit_wolf', 'status': 'victory', 'max_hp': 95,
                               'trail_v024': {'hp': 95, 'maximum': 95, 'last_at': (T - timedelta(hours=1)).isoformat()}}
        with self.assertRaisesRegex(console.ConsoleRejected, 'exceed'):
            await self.run_action('set_camp_hp', {'hp': '96'})
        await self.run_action('set_camp_hp', {'hp': '5'}, event=101)
        self.assertEqual(self.db.combat[DEV]['trail_v024']['hp'], 5)
        self.assertEqual(self.db.combat[DEV]['trail_v024']['last_at'], T.isoformat())
        with self.assertRaisesRegex(console.ConsoleRejected, 'No active battle'):
            await self.run_action('set_battle_hp', {'hp': '5'}, event=102)
        self.db.combat[DEV] = combat.begin('spirit_wolf', 'Fire')
        with self.assertRaisesRegex(console.ConsoleRejected, 'finished hunt'):
            await self.run_action('set_camp_hp', {'hp': '5'}, event=103)
        with self.assertRaisesRegex(console.ConsoleRejected, 'exceed'):
            await self.run_action('set_battle_hp', {'hp': '96'}, event=104)
        await self.run_action('set_battle_qi', {'qi': '3'}, event=105)
        self.assertEqual(self.db.combat[DEV]['qi'], 3)

    async def test_beast_qi_is_bounded_and_needs_a_companion(self):
        self.db.living[DEV] = living()
        with self.assertRaisesRegex(console.ConsoleRejected, 'companion'):
            await self.run_action('set_beast_qi', {'qi': '1'})
        self.db.living[DEV] = companion()
        with self.assertRaisesRegex(console.ConsoleRejected, 'at most 80'):
            await self.run_action('set_beast_qi', {'qi': '81'}, event=101)
        await self.run_action('set_beast_qi', {'qi': '80'}, event=102)
        self.assertEqual(self.db.living[DEV]['beast_cultivation']['qi'], 80)


class CombatScenarioTests(ConsoleCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.combat[DEV] = combat.begin('spirit_wolf', 'Fire')

    async def test_heavy_attack_next_turn(self):
        for turn in range(6):
            self.db.combat[DEV]['turn'] = turn
            await self.run_action('stage_heavy_next', event=100 + turn)
            self.assertTrue(combat.enemy_intent(self.db.combat[DEV]).startswith('HEAVY'))
            self.assertGreaterEqual(self.db.combat[DEV]['turn'], turn)

    async def test_second_wind_emergency_is_chosen_by_automation(self):
        await self.run_action('stage_second_wind')
        battle = self.db.combat[DEV]
        self.assertEqual(combat.select_auto_action(battle), 'second_wind')
        after = combat.turn(deepcopy(battle), 'second_wind')
        self.assertTrue(after['second_wind_used'])
        with self.assertRaisesRegex(ValueError, 'once per battle'):
            combat.turn(after, 'second_wind')

    async def test_victory_and_defeat_follow_normal_rules(self):
        await self.run_action('stage_near_victory')
        self.assertEqual(combat.turn(deepcopy(self.db.combat[DEV]), 'strike')['status'], 'victory')
        self.db.combat[DEV] = combat.begin('spirit_wolf', 'Fire')
        await self.run_action('stage_near_defeat', event=101)
        self.assertEqual(combat.turn(deepcopy(self.db.combat[DEV]), 'focus_qi')['status'], 'defeat')
        self.assertTrue(self.db.combat[DEV]['dev_staged'])

    async def test_scenarios_require_an_active_battle(self):
        self.db.combat[DEV]['status'] = 'victory'
        before = self.db.tables()
        for key in ('stage_heavy_next', 'stage_second_wind', 'stage_near_victory', 'stage_near_defeat'):
            with self.subTest(key=key), self.assertRaisesRegex(console.ConsoleRejected, 'No active battle'):
                await self.run_action(key)
        self.assert_untouched(before)


class CompanionTests(ConsoleCase):
    async def test_trust_bond_and_stage_bounds(self):
        self.db.living[DEV] = companion()
        with self.assertRaises(console.ConsoleRejected):
            await self.run_action('set_companion_trust', {'trust': '20'})
        await self.run_action('set_companion_trust', {'trust': '19'}, event=101)
        await self.run_action('set_bond_level', {'level': '5'}, event=102)
        await self.run_action('set_beast_stage', {'stage': '4'}, event=103)
        state = self.db.living[DEV]
        self.assertEqual((state['companion_v024']['trust'], state['bond'], state['beast_cultivation']['stage']), (19, 5, 4))
        state, _ = companion_bonds_v024.bond_action(state, 'listen', T, 999)
        self.assertEqual(state['bond'], 5)

    async def test_evolution_prerequisites_then_manual_evolution(self):
        self.db.living[DEV] = companion()
        await self.run_action('prepare_evolution')
        state = deepcopy(self.db.living[DEV])
        beast_v022.evolve(state)
        self.assertEqual(state['beast_cultivation']['evolutions'], 1)
        self.db.living[DEV]['beast_cultivation']['evolutions'] = 2
        with self.assertRaisesRegex(console.ConsoleRejected, 'highest'):
            await self.run_action('prepare_evolution', event=101)

    async def test_companion_actions_need_an_adopted_companion(self):
        self.db.living[DEV] = living()
        for key, params in (('set_companion_trust', {'trust': '1'}), ('set_bond_level', {'level': '2'}),
                            ('set_beast_stage', {'stage': '2'}), ('prepare_evolution', {})):
            with self.subTest(key=key), self.assertRaisesRegex(console.ConsoleRejected, 'companion'):
                await self.run_action(key, params)


class StoryTests(ConsoleCase):
    async def test_region_proof_and_resolution_use_story_rules_without_marks(self):
        self.db.living[DEV] = living()
        await self.run_action('story_region_proof')
        self.assertTrue(world.panel(self.db.living[DEV])['ready'])
        reply = await self.run_action('story_resolve_chapter', {'option': '2'}, event=101)
        story = self.db.living[DEV]['living_world_v025']
        self.assertEqual((story['chapter'], story['choices']), (1, {'0': 'trace_script'}))
        self.assertEqual(story['npcs']['yue']['trust'], 3)
        self.assertIn('pay no Spirit Stones', reply)
        self.assertNotIn(DEV, self.db.inventory)

    async def test_resolve_through_complete_arc_then_rewind(self):
        self.db.living[DEV] = living()
        for chapter in range(3):
            await self.run_action('story_resolve_chapter', {'option': '3'}, event=100 + chapter)
        self.assertTrue(world.panel(self.db.living[DEV])['complete'])
        import arc2_v026 as arc2
        before = deepcopy(self.db.inventory)
        for chapter in range(3):  # Arc II continues through the same console buttons.
            reply = await self.run_action('story_resolve_chapter', {'option': '2'}, event=105 + chapter)
            self.assertIn('Arc II never pays', reply)
        self.assertEqual(self.db.inventory, before)
        self.assertTrue(arc2.panel(self.db.living[DEV])['complete'])
        with self.assertRaisesRegex(console.ConsoleRejected, 'complete'):
            await self.run_action('story_resolve_chapter', {'option': '1'}, event=110)
        reply = await self.run_action('story_rewind', {'chapter': '4'}, event=113)
        self.assertIn('Arc II rewound to chapter 5', reply)
        self.assertEqual(arc2.panel(self.db.living[DEV])['chapter'], 4)
        self.assertEqual(arc2._arc(self.db.living[DEV])['titles'], ['Mist Walker'])
        await self.run_action('story_region_proof', event=114)
        self.assertTrue(arc2.panel(self.db.living[DEV])['ready'])
        reply = await self.run_action('story_rewind', {'chapter': '1'}, event=111)
        self.assertNotIn('arc2_v026', self.db.living[DEV])
        self.assertIn('Chapters already paid stay paid', reply)
        story = self.db.living[DEV]['living_world_v025']
        self.assertEqual((story['chapter'], story['choices'], story['fragments']), (1, {'0': 'share_credit'}, 1))
        self.assertEqual(story['paid'], [0, 1, 2])  # Rewind never re-opens a payout.
        self.assertNotIn('technique', story)
        self.assertNotIn('resolution', story)
        self.assertGreater(story['npcs']['zhao']['trust'], 0)
        with self.assertRaisesRegex(console.ConsoleRejected, 'backwards'):
            await self.run_action('story_rewind', {'chapter': '2'}, event=112)

    async def test_npc_trust_memories_and_forms(self):
        self.db.living[DEV] = living()
        with self.assertRaisesRegex(console.ConsoleRejected, 'Stonepeak'):
            await self.run_action('story_set_form', {'style': 'flow'})
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'train_zhao', T)
        self.db.living[DEV] = living(**state)
        await self.run_action('story_set_trust', {'npc': 'jian', 'trust': '12'}, event=101)
        await self.run_action('story_clear_memories', {'npc': 'zhao'}, event=102)
        await self.run_action('story_set_form', {'style': 'flow'}, event=103)
        await self.run_action('set_form_practice', {'count': '8'}, event=104)
        story = self.db.living[DEV]['living_world_v025']
        self.assertEqual(story['npcs']['jian']['trust'], 12)
        self.assertEqual(story['npcs']['zhao']['memories'], [])
        self.assertEqual((story['technique'], story['technique_style']), ('Seeking Thread', 'flow'))
        form = world.technique(self.db.living[DEV], self.db.characters[DEV], 'Fire')
        self.assertEqual((form['style'], form['rank']), ('flow', 3))
        with self.assertRaises(console.ConsoleRejected):
            await self.run_action('story_set_trust', {'npc': 'lin', 'trust': '1'}, event=105)


class ReceiptAndRollbackTests(ConsoleCase):
    async def test_duplicate_delivery_returns_first_reply_and_applies_once(self):
        self.db.living[DEV] = living()
        first = await self.run_action('story_resolve_chapter', {'option': '1'}, event=200)
        second = await self.run_action('story_resolve_chapter', {'option': '1'}, event=200)
        self.assertEqual(first, second)
        self.assertEqual(self.db.living[DEV]['living_world_v025']['chapter'], 1)
        self.assertEqual(self.db.characters[DEV]['dev_console_v025']['actions'], 1)
        self.assertEqual(len(self.db.actions), 1)

    async def test_concurrent_duplicate_deliveries_apply_once(self):
        self.db.living[DEV] = living()
        replies = await asyncio.gather(*(self.run_action('story_resolve_chapter', {'option': '2'}, event=201) for _ in range(3)))
        self.assertEqual(len(set(replies)), 1)
        self.assertEqual(self.db.living[DEV]['living_world_v025']['chapter'], 1)
        self.assertEqual(self.db.characters[DEV]['dev_console_v025']['actions'], 1)

    async def test_another_developers_event_id_is_not_reused(self):
        console.deactivate()
        await console.activate(self.db.repository(), settings(developers=(DEV, OTHER)))
        reply = await self.run_action('set_marks', {'marks': '7'}, event=202)
        with self.assertRaisesRegex(console.ConsoleRejected, 'unavailable'):
            await self.run_action('set_marks', {'marks': '7'}, event=202, developer=OTHER)
        self.assertEqual(self.db.actions[202]['response'], reply)

    async def test_reused_event_id_with_another_action_or_target_is_refused_without_writes(self):
        await self.run_action('set_marks', {'marks': '7'}, event=205)
        before = self.db.tables()
        for key, params, target in (('set_material', {'item': 'wolf_fang', 'quantity': '3'}, DEV),
                                    ('set_marks', {'marks': '7'}, TEST)):
            with self.subTest(key=key, target=target), self.assertRaisesRegex(console.ConsoleRejected, 'again'):
                await self.run_action(key, params, target=target, event=205)
            self.assert_untouched(before)
        self.assertNotIn(TEST, self.db.inventory)

    async def test_failed_audit_write_rolls_back_every_change_and_retry_succeeds(self):
        self.db.living[DEV] = companion()
        for step in ('UPDATE characters', 'UPDATE living_dao_players', 'INSERT INTO dev_console_actions'):
            self.db.fail = step
            before = self.db.tables()
            with self.subTest(step=step), self.assertRaisesRegex(RuntimeError, 'injected'):
                await self.run_action('set_bond_level', {'level': '4'}, event=203)
            self.assert_untouched(before)
        self.db.fail = None
        await self.run_action('set_bond_level', {'level': '4'}, event=203)
        self.assertEqual(self.db.living[DEV]['bond'], 4)
        self.assertEqual(self.db.characters[DEV]['dev_console_v025']['actions'], 1)

    async def test_rule_rejection_inside_action_leaves_no_receipt_or_taint(self):
        before = self.db.tables()
        with self.assertRaises(console.ConsoleRejected):
            await self.run_action('stage_near_victory', event=204)
        self.assert_untouched(before)
        self.assertNotIn('dev_console_v025', self.db.characters[DEV])


class IsolationTests(ConsoleCase):
    async def test_actions_mark_target_only_and_never_touch_player_receipts(self):
        others = deepcopy((self.db.characters[OTHER], self.db.characters[TEST]))
        self.db.living[DEV] = companion()
        await self.run_action('set_marks', {'marks': '9'})
        await self.run_action('set_bond_level', {'level': '3'}, event=101)
        mark = self.db.characters[DEV]['dev_console_v025']
        self.assertEqual((mark['tainted'], mark['actions'], mark['last_action']), (True, 2, 'set_bond_level'))
        self.assertEqual((self.db.characters[OTHER], self.db.characters[TEST]), others)
        self.assertFalse(any(word in sql for sql in self.db.sql for word in FORBIDDEN))
        self.assertEqual(self.db.actions[101]['params'], json.dumps({'level': 3}))

    async def test_corrupted_taint_counter_and_unknown_fields_are_preserved(self):
        self.db.characters[DEV]['dev_console_v025'] = {'actions': 'x', 'note': 'keep'}
        self.db.characters[DEV]['future_field'] = {'keep': [1]}
        await self.run_action('set_marks', {'marks': '1'})
        self.assertEqual(self.db.characters[DEV]['dev_console_v025']['actions'], 1)
        self.assertEqual(self.db.characters[DEV]['dev_console_v025']['note'], 'keep')
        self.assertEqual(self.db.characters[DEV]['future_field'], {'keep': [1]})

    async def test_audit_view_is_developer_only_and_bounded(self):
        for event in range(12):
            await self.run_action('set_marks', {'marks': str(event)}, event=300 + event)
        text = await console.recent(self.db.repository(), G, DEV, limit=50)
        self.assertEqual(text.count('set_marks'), 10)
        self.assertIn('marks=11', text)
        with self.assertRaises(console.ConsoleRejected):
            await console.recent(self.db.repository(), G, STRANGER)


class RegistryTests(unittest.TestCase):
    # Standing rule: every gameplay feature ships with developer controls to skip its waits and set its state.
    FEATURE_CONTROLS = {
        'goals_v025': ('complete_goals',),
        'abode_v025': ('set_abode_level', 'abode_harvest_ready'),
        'sect_merit_v025': ('set_sect_merit',),
        'arc2_v026': ('story_region_proof', 'story_resolve_chapter', 'story_rewind'),
        'expeditions_v026': ('expedition_return_now',),
        'omen_v026': ('set_omen_share', 'reset_omen_claims'),
    }
    VIEW_ONLY = {'chronicle_v026'}  # Pure read-only displays need no controls.

    def test_every_feature_module_has_developer_controls(self):
        here = Path(__file__).parent
        features = {path.stem for path in here.glob('*_v026.py') if not path.stem.startswith('test_')} | {'goals_v025', 'abode_v025', 'sect_merit_v025'}
        for module in sorted(features - self.VIEW_ONLY):
            self.assertIn(module, self.FEATURE_CONTROLS, f'{module} needs Developer console controls (see the standing rule).')
            for key in self.FEATURE_CONTROLS[module]:
                self.assertIn(key, console.REGISTRY, key)

    def test_registry_is_bounded_and_fits_discord_menus(self):
        self.assertGreaterEqual(len(console.REGISTRY), 30)
        for category in console.CATEGORIES:
            actions = [a for a in console.REGISTRY.values() if a.category == category]
            self.assertTrue(1 <= len(actions) <= 25, category)
        for action in console.REGISTRY.values():
            self.assertLessEqual(len(action.label), 80)
            self.assertIn('character', action.rows)
            for param in action.params:
                self.assertTrue(param.choices or param.low <= param.high)

    def test_unsafe_registrations_are_rejected(self):
        apply = lambda rows, params, now, target: ''
        bad = [
            console.DevAction('Bad Key', 'x', 'story', ('character',), apply),
            console.DevAction('set_marks', 'x', 'story', ('character',), apply),
            console.DevAction('new_action', 'x', 'unknown', ('character',), apply),
            console.DevAction('new_action', 'x', 'story', ('living',), apply),
            console.DevAction('new_action', 'x', 'story', ('living', 'character'), apply),
            console.DevAction('new_action', 'x', 'story', ('character', 'living', 'hall'), apply),
            console.DevAction('new_action', 'x', 'story', ('character', 'sql'), apply),
            console.DevAction('new_action', 'x', 'story', ('character',), 'not callable'),
            console.DevAction('new_action', 'x', 'story', ('character',), apply, tuple(console.Param(f'p{n}', 'P', 0, 1) for n in range(6))),
            console.DevAction('new_action', 'x', 'story', ('character',), apply, ({'name': 'raw'},)),
            {'key': 'dict_action'},
        ]
        for action in bad:
            with self.subTest(action=getattr(action, 'key', action)), self.assertRaises(ValueError):
                console.register(action)
        self.assertNotIn('new_action', console.REGISTRY)

    def test_future_system_can_register_a_bounded_action(self):
        action = console.DevAction('future_test_ready', 'Future system ready', 'cooldown', ('character',),
                                   lambda rows, params, now, target: 'ok')
        try:
            self.assertIs(console.register(action), action)
        finally:
            console.REGISTRY.pop('future_test_ready', None)

    def test_module_has_no_shell_network_or_dynamic_sql(self):
        tree = ast.parse((PROJECT / 'dev_console_v025.py').read_text(encoding='utf-8'))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names} | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse({'os', 'subprocess', 'socket', 'eval', 'exec', 'open', '__import__'} & (names | imports))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, 'attr', None) in ('execute', 'fetch', 'fetchrow', 'fetchval'):
                self.assertNotIsInstance(node.args[0], ast.JoinedStr)


class ConsoleUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await console.activate(FakeDB().repository(), settings(targets=(TEST, OTHER)))
        self.run, self.audit = AsyncMock(), AsyncMock()

    def tearDown(self):
        console.deactivate()

    def item(self, user=DEV):
        value = interaction(user=user)
        value.guild_id = G
        value.response.send_modal = AsyncMock()
        return value

    def view(self):
        return console_ui.ConsoleView(DEV, run=self.run, audit=self.audit)

    async def test_components_fit_and_list_only_allowlisted_targets(self):
        view = self.view()
        self.assertLessEqual(len(view.children), 25)
        self.assertEqual([o.value for o in view.target_menu.options], [str(DEV), str(TEST), str(OTHER)])
        self.assertEqual({o.value for o in view.action_menu.options},
                         {k for k, a in console.REGISTRY.items() if a.category == 'cooldown'})
        self.assertLessEqual(len(console_ui.console_embed().description), 4096)

    async def test_every_component_rechecks_server_side_authorization(self):
        view = self.view()
        for user in (TEST, STRANGER):
            item = self.item(user)
            self.assertFalse(await view.interaction_check(item))
            item.response.send_message.assert_awaited_once_with(console.UNAVAILABLE, ephemeral=True)
        self.assertTrue(await view.interaction_check(self.item()))
        console.deactivate()
        item = self.item()
        self.assertFalse(await view.interaction_check(item))
        modal = console_ui.ParamsModal(console.REGISTRY['set_marks'], AsyncMock())
        self.assertFalse(await modal.interaction_check(item))

    async def test_category_action_target_then_run(self):
        view = self.view()
        item = self.item()
        with patch.object(discord.ui.Select, 'values', new_callable=PropertyMock, return_value=['combat']):
            await view.choose_category(item)
        self.assertEqual({o.value for o in view.action_menu.options},
                         {'stage_heavy_next', 'stage_second_wind', 'stage_near_victory', 'stage_near_defeat'})
        await view.run_selected(item)
        item.response.send_message.assert_awaited_with('Choose an action first.', ephemeral=True)
        with patch.object(discord.ui.Select, 'values', new_callable=PropertyMock, return_value=[str(TEST)]):
            await view.choose_target(item)
        with patch.object(discord.ui.Select, 'values', new_callable=PropertyMock, return_value=['stage_second_wind']):
            await view.choose_action(item)
        await view.run_selected(item)
        self.run.assert_awaited_once_with(item, 'stage_second_wind', TEST, {})

    async def test_parameter_actions_open_a_modal_and_submit_values(self):
        view = self.view()
        view.action = 'set_material'
        item = self.item()
        await view.run_selected(item)
        modal = item.response.send_modal.await_args.args[0]
        self.assertEqual(list(modal.inputs), ['item', 'quantity'])
        modal.inputs['item']._value = 'wolf_fang'
        modal.inputs['quantity']._value = '4'
        submitted = self.item()
        await modal.on_submit(submitted)
        self.run.assert_awaited_once_with(submitted, 'set_material', DEV, {'item': 'wolf_fang', 'quantity': '4'})
        await view.show_audit(item)
        self.audit.assert_awaited_once_with(item)


class BotWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDB()
        self.db.characters[DEV] = default_state(T)
        await console.activate(self.db.repository(), settings())
        self.observe = Mock()
        self.logging = SimpleNamespace(exception=Mock())
        self.ns = load(['console_allowed', 'dev_console_open', 'dev_console_run', 'dev_console_audit'], dict(
            discord=discord, authorized=lambda item: item.guild_id == G, observe=self.observe,
            bot=SimpleNamespace(repo=self.db.repository()), logging=self.logging))

    def tearDown(self):
        console.deactivate()

    def item(self, user=DEV):
        value = interaction(user=user)
        value.guild_id, value.id = G, 900
        return value

    async def test_non_developer_is_denied_and_observed_everywhere(self):
        for name in ('dev_console_open', 'dev_console_audit'):
            item = self.item(STRANGER)
            await self.ns[name](item)
            item.response.send_message.assert_awaited_once_with(console.UNAVAILABLE, ephemeral=True)
        item = self.item(STRANGER)
        await self.ns['dev_console_run'](item, 'set_marks', DEV, {'marks': '5'})
        item.response.defer.assert_not_awaited()
        self.assertEqual(self.observe.call_count, 3)
        self.assertNotIn(DEV, self.db.inventory)

    async def test_developer_open_run_and_audit(self):
        item = self.item()
        await self.ns['dev_console_open'](item)
        self.assertIsInstance(item.response.send_message.await_args.kwargs['view'], console_ui.ConsoleView)
        item = self.item()
        await self.ns['dev_console_run'](item, 'set_marks', DEV, {'marks': '5'})
        self.assertIn('Spirit Stones set to 5', item.followup.send.await_args.args[0])
        item = self.item()
        await self.ns['dev_console_audit'](item)
        self.assertIn('set_marks', item.followup.send.await_args.args[0])

    async def test_rule_rejections_are_shown_but_unexpected_errors_stay_generic(self):
        item = self.item()
        await self.ns['dev_console_run'](item, 'set_marks', DEV, {'marks': '999'})
        self.assertIn('0 to 500', item.followup.send.await_args.args[0])
        item = self.item()
        with patch.object(console, 'run', AsyncMock(side_effect=json.JSONDecodeError('secret-ish detail', '', 0))):
            await self.ns['dev_console_run'](item, 'set_marks', DEV, {'marks': '5'})
        text = item.followup.send.await_args.args[0]
        self.assertNotIn('secret-ish', text)
        self.assertIn('not confirmed', text)
        self.observe.assert_called_with('unexpected_error', item, 'command', 'failure', 'unexpected')
        self.logging.exception.assert_called_once()

    def test_bot_source_gates_entry_points_and_adds_no_slash_command(self):
        source = (PROJECT / 'bot.py').read_text(encoding='utf-8-sig')
        self.assertIn("if console_authorized(interaction.guild_id,interaction.user.id):\n        callbacks['Developer console']", source)
        hook = source[source.index('async def setup_hook'):source.index('GUILD_COMMAND_COPY')]
        self.assertIn('if PRIVATE_PLAYTEST:', hook)
        self.assertIn('with guard(StartupStage.SETUP_DEV_CONSOLE):', hook)
        self.assertLess(hook.index('settings_from_environment'), hook.index('activate('))
        self.assertNotIn('dev_console', ''.join(line for line in source.splitlines() if '@bot.tree.command' in line))


class LauncherTests(unittest.TestCase):
    def test_console_needs_explicit_switch_confirmation_and_clears_inherited_settings(self):
        text = (PROJECT / 'launch_private_playtest.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('[switch]$EnableDeveloperConsole', text)
        block = text[text.index('if ($EnableDeveloperConsole)'):text.index("$daoStage = 'ENVIRONMENT_SETUP'")]
        self.assertIn("if ($DiagnoseOnly) { throw", block)
        self.assertIn("-cne $daoValues.DATABASE", block)
        self.assertIn("-cne $daoValues.SYSTEM_IDENTIFIER", block)
        self.assertIn("-cne 'ENABLE SANDBOX DEVELOPER CONSOLE'", block)
        self.assertIn(console.FLAG, block)
        self.assertLess(text.index('LAUNCH PRIVATE PLAYTEST'), text.index('if ($EnableDeveloperConsole)'))
        self.assertLess(text.index('if ($EnableDeveloperConsole)'), text.index('Read-Host $daoPrompt -AsSecureString'))
        self.assertIn("else { $null }", text)
        for name in console.ENV:
            suffix = name[len('DAO_DEV_CONSOLE_'):] if name != 'DAO_DEV_CONSOLE' else ''
            self.assertIn(f"'{suffix}'", text[text.index("foreach ($daoKey in @('', 'DATABASE'"):])

    def test_v024_root_launcher_and_runtime_have_no_console(self):
        root = PROJECT.parent.parent
        for name in ('launch_private_playtest.ps1', 'bot.py', 'private_playtest.py'):
            self.assertNotIn('DEV_CONSOLE', (root / name).read_text(encoding='utf-8-sig'))
        self.assertFalse((root / 'dev_console_v025.py').exists())


if __name__ == '__main__':
    unittest.main()
