"""Economy v1 daily and weekly goals (pure rules)."""
from datetime import datetime, timedelta, timezone
import unittest

import goals_v025 as goals

T = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)  # A Monday.


def finish_today(state, now=T):
    for kind, need, _ in goals.daily_goals(now):
        for _ in range(need):
            state = goals.record(state, kind, now)
    return state


class GoalTests(unittest.TestCase):
    def test_daily_goals_are_one_of_each_kind_and_vary_by_day(self):
        days = {goals.daily_goals(T + timedelta(days=n)) for n in range(4)}
        self.assertGreater(len(days), 1)
        for n in range(7):
            kinds = [kind for kind, _, _ in goals.daily_goals(T + timedelta(days=n))]
            self.assertEqual(kinds, ['explore', 'hunt_win', 'practice'])
        self.assertEqual(goals.daily_goals(T), goals.daily_goals(T.replace(hour=23, minute=59)))

    def test_claim_pays_each_goal_once_plus_bonus(self):
        state = finish_today({'keep': 1})
        state, reply, reward = goals.claim(state, 'daily', T)
        expected = sum(r for _, _, r in goals.daily_goals(T)) + goals.DAILY_BONUS
        self.assertEqual(reward, expected)
        self.assertIn('All three daily goals', reply)
        self.assertEqual(state['keep'], 1)
        with self.assertRaisesRegex(ValueError, 'No finished goals'):
            goals.claim(state, 'daily', T)
        self.assertEqual(state['goals_v025']['weekly']['days'], 1)

    def test_partial_claim_then_rest_later_same_day(self):
        kind, need, gain = goals.daily_goals(T)[2]
        state = goals.record({}, kind, T)
        state, _, reward = goals.claim(state, 'daily', T)
        self.assertEqual(reward, gain)
        state = finish_today(state)
        state, reply, reward = goals.claim(state, 'daily', T)
        self.assertEqual(reward, sum(r for _, _, r in goals.daily_goals(T)[:2]) + goals.DAILY_BONUS)

    def test_new_day_resets_daily_but_keeps_week(self):
        state = finish_today({})
        state, _, _ = goals.claim(state, 'daily', T)
        tomorrow = T + timedelta(days=1)
        self.assertIn('0/', goals.view(state, tomorrow))
        state = goals.record(state, 'explore', tomorrow)
        self.assertEqual(state['goals_v025']['daily']['explore'], 1)
        self.assertGreater(state['goals_v025']['weekly']['explore'], 1)
        self.assertEqual(state['goals_v025']['weekly']['days'], 1)
        next_week = T + timedelta(days=7)
        self.assertEqual(goals.record(state, 'explore', next_week)['goals_v025']['weekly']['explore'], 1)

    def test_weekly_goals_pay_once(self):
        state = {}
        for day in range(4):
            now = T + timedelta(days=day)
            state = finish_today(state, now)
            state, _, _ = goals.claim(state, 'daily', now)
        for _ in range(12):
            state = goals.record(state, 'explore', T + timedelta(days=4))
        for _ in range(8):
            state = goals.record(state, 'hunt_win', T + timedelta(days=4))
        state, reply, reward = goals.claim(state, 'weekly', T + timedelta(days=4))
        self.assertEqual(reward, sum(r for _, _, r in goals.WEEKLY))
        with self.assertRaisesRegex(ValueError, 'No finished goals'):
            goals.claim(state, 'weekly', T + timedelta(days=5))

    def test_corrupted_saves_and_bad_inputs_are_safe(self):
        for bad in ('x', [1], {'day': 5, 'daily': 'x', 'claimed_daily': {'a': 1}},
                    {'day': T.date().isoformat(), 'daily': {'explore': -4, 'hunt_win': 10**9, 'practice': True},
                     'claimed_daily': ['explore', 7, None]}):
            state = {'goals_v025': bad}
            self.assertIn('Daily goals', goals.view(state, T))
            self.assertEqual(goals.record(state, 'nonsense', T).get('goals_v025'), bad)
        with self.assertRaises(ValueError):
            goals.claim({}, 'monthly', T)
        with self.assertRaises(ValueError):
            goals.view({}, datetime(2026, 10, 26))

    def test_view_never_shows_raw_kind_ids(self):
        text = goals.view({}, T)
        self.assertNotIn('hunt_win', text)
        self.assertIn('Spirit Stones', text)


if __name__ == '__main__':
    unittest.main()
