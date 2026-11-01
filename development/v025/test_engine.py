import unittest
from datetime import datetime, timedelta, timezone
from engine import Cultivator, qi_cost
from simulate import run_profile, PROFILES

NOW = datetime(2026,10,19,8,tzinfo=timezone.utc)

class EngineTests(unittest.TestCase):
    def test_stage_costs(self):
        self.assertEqual([qi_cost(s) for s in range(1,9)], [100,125,160,205,260,325,400,485])
        self.assertEqual(sum(qi_cost(s) for s in range(1,9)),2060)

    def test_minor_breakthrough(self):
        p=Cultivator(qi=100)
        self.assertTrue(p.minor_breakthrough())
        self.assertEqual((p.stage,p.qi),(2,0))
        self.assertFalse(p.minor_breakthrough())

    def test_daily_active_limit_cooldown(self):
        p=Cultivator(stage=8)
        self.assertEqual(p.active_meditation(NOW),20)
        with self.assertRaises(ValueError): p.active_meditation(NOW+timedelta(minutes=14))
        for i in range(1,4): p.active_meditation(NOW+timedelta(minutes=15*i))
        self.assertEqual(p.earned_today['active'],80)
        with self.assertRaises(ValueError): p.active_meditation(NOW+timedelta(minutes=75))

    def test_training_recovery_consumes_slot(self):
        p=Cultivator(stability=64, meridian_integrity=70, impurity=18)
        self.assertEqual(p.stabilize(NOW),8)
        self.assertEqual(p.active_slots,1)
        self.assertEqual(p.recover_meridians(NOW),12)
        with self.assertRaises(ValueError): p.purify(NOW+timedelta(minutes=10))
        self.assertEqual(p.purify(NOW+timedelta(minutes=30)),6)
        self.assertEqual(p.training_slots,2)
        with self.assertRaises(ValueError): p.training(NOW+timedelta(minutes=60))

    def test_offline_claim_no_double(self):
        p=Cultivator(stage=8)
        p.offline_cursor=NOW
        self.assertEqual(p.claim_offline(NOW+timedelta(hours=5)),50)
        self.assertEqual(p.claim_offline(NOW+timedelta(hours=5)),0)
        self.assertEqual(p.claim_offline(NOW+timedelta(hours=10)),30)
        self.assertEqual(p.claim_offline(NOW+timedelta(hours=11)),0)

    def test_offline_claim_long_absence_is_capped(self):
        p=Cultivator(stage=8)
        p.offline_cursor=NOW
        self.assertEqual(p.claim_offline(NOW+timedelta(days=5)),80)
        self.assertEqual(p.claim_offline(NOW+timedelta(days=5)),0)

    def test_meridian_opening_cost(self):
        p=Cultivator(training_seals=3)
        self.assertTrue(p.open_meridian())
        self.assertEqual((p.open_meridians,p.training_seals),(1,0))
        self.assertFalse(p.open_meridian())

    def test_weekly_foundation_limit(self):
        p=Cultivator(foundation_essences=15)
        for _ in range(4): self.assertTrue(p.refine_foundation(NOW))
        self.assertEqual(p.foundation_quality,68)
        self.assertFalse(p.refine_foundation(NOW))
        self.assertTrue(p.refine_foundation(NOW+timedelta(days=7)))

    def test_major_failure_then_pity_success(self):
        p=Cultivator(stage=9,qi=600,stability=90,meridian_integrity=100)
        self.assertFalse(p.attempt_foundation(99.9))
        self.assertEqual((p.qi,p.stability,p.meridian_integrity,p.impurity,p.failure_count),(420,80,95,6,1))
        p.qi=600
        self.assertFalse(p.attempt_foundation(99.9))
        p.qi=600
        self.assertFalse(p.attempt_foundation(99.9))
        p.qi=600
        self.assertEqual(p.foundation_probability(),100)
        self.assertTrue(p.attempt_foundation(99.9))
        self.assertTrue(p.advanced_to_foundation)

    def test_major_eligibility(self):
        p=Cultivator(stage=9,qi=600,stability=59)
        self.assertFalse(p.attempt_foundation(0))
        self.assertEqual(p.qi,600)
        p.stability=70;p.impurity=60
        self.assertFalse(p.attempt_foundation(0))

    def test_no_qi_overflow(self):
        p=Cultivator(qi=95)
        self.assertEqual(p.active_meditation(NOW),5)
        self.assertEqual(p.qi,100)
        self.assertTrue(p.minor_breakthrough())
        self.assertEqual(p.qi,0)

    def test_all_profiles_monotonic(self):
        for args in PROFILES.values():
            p, earned, s9, ready, daily=run_profile(*args)
            self.assertEqual(len(daily),30)
            self.assertLessEqual(p.qi,p.capacity)
            self.assertGreater(earned,0)
            self.assertIsNotNone(s9)
            self.assertIsNotNone(ready)

if __name__=='__main__': unittest.main(verbosity=2)
