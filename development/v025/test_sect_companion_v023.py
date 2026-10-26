"""Offline progression and fake-connection regressions: no server is used."""
import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import beast_v022
import living_dao
import sect_halls
from sect_activities_v023 import (choose_doctrine, complete_commission,
                                  renown_status, DOCTRINES, COMMISSIONS)

T = datetime(2026, 10, 24, tzinfo=timezone.utc)


class SectActivityTests(unittest.TestCase):
    def test_legacy_state_defaults(self):
        self.assertEqual(renown_status(sect_halls.unpack(None)), (0, 'Novice', 30))

    def test_unpack_preserves_new_and_unknown_fields(self):
        state = {'renown': 55, 'doctrine': 'valor', 'future': {'x': [1]},
                 'levels': {'future_hall': 2}}
        self.assertEqual(sect_halls.unpack(json.dumps(state))['future'], {'x': [1]})
        self.assertEqual(sect_halls.unpack(state)['levels']['future_hall'], 2)
        self.assertEqual(sect_halls.unpack(state)['renown'], 55)

    def test_all_commissions_reward_earned_marks(self):
        for activity in COMMISSIONS:
            with self.subTest(activity=activity):
                state, marks, renown = complete_commission({}, 1, activity, T)
                self.assertEqual((marks, renown, state['renown']), (6, 5, 5))
                self.assertNotIn('spent', state)

    def test_each_doctrine_bonuses_only_matching_commission(self):
        for doctrine in DOCTRINES:
            for activity, (_, matching) in COMMISSIONS.items():
                with self.subTest(doctrine=doctrine, activity=activity):
                    _, _, renown = complete_commission({'doctrine': doctrine}, 1, activity, T)
                    self.assertEqual(renown, 6 if matching == doctrine else 5)

    def test_commission_preserves_treasury_and_input(self):
        state = {'spent': 23, 'future': {'x': 3}, 'commissions': {'2': {'future': True}}}
        before = deepcopy(state)
        result, _, _ = complete_commission(state, 1, 'patrol', T)
        self.assertEqual(state, before)
        self.assertEqual(result['spent'], 23)
        self.assertEqual(result['future'], {'x': 3})
        self.assertEqual(result['commissions']['2'], {'future': True})

    def test_cooldown_shared_across_activity_choices(self):
        state, _, _ = complete_commission({}, 1, 'patrol', T)
        with self.assertRaisesRegex(ValueError, 'recovering'):
            complete_commission(state, 1, 'gardens', T + timedelta(hours=6, microseconds=-1))
        result, _, _ = complete_commission(state, 1, 'gardens', T + timedelta(hours=6))
        self.assertEqual(result['commissions']['1']['count'], 2)

    def test_three_per_utc_day(self):
        state = {}
        for hour in (0, 6, 12):
            state, _, _ = complete_commission(state, 1, 'patrol', T + timedelta(hours=hour))
        with self.assertRaisesRegex(ValueError, 'three'):
            complete_commission(state, 1, 'patrol', T + timedelta(hours=18))

    def test_daily_reset_preserves_cross_midnight_cooldown(self):
        state, _, _ = complete_commission({}, 1, 'patrol', T + timedelta(hours=23))
        with self.assertRaisesRegex(ValueError, 'recovering'):
            complete_commission(state, 1, 'patrol', T + timedelta(days=1))
        result, _, _ = complete_commission(state, 1, 'patrol', T + timedelta(hours=29))
        self.assertEqual(result['commissions']['1']['count'], 1)
        self.assertEqual(result['commissions']['1']['day'], '2026-10-25')

    def test_cooldown_is_per_member(self):
        state, _, _ = complete_commission({}, 1, 'patrol', T)
        result, _, _ = complete_commission(state, 2, 'patrol', T)
        self.assertEqual(result['renown'], 10)

    def test_utc_day_uses_instant_not_local_date(self):
        local = T.astimezone(timezone(timedelta(hours=-5)))
        state, _, _ = complete_commission({}, 1, 'patrol', local)
        self.assertEqual(state['commissions']['1']['day'], '2026-10-24')

    def test_legacy_naive_cursor_is_utc(self):
        state = {'commissions': {'1': {'last_at': T.replace(tzinfo=None).isoformat()}}}
        with self.assertRaisesRegex(ValueError, 'recovering'):
            complete_commission(state, 1, 'patrol', T)

    def test_invalid_commission_rejected(self):
        with self.assertRaises(ValueError):
            complete_commission({}, 1, 'paid_skip', T)

    def test_renown_tiers(self):
        for value, expected in ((29, ('Novice', 30)), (30, ('Rising', 90)),
                                (90, ('Established', 180)), (180, ('Renowned', 360)),
                                (999, ('Heavenly', None))):
            self.assertEqual(renown_status({'renown': value})[1:], expected)

    def test_first_doctrine_is_free_and_preserves_fields(self):
        state = {'spent': 30, 'future': [1]}
        result = choose_doctrine(state, 'wisdom', T)
        self.assertEqual(result['doctrine'], 'wisdom')
        self.assertEqual(result['spent'], 30)
        self.assertNotIn('doctrine', state)

    def test_doctrine_change_at_24_hours(self):
        state = choose_doctrine({}, 'wisdom', T)
        with self.assertRaisesRegex(ValueError, '24 hours'):
            choose_doctrine(state, 'valor', T + timedelta(hours=24, microseconds=-1))
        self.assertEqual(choose_doctrine(state, 'valor', T + timedelta(hours=24))['doctrine'], 'valor')

    def test_invalid_or_unchanged_doctrine_rejected(self):
        with self.assertRaises(ValueError):
            choose_doctrine({}, 'premium', T)
        with self.assertRaisesRegex(ValueError, 'already'):
            choose_doctrine({'doctrine': 'wisdom'}, 'wisdom', T)


