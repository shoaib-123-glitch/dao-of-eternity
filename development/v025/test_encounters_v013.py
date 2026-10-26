import unittest
from datetime import datetime, timezone, timedelta
from game import apply, default_state
from encounters import ENCOUNTERS

NOW=datetime(2026,10,20,tzinfo=timezone.utc)

class EncounterTests(unittest.TestCase):
    def test_start_and_resolve(self):
        state,reply=apply(default_state(NOW),'encounter_start',NOW)
        self.assertIn('Vortex',reply)
        state,reply=apply(state,'encounter_choice',NOW,choice='study')
        self.assertEqual(state['dao_insight'],2)
        self.assertIsNone(state['encounter'])
    def test_double_resolution_rejected(self):
        state,_=apply(default_state(NOW),'encounter_start',NOW)
        state,_=apply(state,'encounter_choice',NOW,choice='study')
        with self.assertRaisesRegex(ValueError,'No active encounter'):
            apply(state,'encounter_choice',NOW,choice='study')
    def test_cooldown(self):
        state,_=apply(default_state(NOW),'encounter_start',NOW)
        state,_=apply(state,'encounter_choice',NOW,choice='study')
        with self.assertRaisesRegex(ValueError,'rest'):
            apply(state,'encounter_start',NOW+timedelta(minutes=2))
    def test_expiry(self):
        state,_=apply(default_state(NOW),'encounter_start',NOW)
        with self.assertRaisesRegex(ValueError,'expired'):
            apply(state,'encounter_choice',NOW+timedelta(minutes=16),choice='study')
    def test_invalid_choice_no_reward(self):
        state,_=apply(default_state(NOW),'encounter_start',NOW)
        with self.assertRaises(ValueError):
            apply(state,'encounter_choice',NOW,choice='harvest')
    def test_rotating_events(self):
        state=default_state(NOW)
        for i,key in enumerate(ENCOUNTERS):
            start_time=NOW+timedelta(minutes=11*i)
            state,_=apply(state,'encounter_start',start_time)
            self.assertEqual(state['encounter']['key'],key)
            choice=next(iter(ENCOUNTERS[key][2]))
            state,_=apply(state,'encounter_choice',start_time,choice=choice)
    def test_foundation_character_not_reset(self):
        state=default_state(NOW)
        state['player']['advanced_to_foundation']=True
        state['foundation_stage']=2
        state,_=apply(state,'encounter_start',NOW)
        state,_=apply(state,'encounter_choice',NOW,choice='study')
        self.assertTrue(state['player']['advanced_to_foundation'])
        self.assertEqual(state['foundation_stage'],2)
    def test_no_extra_progression_qi(self):
        state=default_state(NOW)
        state,_=apply(state,'encounter_start',NOW)
        state,_=apply(state,'encounter_choice',NOW,choice='absorb')
        self.assertEqual(state['player']['qi'],0)
        self.assertEqual(state['player']['impurity'],4)

if __name__=='__main__': unittest.main()
