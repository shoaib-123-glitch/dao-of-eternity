"""Deterministic pure rules regressions; no Discord, database or environment."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from fractions import Fraction

from eternal import DENOM, rate_now, settle
from game import apply, default_state, pack_player, unpack_player
from heavens import ALL_ROOTS, RARE_CORRESPONDENCE, add_boon, boost_summary, iso
from shattered_heavens_v023 import (
    bonus_milli, cultivation_summary, focus, journey, practice, root_utility,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def character():
    state = default_state(NOW)
    settle(state, NOW)
    return state


class ShatteredHeavensCultivationTests(unittest.TestCase):
    def test_existing_character_rate_is_unchanged(self):
        state = character()
        self.assertEqual(rate_now(state, NOW, root='Fire'), (40, 1100, 44))
        self.assertEqual(bonus_milli(state, NOW, 'Fire'), 1000)
        self.assertNotIn('cultivation_v023', state)

    def test_permanent_cultivation_pays_beyond_seven_days(self):
        state = character()
        self.assertEqual(settle(state, NOW + timedelta(days=365)), 365 * 24 * 40)

    def test_focus_gather_adds_ten_percent(self):
        state = character()
        focus(state, 'gather', NOW)
        self.assertEqual(rate_now(state, NOW, root='Fire'), (40, 1210, 48.4))

    def test_focus_has_six_hour_commitment(self):
        state = character()
        focus(state, 'gather', NOW)
        with self.assertRaisesRegex(ValueError, 'ready'):
            focus(state, 'insight', NOW + timedelta(hours=5))
        focus(state, 'insight', NOW + timedelta(hours=6))
        self.assertEqual(bonus_milli(state, NOW + timedelta(hours=6), 'Fire'), 1000)

    def test_same_focus_is_not_reselected(self):
        state = character()
        focus(state, 'gather', NOW)
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'already active'):
            focus(state, 'gather', NOW + timedelta(hours=6))
        self.assertEqual(state, before)

    def test_invalid_focus_does_not_create_new_state(self):
        state = character()
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            focus(state, 'premium', NOW)
        self.assertEqual(state, before)

    def test_focus_and_practice_apply_copy_the_record(self):
        state = character()
        before = copy.deepcopy(state)
        changed, _ = apply(state, 'cultivation_focus', NOW, root='Fire', focus='gather')
        practiced, _ = apply(changed, 'cultivation_practice', NOW, root='Fire', roll=999)
        self.assertEqual(state, before)
        self.assertNotIn('practices', changed['cultivation_v023'])
        self.assertEqual(practiced['cultivation_v023']['practices'], 1)

    def test_practice_is_free_with_empty_resources(self):
        state = character()
        before = unpack_player(state['player'])
        practice(state, 'Earth', NOW, roll=999)
        after = unpack_player(state['player'])
        self.assertEqual(after.training_seals, before.training_seals)
        self.assertEqual(after.foundation_essences, before.foundation_essences)
        self.assertEqual(state['cultivation_v023']['mastery']['Earth'], 4)

    def test_practice_rejects_early_replay_without_reward(self):
        state = character()
        practice(state, 'Metal', NOW, roll=999)
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'ready'):
            practice(state, 'Metal', NOW + timedelta(hours=5), roll=0)
        self.assertEqual(state, before)

    def test_practice_is_ready_at_exact_six_hours(self):
        state = character()
        practice(state, 'Earth', NOW, roll=999)
        practice(state, 'Earth', NOW + timedelta(hours=6), roll=999)
        self.assertEqual(state['cultivation_v023']['mastery']['Earth'], 8)

    def test_insight_grants_two_extra_mastery(self):
        state = character()
        focus(state, 'insight', NOW)
        practice(state, 'Earth', NOW, roll=999)
        self.assertEqual(state['cultivation_v023']['mastery']['Earth'], 6)
        self.assertEqual(bonus_milli(state, NOW, 'Earth'), 1000)

    def test_wind_root_mastery_utility_applies(self):
        state = character()
        practice(state, 'Wind', NOW, roll=999)
        self.assertEqual(state['cultivation_v023']['mastery']['Wind'], 5)

    def test_every_awakened_root_can_progress(self):
        for root in ALL_ROOTS:
            with self.subTest(root=root):
                state = character()
                practice(state, root, NOW, roll=999)
                self.assertGreater(state['cultivation_v023']['mastery'][root], 0)
                self.assertTrue(root_utility(root)[0])

    def test_rare_roots_have_exact_common_equivalent_rewards(self):
        for rare, common in RARE_CORRESPONDENCE.items():
            with self.subTest(root=rare):
                a, b = character(), character()
                for s in (a, b):
                    p = unpack_player(s['player'])
                    p.stability = 60
                    p.meridian_integrity = 60
                    p.impurity = 10
                    s['player'] = pack_player(p)
                practice(a, rare, NOW, roll=999)
                practice(b, common, NOW, roll=999)
                self.assertEqual(a['player'], b['player'])
                self.assertEqual(a['eternal'], b['eternal'])
                self.assertEqual(a['cultivation_v023']['mastery'][rare], b['cultivation_v023']['mastery'][common])
                self.assertEqual(root_utility(rare)[1], root_utility(common)[1])

    def test_root_utilities_are_distinct_and_bounded(self):
        effects = set()
        for root in ('Fire', 'Water', 'Earth', 'Wind', 'Wood', 'Metal', 'Lightning', 'Ice'):
            state = character()
            p = unpack_player(state['player'])
            p.stability = 60
            p.meridian_integrity = 60
            p.impurity = 10
            state['player'] = pack_player(p)
            practice(state, root, NOW, roll=999)
            p = unpack_player(state['player'])
            effects.add((p.qi, p.stability, p.meridian_integrity, p.impurity,
                         p.training_seals, state['cultivation_v023']['mastery'][root]))
            self.assertLessEqual(p.qi, p.capacity)
            self.assertGreaterEqual(p.impurity, 0)
        self.assertEqual(len(effects), 8)

    def test_recovery_never_exceeds_full_stats(self):
        for root in ('Water', 'Earth', 'Wood'):
            state = character()
            practice(state, root, NOW, roll=999)
            p = unpack_player(state['player'])
            self.assertLessEqual(p.stability, 100)
            self.assertLessEqual(p.meridian_integrity, 100)

    def test_invalid_roots_and_rolls_do_not_mutate(self):
        for root, roll in ((None, 0), ('Paid', 0), ('Fire', -1), ('Fire', 1000),
                           ('Fire', True), ('Fire', 0.5)):
            state = character()
            before = copy.deepcopy(state)
            with self.assertRaises(ValueError):
                practice(state, root, NOW, roll=roll)
            self.assertEqual(state, before)

    def test_rare_encounter_roll_boundaries(self):
        for roll, multiplier in ((0, 5000), (2, 5000), (3, 3000), (17, 3000),
                                 (18, 2000), (79, 2000), (80, None), (999, None)):
            state = character()
            reply = practice(state, 'Earth', NOW, roll=roll)
            if multiplier:
                self.assertEqual(state['personal_boons'][0]['mult'], multiplier)
                self.assertIn('Rare encounter:', reply)
            else:
                self.assertFalse(state.get('personal_boons'))

    def test_resonance_improves_chance_without_greater_boosts(self):
        for roll, has_boon in ((80, True), (129, True), (130, False)):
            state = character()
            focus(state, 'resonance', NOW)
            practice(state, 'Earth', NOW, roll=roll)
            self.assertEqual(bool(state.get('personal_boons')), has_boon)
            if has_boon:
                self.assertEqual(state['personal_boons'][0]['mult'], 2000)

    def test_existing_personal_boon_is_preserved_exactly(self):
        state = character()
        add_boon(state, NOW, 'legendary')
        before = copy.deepcopy(state['personal_boons'])
        practice(state, 'Earth', NOW, roll=18)
        self.assertEqual(state['personal_boons'], before)
        self.assertNotIn('discoveries', state['cultivation_v023'])

    def test_practice_boost_counts_offline_and_then_expires(self):
        state = character()
        practice(state, 'Earth', NOW, roll=18)
        self.assertEqual(settle(state, NOW + timedelta(hours=3), root='Earth'), 220)
        self.assertEqual(state['personal_boons'], [])
        self.assertEqual(rate_now(state, NOW + timedelta(hours=3), root='Earth')[1], 1100)

    def test_mastery_caps_at_one_hundred_and_bonus_at_five_percent(self):
        state = character()
        for i in range(30):
            apply_time = NOW + timedelta(hours=6 * i)
            settle(state, apply_time, root='Earth')
            practice(state, 'Earth', apply_time, roll=999)
        self.assertEqual(state['cultivation_v023']['mastery']['Earth'], 100)
        self.assertEqual(bonus_milli(state, apply_time, 'Earth'), 1050)

    def test_mastery_is_retained_separately_for_each_root(self):
        state = character()
        practice(state, 'Earth', NOW, roll=999)
        practice(state, 'Fire', NOW + timedelta(hours=6), roll=999)
        self.assertEqual(state['cultivation_v023']['mastery'], {'Earth': 4, 'Fire': 4})

    def test_focus_does_not_backpay_the_previous_day(self):
        state = character()
        changed, _ = apply(state, 'cultivation_focus', NOW + timedelta(days=1),
                           focus='gather', root='Fire')
        self.assertEqual(changed['eternal']['total_earned'], 24 * 44)
        gained = settle(changed, NOW + timedelta(days=1, hours=1), root='Fire')
        self.assertEqual(gained, 48)

    def test_mastery_unlock_does_not_retroactively_boost_offline_time(self):
        state = character()
        for i in range(4):
            state, _ = apply(state, 'cultivation_practice', NOW + timedelta(hours=6*i),
                             root='Earth', roll=999)
        state, _ = apply(state, 'cultivation_practice', NOW + timedelta(days=1), root='Earth', roll=999)
        self.assertEqual(state['cultivation_v023']['mastery']['Earth'], 20)
        self.assertEqual(state['eternal']['total_earned'], 24 * 44)
        self.assertEqual(rate_now(state, NOW + timedelta(days=1), root='Earth')[1], 1111)

    def test_microsecond_bonus_boundary_preserves_all_fractional_credit(self):
        state = character()
        boundary = NOW + timedelta(seconds=1800, microseconds=1)
        focus(state, 'gather', boundary)
        settle(state, NOW + timedelta(hours=1))
        first = Fraction(1_800_000_001, 1_000_000)
        units = first * 40 * 1000 + (3600 - first) * 40 * 1100
        actual_units = (state['eternal']['total_earned'] * DENOM
                        + state['eternal']['remainder']
                        + Fraction(state['eternal'].get('subsecond_remainder', 0), 1_000_000))
        self.assertEqual(actual_units, units)

    def test_repeated_partial_settlement_matches_single_settlement(self):
        a = character()
        focus(a, 'gather', NOW + timedelta(seconds=5, microseconds=123456))
        b = copy.deepcopy(a)
        for seconds in range(1, 61):
            settle(a, NOW + timedelta(seconds=seconds))
        settle(b, NOW + timedelta(seconds=60))
        self.assertEqual(a['eternal'], b['eternal'])
        self.assertEqual(a['player'], b['player'])

    def test_closed_rate_windows_are_pruned_after_settlement(self):
        state = character()
        focus(state, 'gather', NOW)
        state, _ = apply(state, 'cultivation_focus', NOW + timedelta(hours=6), focus='insight')
        settle(state, NOW + timedelta(hours=7))
        windows = state['cultivation_v023']['windows']
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]['focus'], 'insight')

    def test_combined_multiplier_still_has_six_times_cap(self):
        state = character()
        focus(state, 'gather', NOW)
        add_boon(state, NOW, 'legendary')
        state['sect_qi_level'] = 20
        state['cultivation_path'] = 'Spirit'
        self.assertEqual(rate_now(state, NOW, root='Fire')[1], 6000)

    def test_journal_and_summary_do_not_mutate_or_grant_rewards(self):
        state = character()
        before = copy.deepcopy(state)
        for _ in range(3):
            self.assertIn('never expires', journey(state, 'Fire', NOW))
            self.assertTrue(cultivation_summary(state, 'Fire', NOW))
            changed, reply = apply(state, 'journey', NOW, root='Fire')
            self.assertEqual(changed, state)
            self.assertIn('Journal', reply)
        self.assertEqual(state, before)

    def test_dashboard_shows_focus_mastery_and_ready_time(self):
        state = character()
        focus(state, 'gather', NOW)
        practice(state, 'Earth', NOW, roll=999)
        text = '\n'.join(cultivation_summary(state, 'Earth', NOW))
        self.assertIn('Gathering', text)
        self.assertIn('4/100', text)
        self.assertIn('<t:', text)
        self.assertIn('Cultivation focus/mastery: +10% Qi', boost_summary(state, 0, 'Earth', NOW))

    def test_journal_remains_readable_during_active_breakthrough_trial(self):
        state = character()
        state['trial'] = {'phase_index': 0}
        before = copy.deepcopy(state)
        changed, reply = apply(state, 'journey', NOW, root='Earth')
        self.assertEqual(changed, before)
        self.assertEqual(state, before)
        self.assertIn('Journal', reply)

    def test_legacy_eternal_remainder_preserved(self):
        state = character()
        state['eternal']['scale_version'] = 1
        state['eternal']['remainder'] = 100
        settle(state, NOW + timedelta(seconds=1))
        self.assertEqual(state['eternal']['scale_version'], 2)
        self.assertEqual(state['eternal']['remainder'], 140000)

    def test_naive_time_is_rejected(self):
        state = character()
        with self.assertRaises(ValueError):
            focus(state, 'gather', NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            practice(state, 'Earth', NOW.replace(tzinfo=None), roll=999)


if __name__ == '__main__':
    unittest.main()
