"""Companion expeditions: pure rules over synthetic state; no database."""
from datetime import datetime, timedelta, timezone
import unittest

import expeditions_v026 as ex

T = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def pet(bond=3, companion='storm_hawk'):
    return {'companion': companion, 'bond': bond}


class ExpeditionTests(unittest.TestCase):
    def test_send_then_welcome_pays_once(self):
        state, reply = ex.send(pet(), 'thunder_ridge', T)
        self.assertIn('Thunder Ridge', reply)
        self.assertIn(f'<t:{int((T + timedelta(hours=8)).timestamp())}:R>', reply)
        with self.assertRaisesRegex(ValueError, 'already on Thunder Ridge'):
            ex.send(state, 'mistwood_trail', T)
        with self.assertRaisesRegex(ValueError, 'Nothing changed'):
            ex.welcome(state, T + timedelta(hours=7, minutes=59))
        state, reply, stones, holdings = ex.welcome(state, T + timedelta(hours=8))
        self.assertEqual(stones, 10)
        self.assertEqual(sum(holdings.values()), 3)
        self.assertIn('Expeditions completed: 1', reply)
        with self.assertRaisesRegex(ValueError, 'not on an expedition'):
            ex.welcome(state, T + timedelta(days=1))

    def test_late_return_loses_nothing(self):
        state, _ = ex.send(pet(), 'mistwood_trail', T)
        _, _, stones, holdings = ex.welcome(state, T + timedelta(days=30))
        self.assertEqual((stones, holdings), (4, {'wolf_fang': 1}))

    def test_bond_gates_longer_routes(self):
        with self.assertRaisesRegex(ValueError, 'Bond Level 3'):
            ex.send(pet(bond=2), 'thunder_ridge', T)
        ex.send(pet(bond=2), 'stonepeak_caves', T)
        with self.assertRaisesRegex(ValueError, 'Adopt'):
            ex.send({'companion': None, 'bond': 0}, 'mistwood_trail', T)
        with self.assertRaisesRegex(ValueError, 'Choose'):
            ex.send(pet(), 'moon', T)

    def test_shorter_routes_pay_more_per_hour(self):
        rates = [stones / hours for _, hours, _, stones, _, _ in ex.ROUTES.values()]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_tales_and_materials_rotate(self):
        state, seen = pet(), set()
        for day in range(3):
            state, _ = ex.send(state, 'stonepeak_caves', T + timedelta(days=day))
            state, reply, _, _ = ex.welcome(state, T + timedelta(days=day, hours=4))
            seen.add(reply.split(' on ')[0])
        self.assertEqual(len(seen), 3)

    def test_send_does_not_mutate_input_and_view_shows_status(self):
        original = pet()
        state, _ = ex.send(original, 'mistwood_trail', T)
        self.assertNotIn('expedition_v026', original)
        self.assertIn('returns <t:', ex.view(state, T))
        self.assertIn('back now', ex.view(state, T + timedelta(hours=2)))
        self.assertIn('needs Bond Level 3', ex.view(pet(bond=1), T))
        self.assertEqual(ex.view({'companion': None}, T), '')

    def test_malformed_state_is_tolerated(self):
        state = {**pet(), 'expedition_v026': {'route': 'mistwood_trail', 'sent_at': 'nope', 'count': -4}}
        self.assertIn('Resting', ex.view(state, T))
        ex.send(state, 'mistwood_trail', T)

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            ex.send(pet(), 'mistwood_trail', T.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
