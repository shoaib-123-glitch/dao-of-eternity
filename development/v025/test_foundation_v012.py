import unittest
from datetime import datetime, timezone, timedelta
from game import default_state, apply
from rapid import foundation_capacity

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)

class FoundationTests(unittest.TestCase):
    def foundation(self):
        state=default_state(NOW)
        state['player']['advanced_to_foundation']=True
        return state

    def test_new_foundation_defaults(self):
        s=self.foundation()
        s,txt=apply(s,'profile',NOW)
        self.assertIn('Foundation Establishment Stage 1',txt)
        self.assertIn('0/180',txt)

    def test_foundation_first_reward(self):
        s,msg=apply(self.foundation(),'foundation_cultivate',NOW,choice='study')
        self.assertEqual(s['foundation_qi'],45)
        self.assertEqual(s['dao_insight'],1)

    def test_foundation_cooldown(self):
        s,_=apply(self.foundation(),'foundation_cultivate',NOW,choice='circulate')
        with self.assertRaisesRegex(ValueError,'cooling down'):
            apply(s,'foundation_cultivate',NOW,choice='study')

    def test_foundation_advance_requires_qi(self):
        with self.assertRaisesRegex(ValueError,'Need 180'):
            apply(self.foundation(),'foundation_advance',NOW)

    def test_foundation_advance(self):
        s=self.foundation()
        for i in range(4):
            s,_=apply(s,'foundation_cultivate',NOW+timedelta(minutes=2*i),choice='circulate')
        self.assertEqual(s['foundation_qi'],180)
        s,msg=apply(s,'foundation_advance',NOW+timedelta(minutes=8))
        self.assertEqual(s['foundation_stage'],2)
        self.assertEqual(s['foundation_qi'],0)
        self.assertIn('Stage 2',msg)

    def test_original_qi_untouched(self):
        s=self.foundation()
        s['player']['qi']=0
        s,_=apply(s,'foundation_cultivate',NOW,choice='circulate')
        self.assertEqual(s['player']['qi'],0)
        self.assertEqual(s['player']['stage'],1)

    def test_reject_qi_refining_foundation_action(self):
        with self.assertRaisesRegex(ValueError,'requires Foundation'):
            apply(default_state(NOW),'foundation_cultivate',NOW,choice='study')

    def test_quality_not_mutated(self):
        s=self.foundation()
        before=s['player']['foundation_quality']
        s,_=apply(s,'foundation_cultivate',NOW,choice='study')
        self.assertEqual(s['player']['foundation_quality'],before)

    def test_capacity_increases(self):
        self.assertEqual(foundation_capacity(1),180)
        self.assertEqual(foundation_capacity(9),500)

if __name__ == '__main__':
    unittest.main()
