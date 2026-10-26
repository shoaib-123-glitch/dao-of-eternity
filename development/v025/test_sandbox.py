"""Unit tests for isolated trial gameplay without a live Discord connection."""
import unittest
from datetime import datetime, timezone
from game import apply, default_state

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def sandbox_fixture():
    state = default_state(NOW)
    state['player']['stage'] = 9
    state['player']['qi'] = 600
    return state


class TestSandboxSimulation(unittest.TestCase):
    def test_reset_only_uses_independent_state(self):
        real = default_state(NOW)
        real['player']['advanced_to_foundation'] = True
        real_snapshot = repr(real)
        trial = sandbox_fixture()
        self.assertEqual(trial['player']['stage'], 9)
        self.assertEqual(trial['player']['qi'], 600)
        self.assertEqual(repr(real), real_snapshot)

    def test_trial_start_does_not_mutate_real_state(self):
        real = default_state(NOW)
        original = repr(real)
        test, _ = apply(sandbox_fixture(), 'trial_start', NOW)
        self.assertIsNotNone(test['trial'])
        self.assertEqual(repr(real), original)

    def test_full_sandbox_trial_finishes_without_active_trial(self):
        state, _ = apply(sandbox_fixture(), 'trial_start', NOW)
        for phase_index, choice in enumerate(['endure', 'accept', 'comprehend']):
            state, msg = apply(state, 'trial_choice', NOW, phase_index=phase_index, choice=choice)
        self.assertIsNone(state['trial'])
        self.assertTrue('BREAKTHROUGH' in msg or 'Breakthrough failed' in msg)

    def test_stage_nine_cannot_be_reset_on_real_foundation(self):
        real = default_state(NOW)
        real['player']['advanced_to_foundation'] = True
        with self.assertRaises(ValueError):
            apply(real, 'dev_setstage', NOW, stage=9)


if __name__ == '__main__':
    unittest.main()
