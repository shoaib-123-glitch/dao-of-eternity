"""Cave abode: long-term Spirit Stone sink (pure rules)."""
from datetime import datetime, timedelta, timezone
import unittest

import abode_v025 as abode
import combat

T = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


class AbodeTests(unittest.TestCase):
    def test_prices_grow_quadratically_and_total_is_a_long_term_sink(self):
        self.assertEqual([abode.cost('garden', n) for n in range(3)], [30, 120, 270])
        total = sum(abode.cost(room, n) for room in abode.ROOMS for n in range(abode.MAX_LEVEL))
        self.assertGreater(total, 30000)

    def test_upgrade_checks_balance_and_returns_price(self):
        with self.assertRaisesRegex(ValueError, 'Nothing was spent'):
            abode.upgrade({}, 'garden', 29)
        state, reply, price = abode.upgrade({'keep': 1}, 'garden', 30)
        self.assertEqual((price, state['keep'], abode.level(state, 'garden')), (30, 1, 1))
        self.assertIn('Next level costs 120', reply)
        with self.assertRaises(ValueError):
            abode.upgrade(state, 'vault', 10**6)

    def test_max_level_is_enforced(self):
        state = {'abode_v025': {'levels': {'hall': abode.MAX_LEVEL}}}
        with self.assertRaisesRegex(ValueError, 'already at level'):
            abode.upgrade(state, 'hall', 10**9)

    def test_harvest_once_per_day_scales_with_garden(self):
        with self.assertRaisesRegex(ValueError, 'Build a Spirit Herb Garden'):
            abode.harvest({}, T)
        state = {'abode_v025': {'levels': {'garden': 3}}}
        state, reply, holdings = abode.harvest(state, T)
        self.assertEqual(sum(holdings.values()), 3)
        self.assertNotIn('serpent_scale', holdings)
        with self.assertRaisesRegex(ValueError, 'already harvested'):
            abode.harvest(state, T + timedelta(hours=2))
        state['abode_v025']['levels']['garden'] = 6
        _, _, holdings = abode.harvest(state, T + timedelta(days=1))
        self.assertEqual(sum(holdings.values()), 6)
        self.assertIn('serpent_scale', holdings)

    def test_training_grounds_bonus_is_small_and_reaches_battle(self):
        state = {'abode_v025': {'levels': {'training': 10}}}
        self.assertEqual(abode.battle_hp(state), 20)
        plain = combat.begin('spirit_wolf', 'Fire')
        boosted = combat.begin('spirit_wolf', 'Fire', gear={'hp': abode.battle_hp(state)})
        self.assertEqual(boosted['max_hp'] - plain['max_hp'], 20)

    def test_rank_and_view(self):
        state = {'abode_v025': {'levels': {'garden': 4, 'training': 3, 'hall': 5}}}
        self.assertEqual(abode.rank(state), (12, 'Tranquil Retreat'))
        text = abode.view(state, T)
        self.assertIn('Tranquil Retreat', text)
        self.assertIn('ready to harvest', text)

    def test_corrupted_saves_are_safe(self):
        for bad in ('x', [1], {'levels': 'x'}, {'levels': {'garden': -1, 'training': 99, 'hall': True}, 'harvest_day': 5}):
            state = {'abode_v025': bad}
            self.assertEqual(abode.battle_hp(state), 0)
            self.assertIn('Bare Cave', abode.view(state, T))
        with self.assertRaises(ValueError):
            abode.harvest({'abode_v025': {'levels': {'garden': 1}}}, datetime(2026, 1, 1))


if __name__ == '__main__':
    unittest.main()
