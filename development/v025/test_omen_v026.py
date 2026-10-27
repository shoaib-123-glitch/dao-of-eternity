"""Heavenly Omen: pure weekly server goal rules; no database."""
from datetime import datetime, timedelta, timezone
import unittest

import omen_v026 as omen

T = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)  # A Monday.
GUILD = 42


def region():
    return omen.current(GUILD, T)['region']


class OmenTests(unittest.TestCase):
    def test_same_omen_all_week_and_changes_between_weeks(self):
        week = {omen.current(GUILD, T + timedelta(days=d))['name'] for d in range(7)}
        self.assertEqual(len(week), 1)
        names = {omen.current(GUILD, T + timedelta(weeks=w))['name'] for w in range(12)}
        self.assertGreater(len(names), 1)
        self.assertEqual(omen.week(T), '2026-W48')

    def test_only_the_omen_region_counts_and_share_is_capped(self):
        other = next(r for r in ('mistwood', 'stonepeak', 'thunder_rift') if r != region())
        self.assertEqual(omen.record({}, GUILD, other, T), ({}, ''))
        state = {}
        for _ in range(omen.PERSONAL_CAP):
            state, line = omen.record(state, GUILD, region(), T)
        self.assertIn(f'{omen.PERSONAL_CAP}/{omen.PERSONAL_CAP}', line)
        again, line = omen.record(state, GUILD, region(), T)
        self.assertEqual(again, state)
        self.assertIn('full share', line)

    def test_share_resets_next_week(self):
        state, _ = omen.record({}, GUILD, region(), T)
        self.assertEqual(omen._share(state, T + timedelta(weeks=1))['count'], 0)

    def test_claim_pays_each_tier_once(self):
        state, _ = omen.record({}, GUILD, region(), T)
        with self.assertRaisesRegex(ValueError, 'server is at 5/20'):
            omen.claim(state, 5, T)
        state, reply, reward = omen.claim(state, 65, T)
        self.assertEqual(reward, 15)  # Tiers 1 and 2 together.
        with self.assertRaisesRegex(ValueError, 'Nothing new'):
            omen.claim(state, 70, T)
        state, reply, reward = omen.claim(state, 120, T)
        self.assertEqual(reward, 15)
        self.assertIn(omen.TITLE, reply)
        self.assertEqual(state['titles_v026'], [omen.TITLE])
        with self.assertRaisesRegex(ValueError, 'All tiers are claimed'):
            omen.claim(state, 500, T)

    def test_non_helpers_cannot_claim(self):
        with self.assertRaisesRegex(ValueError, 'at least once'):
            omen.claim({}, 500, T)

    def test_view_shows_progress_and_ready_reward(self):
        state, _ = omen.record({}, GUILD, region(), T)
        text = omen.view(state, GUILD, 25, T)
        self.assertIn('Server: 25', text)
        self.assertIn('✓ 20', text)
        self.assertIn('Reward ready', text)
        self.assertNotIn('mistwood', text.replace('Mistwood', ''))  # Region shown by display name.

    def test_malformed_share_is_tolerated(self):
        state = {'omen_v026': {'week': omen.week(T), 'count': 'x', 'claimed': 99}}
        self.assertEqual(omen._share(state, T), {'week': omen.week(T), 'count': 0, 'claimed': len(omen.TIERS)})

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            omen.current(GUILD, T.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