class BeastPrecisionTests(unittest.TestCase):
    def state(self):
        return beast_v022.settle({'companion': 'jade_fox', 'bond': 1}, T)

    def test_fractional_visits_match_single_settlement(self):
        state = self.state()
        for index in range(1, 1001):
            state = beast_v022.settle(state, T + timedelta(seconds=index * 0.6))
        once = beast_v022.settle(self.state(), T + timedelta(seconds=600))
        self.assertEqual(state['beast_cultivation'], once['beast_cultivation'])
        self.assertEqual(state['beast_cultivation']['earned'], 3)

    def test_cursor_retains_fractional_elapsed_time(self):
        state = beast_v022.settle(self.state(), T + timedelta(seconds=1.75))
        self.assertEqual(state['beast_cultivation']['last_at'], (T + timedelta(seconds=1)).isoformat())

    def test_settle_does_not_mutate_input_or_unknown_fields(self):
        state = self.state()
        state['beast_cultivation']['future'] = 'preserved'
        before = deepcopy(state)
        after = beast_v022.settle(state, T + timedelta(hours=1))
        self.assertEqual(state, before)
        self.assertEqual(after['beast_cultivation']['future'], 'preserved')

    def test_legacy_naive_cursor(self):
        state = self.state()
        state['beast_cultivation']['last_at'] = T.replace(tzinfo=None).isoformat()
        after = beast_v022.settle(state, T + timedelta(hours=1))
        self.assertEqual(after['beast_cultivation']['earned'], 18)

    def test_legacy_activation_has_no_backpay(self):
        state = beast_v022.settle({'companion': 'jade_fox', 'bond': 1}, T + timedelta(days=90))
        self.assertEqual(state['beast_cultivation']['earned'], 0)

    def test_backward_clock_does_not_move_cursor(self):
        state = self.state()
        after = beast_v022.settle(state, T - timedelta(hours=1))
        self.assertEqual(after['beast_cultivation'], state['beast_cultivation'])

    def test_no_automatic_advancement_or_evolution(self):
        state = beast_v022.settle(self.state(), T + timedelta(days=30))
        self.assertEqual(state['beast_cultivation']['stage'], 1)
        self.assertEqual(state['beast_cultivation']['evolutions'], 0)
        self.assertEqual(state['beast_cultivation']['qi'], 80)
        self.assertGreater(state['beast_cultivation']['bank'], 0)


class _Context:
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *_): return False


class _Connection:
    """Only interprets the operations used here; never executes SQL."""
    def __init__(self, living=None, hall=None, founder=1, replay_after_lock=None):
        self.living = deepcopy(living or {'sect': 'Azure'})
        self.hall = deepcopy(hall or {})
        self.founder = founder
        self.marks = 100
        self.calls = []
        self.replies = {}
        self.replay_checks = 0
        self.replay_after_lock = replay_after_lock

    def transaction(self): return _Context(self)

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if sql.startswith('SELECT reply FROM sect_hall_actions'):
            self.replay_checks += 1
            if self.replay_after_lock and self.replay_checks >= 2:
                return self.replay_after_lock
            return self.replies.get(args[2])
        if sql.startswith('SELECT data FROM living_dao_players'): return self.living
        if sql.startswith('SELECT 1 FROM characters'): return 1
        if sql.startswith('SELECT response FROM living_dao_actions'): return self.replies.get(args[2])
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if 'FROM player_inventory' in sql:
            return {'holdings': {}, 'marks': self.marks, 'equipped': {}}
        if 'FROM living_dao_sects' in sql: return {'founder_id': self.founder, 'contribution': 70}
        if 'FROM sect_hall_state' in sql: return {'data': self.hall}
        if 'FROM living_dao_players' in sql: return {'data': self.living}
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if sql.startswith('UPDATE sect_hall_state'): self.hall = json.loads(args[2])
        elif sql.startswith('UPDATE living_dao_players'): self.living = json.loads(args[2])
        elif sql.startswith('UPDATE player_inventory'): self.marks = args[3]
        elif sql.startswith(('INSERT INTO sect_hall_actions', 'INSERT INTO living_dao_actions')):
            self.replies[args[2]] = args[3]
        return 'OK'


