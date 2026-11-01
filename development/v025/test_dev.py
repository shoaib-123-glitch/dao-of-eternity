import unittest
from datetime import datetime, timezone
from game import apply, default_state

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)

class TestDevCommands(unittest.TestCase):
    def test_addqi_is_capped(self):
        state = default_state(NOW)
        state, msg = apply(state, 'dev_addqi', NOW, amount=600)
        self.assertEqual(state['player']['qi'], 100)
        self.assertIn('100', msg)

    def test_addqi_no_daily_quota_used(self):
        state, _ = apply(default_state(NOW), 'dev_addqi', NOW, amount=70)
        self.assertEqual(sum(state['player']['earned_today'].values()), 0)

    def test_stage_reset(self):
        state, _ = apply(default_state(NOW), 'dev_addqi', NOW, amount=70)
        state, _ = apply(state, 'dev_setstage', NOW, stage=9)
        self.assertEqual(state['player']['stage'], 9)
        self.assertEqual(state['player']['qi'], 0)

    def test_bad_amount(self):
        with self.assertRaises(ValueError):
            apply(default_state(NOW), 'dev_addqi', NOW, amount=-10)

    def test_bad_stage(self):
        with self.assertRaises(ValueError):
            apply(default_state(NOW), 'dev_setstage', NOW, stage=10)

    def test_trial_cannot_be_overwritten(self):
        state = default_state(NOW)
        state['trial'] = {'dummy': 1}
        with self.assertRaises(ValueError):
            apply(state, 'dev_setstage', NOW, stage=5)

if __name__ == '__main__':
    unittest.main()
