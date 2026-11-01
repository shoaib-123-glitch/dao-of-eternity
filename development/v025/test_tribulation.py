import unittest
from engine import Cultivator
from tribulation import start_trial, choose_phase, finish_trial


def ready(**kwargs):
    return Cultivator(stage=9, qi=600, stability=85, meridian_integrity=95, **kwargs)


def play(t, body=65, dao=65):
    for choice in ('endure', 'accept', 'comprehend'):
        choose_phase(t, choice, body_resilience=body, dao_comprehension=dao)


class TrialTests(unittest.TestCase):
    def test_ineligible(self):
        with self.assertRaises(ValueError): start_trial(Cultivator())
        with self.assertRaises(ValueError): start_trial(ready(), prepared=False)

    def test_choices_advance_in_order(self):
        t = start_trial(ready())
        with self.assertRaises(ValueError): choose_phase(t,'accept')
        self.assertEqual(choose_phase(t,'endure')['next_phase'], 'heart_demon')
        self.assertEqual(choose_phase(t,'accept')['next_phase'], 'heavenly_dao')
        self.assertIsNone(choose_phase(t,'comprehend')['next_phase'])
        self.assertEqual(t.insight,1)

    def test_success_preserves_v04_contract(self):
        p=ready()
        t=start_trial(p); play(t)
        r=finish_trial(p,t,roll=0)
        self.assertTrue(r['success'])
        self.assertTrue(p.advanced_to_foundation)
        self.assertEqual(p.qi,0)
        with self.assertRaises(ValueError): finish_trial(p,t,roll=0)

    def test_failure_penalty(self):
        p=ready()
        t=start_trial(p); play(t)
        r=finish_trial(p,t,roll=99.9)
        self.assertFalse(r['success'])
        self.assertEqual(p.qi,420)
        self.assertEqual(p.stability,75)
        self.assertEqual(p.failure_count,1)

    def test_prevent_midtrial_mutations(self):
        p=ready();t=start_trial(p);play(t)
        p.qi=599
        with self.assertRaises(ValueError): finish_trial(p,t,roll=0)
        self.assertFalse(t.completed)

    def test_cannot_resolve_incomplete_trial(self):
        p=ready();t=start_trial(p)
        with self.assertRaises(ValueError): finish_trial(p,t,roll=0)

    def test_guaranteed_fourth_attempt(self):
        p=ready(failure_count=3, impurity=20)
        t=start_trial(p)
        play(t,body=0,dao=0)
        r=finish_trial(p,t,roll=99.9)
        self.assertTrue(r['success'])

    def test_invalid_values_and_duplicate_phases(self):
        p=ready();t=start_trial(p)
        with self.assertRaises(ValueError): choose_phase(t,'endure',body_resilience=101)
        play(t)
        with self.assertRaises(ValueError): choose_phase(t,'endure')
        with self.assertRaises(ValueError): finish_trial(p,t,roll=100)

    def test_low_endurance_collapse_fails(self):
        p=ready()
        t=start_trial(p)
        for choice in ('redirect', 'sever', 'resist'):
            choose_phase(t,choice,body_resilience=0,dao_comprehension=0)
        self.assertEqual(t.endurance,0)
        self.assertFalse(finish_trial(p,t,roll=0)['success'])

    def test_cannot_bypass_bad_impurity(self):
        p=ready(impurity=65)
        with self.assertRaises(ValueError): start_trial(p)

    def test_bonus_is_bounded(self):
        p=ready();t=start_trial(p);play(t,body=100,dao=100)
        r=finish_trial(p,t,roll=0)
        self.assertLessEqual(r['survival_bonus'],10)

if __name__ == '__main__': unittest.main()
