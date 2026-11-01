import unittest
from datetime import datetime, timedelta, timezone
from game import default_state, apply

T = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

class QuickCultivationTests(unittest.TestCase):
    def test_initial_reward(self):
        state, reply = apply(default_state(T), 'rapid_cultivate', T, choice='study')
        self.assertEqual(state['player']['qi'], 34)
        self.assertEqual(state['dao_insight'], 1)
        self.assertIn('Qi', reply)

    def test_cooldown_enforced_and_failed_action_does_not_mutate(self):
        state, _ = apply(default_state(T), 'rapid_cultivate', T, choice='study')
        with self.assertRaisesRegex(ValueError, 'cooling down'):
            apply(state, 'rapid_cultivate', T + timedelta(seconds=5), choice='study')
        self.assertEqual(state['dao_insight'], 1)

    def test_stage_one_fills_three_actions(self):
        state = default_state(T)
        for i in range(3):
            state, _ = apply(state, 'rapid_cultivate', T + timedelta(seconds=21 * i), choice='study')
        self.assertEqual(state['player']['qi'], 100)
        self.assertEqual(state['player']['stage'], 1)
        with self.assertRaisesRegex(ValueError, 'full'):
            apply(state, 'rapid_cultivate', T+timedelta(minutes=2), choice='study')
        state, _ = apply(state, 'minor', T+timedelta(minutes=3))
        self.assertEqual(state['player']['stage'], 2)
        state, _ = apply(state, 'rapid_cultivate', T+timedelta(minutes=3), choice='study')
        self.assertEqual(state['player']['qi'], 42)

    def test_stage_nine_four_actions(self):
        state = default_state(T)
        state['player']['stage'] = 9
        for i in range(4):
            state, _ = apply(state, 'rapid_cultivate', T+timedelta(seconds=241*i), choice='circulate')
        self.assertEqual(state['player']['qi'], 600)
        self.assertEqual(state['player']['stage'], 9)

    def test_foundation_character_untouched(self):
        state = default_state(T)
        state['player']['advanced_to_foundation'] = True
        with self.assertRaisesRegex(ValueError, 'not implemented'):
            apply(state, 'rapid_cultivate', T, choice='study')
        self.assertNotIn('rapid', state)

    def test_pending_legacy_prevents_action(self):
        state = default_state(T)
        state['pending'] = T.isoformat()
        with self.assertRaisesRegex(ValueError, 'Claim'):
            apply(state, 'rapid_cultivate', T, choice='study')

    def test_invalid_choice(self):
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            apply(default_state(T), 'rapid_cultivate', T, choice='fake')

    def test_stability_and_impurity_bounded(self):
        state = default_state(T)
        state['player']['stability'] = 100
        state['player']['impurity'] = 1
        state, _ = apply(state, 'rapid_cultivate', T, choice='purify')
        self.assertEqual(state['player']['impurity'], 0)
        state, _ = apply(state, 'rapid_cultivate', T+timedelta(seconds=21), choice='circulate')
        self.assertEqual(state['player']['stability'], 100)

if __name__ == '__main__':
    unittest.main()
