"""The economy simulation runs from the real tables and stays deterministic."""
import unittest

import simulate_economy_v025 as sim


class EconomySimulationTests(unittest.TestCase):
    def test_report_is_deterministic_and_covers_every_profile(self):
        first, results = sim.report()
        self.assertEqual(first, sim.report()[0])
        self.assertEqual(set(results), set(sim.PROFILES))
        for name, result in results.items():
            self.assertEqual(result['earned'], result['spent'] + result['balance'], name)
            self.assertLess(result['goal_share'], 0.5, name)  # Goals help but never dominate income.

    def test_new_players_afford_a_companion_on_day_one(self):
        for result in sim.report()[1].values():
            self.assertEqual(result['reached'].get('Jade Spirit Fox'), 1)


if __name__ == '__main__':
    unittest.main()