def _repo(con):
    return SimpleNamespace(pool=SimpleNamespace(acquire=lambda: _Context(con)))


class SectPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_commission_updates_existing_json_and_inventory_once(self):
        con = _Connection(hall={'future': {'x': 1}, 'doctrine': 'valor'})
        reply = await sect_halls.act(_repo(con), 10, 1, 99, 'commission', 'patrol')
        self.assertIn('+6 combat marks', reply)
        self.assertEqual((con.marks, con.hall['renown']), (106, 6))
        self.assertEqual(con.hall['future'], {'x': 1})
        count = len(con.calls)
        self.assertEqual(await sect_halls.act(_repo(con), 10, 1, 99, 'commission', 'patrol'), reply)
        self.assertEqual(len(con.calls), count + 1)
        self.assertEqual(con.marks, 106)

    async def test_reward_lock_order_matches_contribution(self):
        for operation, argument in (('commission', 'patrol'), ('trial', None)):
            with self.subTest(operation=operation):
                con = _Connection()
                await sect_halls.act(_repo(con), 10, 1, 99, operation, argument)
                statements = [call[0] for call in con.calls]
                inventory = next(i for i, sql in enumerate(statements) if 'FROM player_inventory' in sql)
                hall_insert = next(i for i, sql in enumerate(statements) if sql.startswith('INSERT INTO sect_hall_state'))
                sect = next(i for i, sql in enumerate(statements) if 'FROM living_dao_sects' in sql)
                self.assertLess(inventory, hall_insert)
                self.assertLess(inventory, sect)
                self.assertIn('FOR UPDATE', statements[sect])
                self.assertTrue(any('FROM sect_hall_state' in sql and 'FOR UPDATE' in sql for sql in statements))

    async def test_replay_rechecked_after_locks_without_second_award(self):
        con = _Connection(replay_after_lock='already completed')
        reply = await sect_halls.act(_repo(con), 10, 1, 99, 'commission', 'patrol')
        self.assertEqual(reply, 'already completed')
        self.assertEqual(con.marks, 100)
        self.assertEqual(con.hall, {})
        self.assertEqual(con.replay_checks, 2)

    async def test_doctrine_founder_only(self):
        con = _Connection(founder=2)
        with self.assertRaisesRegex(ValueError, 'founder'):
            await sect_halls.act(_repo(con), 10, 1, 99, 'doctrine', 'valor')
        self.assertEqual(con.hall, {})
        self.assertFalse(any('player_inventory' in call[0] for call in con.calls))

    async def test_founder_doctrine_preserves_existing_hall_progression(self):
        con = _Connection(hall={'spent': 40, 'trials': 7, 'levels': {'martial': 2}})
        await sect_halls.act(_repo(con), 10, 1, 99, 'doctrine', 'wisdom')
        self.assertEqual(con.hall['doctrine'], 'wisdom')
        self.assertEqual((con.hall['spent'], con.hall['trials'], con.hall['levels']['martial']), (40, 7, 2))

    async def test_trial_still_rewards_ten(self):
        con = _Connection()
        await sect_halls.act(_repo(con), 10, 1, 99, 'trial')
        self.assertEqual((con.marks, con.hall['trials']), (110, 1))


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None): return T if tz else T.replace(tzinfo=None)


class LivingCompanionTests(unittest.IsolatedAsyncioTestCase):
    async def test_adoption_activates_at_adoption_instant(self):
        con = _Connection(living={'companion': None})
        with patch.object(living_dao, 'datetime', _FrozenDateTime):
            await living_dao.action(_repo(con), 10, 1, 99, 'adopt', 'jade_fox')
        self.assertEqual(con.marks, 84)
        self.assertEqual(con.living['beast_cultivation']['last_at'], T.isoformat())
        later = beast_v022.settle(con.living, T + timedelta(hours=1))
        self.assertEqual(later['beast_cultivation']['earned'], 18)

    async def test_exploration_settles_companion_without_extra_row_lock(self):
        con = _Connection(living={'companion': 'jade_fox', 'bond': 1,
                                 'beast_cultivation': beast_v022.initial_cultivation(T - timedelta(hours=1))})
        with patch.object(living_dao, 'datetime', _FrozenDateTime):
            await living_dao.action(_repo(con), 10, 1, 99, 'explore', 'mistwood')
        self.assertEqual(con.living['beast_cultivation']['earned'], 18)
        self.assertEqual(sum('FOR UPDATE' in call[0] and 'living_dao_players' in call[0] for call in con.calls), 1)

    async def test_replayed_living_action_does_not_reinitialize_beast(self):
        con = _Connection(living={'companion': 'jade_fox', 'bond': 1})
        con.replies[99] = 'existing response'
        self.assertEqual(await living_dao.action(_repo(con), 10, 1, 99, 'adopt', 'jade_fox'), 'existing response')
        self.assertNotIn('beast_cultivation', con.living)
        self.assertEqual(con.marks, 100)


if __name__ == '__main__':
    unittest.main()
