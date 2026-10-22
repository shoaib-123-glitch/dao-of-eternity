import unittest
from datetime import datetime,timedelta,timezone
from game import default_state,apply,unpack_player,pack_player
from engine import Cultivator
UTC=timezone.utc
BASE=datetime(2026,10,19,12,tzinfo=UTC)
class GameTests(unittest.TestCase):
    def setUp(self): self.s=default_state(BASE)
    def step(self,op,time=BASE,**kw):
        self.s,msg=apply(self.s,op,time,**kw)
        return msg
    def test_serializer(self):
        p=unpack_player(self.s['player']);self.assertEqual(pack_player(p),self.s['player'])
    def test_start_no_immediate_credit(self):
        self.step('start_meditation');self.assertEqual(self.s['player']['qi'],0)
        with self.assertRaises(ValueError):self.step('claim_meditation',BASE+timedelta(minutes=14))
        self.step('claim_meditation',BASE+timedelta(minutes=15));self.assertEqual(self.s['player']['qi'],20)
    def test_duplicate_claim_disallowed(self):
        self.step('start_meditation');self.step('claim_meditation',BASE+timedelta(minutes=15))
        with self.assertRaises(ValueError):self.step('claim_meditation',BASE+timedelta(minutes=15))
    def test_no_double_meditation(self):
        self.step('start_meditation')
        with self.assertRaises(ValueError):self.step('start_meditation')
    def test_training_resource_reward(self):
        self.step('training');self.assertEqual(self.s['player']['training_seals'],2)
        with self.assertRaises(ValueError):self.step('training',BASE+timedelta(minutes=10))
    def test_no_offline_double_claim(self):
        self.step('offline',BASE+timedelta(hours=4))
        first=self.s['player']['qi'];self.assertGreater(first,0)
        self.step('offline',BASE+timedelta(hours=4))
        self.assertEqual(first,self.s['player']['qi'])
    def test_old_day_offline_is_not_paid_today(self):
        self.step('offline',BASE+timedelta(hours=26))
        self.assertLessEqual(self.s['player']['qi'],80)
    def test_trial_isolation_and_resume(self):
        p=Cultivator(stage=9,qi=600,stability=85,meridian_integrity=95)
        self.s['player']=pack_player(p)
        self.step('trial_start')
        with self.assertRaises(ValueError):self.step('training')
        self.step('trial_choice',phase_index=0,choice='endure')
        with self.assertRaises(ValueError):self.step('trial_choice',phase_index=0,choice='endure')
        self.assertEqual(self.s['trial']['phase_index'],1)
    def test_trial_expiration(self):
        p=Cultivator(stage=9,qi=600,stability=85,meridian_integrity=95)
        self.s['player']=pack_player(p)
        self.step('trial_start');self.step('trial_choice',BASE+timedelta(minutes=31),phase_index=0,choice='endure')
        self.assertIsNone(self.s['trial']);self.assertEqual(self.s['player']['qi'],600)
    def test_invalid_action_no_mutation(self):
        original=self.s.copy()
        with self.assertRaises(ValueError):apply(self.s,'unknown',BASE)
        self.assertEqual(self.s,original)
if __name__=='__main__':unittest.main()
