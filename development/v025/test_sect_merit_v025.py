"""Economy v1 Sect Merit shop (pure rules)."""
from datetime import datetime, timedelta, timezone
import unittest

import sect_merit_v025 as merit

T = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def rich(user=7, amount=200):
    st = {'other': 1}
    book = {str(user): {'merit': amount}}
    st['merit_v025'] = book
    return st


class MeritTests(unittest.TestCase):
    def test_earning_is_per_member_and_bounded(self):
        st, gain = merit.earn({}, 7, 'commission')
        self.assertEqual(gain, merit.EARN['commission'])
        st, _ = merit.earn(st, 8, 'trial')
        self.assertEqual(merit._member(st, 7)['merit'], 4)
        self.assertEqual(merit._member(st, 8)['merit'], 2)
        self.assertEqual(merit.earn(st, 7, 'unknown')[1], 0)

    def test_buy_spends_merit_once_per_day_and_returns_materials(self):
        st, reply, holdings = merit.buy(rich(), 7, 'gathering_bundle', T)
        self.assertEqual(holdings, {'wolf_fang': 1, 'stone_core': 1})
        self.assertEqual(merit._member(st, 7)['merit'], 188)
        self.assertEqual(st['other'], 1)
        with self.assertRaisesRegex(ValueError, 'already bought'):
            merit.buy(st, 7, 'gathering_bundle', T + timedelta(hours=1))
        st, _, _ = merit.buy(st, 7, 'gathering_bundle', T + timedelta(days=1))
        self.assertEqual(merit._member(st, 7)['merit'], 176)

    def test_titles_are_cosmetic_and_permanent(self):
        st, _, holdings = merit.buy(rich(), 7, 'title_outer', T)
        self.assertEqual(holdings, {})
        self.assertEqual(merit._member(st, 7)['titles'], ['Outer Disciple'])
        with self.assertRaisesRegex(ValueError, 'kept forever'):
            merit.buy(st, 7, 'title_outer', T + timedelta(days=30))
        self.assertIn('Outer Disciple', merit.view(st, 7, T))

    def test_not_enough_merit_spends_nothing(self):
        st = rich(amount=5)
        with self.assertRaisesRegex(ValueError, 'Nothing was spent'):
            merit.buy(st, 7, 'serpent_scale', T)
        self.assertEqual(merit._member(st, 7)['merit'], 5)
        with self.assertRaises(ValueError):
            merit.buy(st, 7, 'qi_pill', T)

    def test_shop_never_sells_power(self):
        for label, _, holdings, title, _, _ in merit.SHOP.values():
            self.assertNotRegex(label.lower(), r'\bqi\b|cultivation|attack|damage')
            self.assertTrue(set(holdings) <= {'wolf_fang', 'stone_core', 'serpent_scale'})

    def test_corrupted_merit_book_is_safe(self):
        for bad in ('x', [1], {'7': 'x'}, {'7': {'merit': -5, 'bought': [], 'titles': 'x'}}, {'7': {'merit': True}}):
            st = {'merit_v025': bad}
            self.assertEqual(merit._member(st, 7)['merit'], 0)
            self.assertIn('Sect Merit', merit.view(st, 7, T))


if __name__ == '__main__':
    unittest.main()
