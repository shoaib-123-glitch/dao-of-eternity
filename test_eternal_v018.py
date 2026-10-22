import copy
import unittest
from datetime import datetime, timedelta, timezone
from game import default_state, apply, unpack_player, pack_player
from eternal import settle, refill_after_advance, status

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)

class EternalCultivationTests(unittest.TestCase):
    def setup_state(self):
        s = default_state(NOW)
        settle(s, NOW)
        return s

    def test_created_automatically(self):
        s=self.setup_state()
        self.assertIn('eternal',s)
        self.assertIn('ACTIVE',status(s))

    def test_legacy_character_starts_without_backpay(self):
        s=default_state(NOW-timedelta(days=300))
        self.assertEqual(settle(s,NOW),0)
        self.assertEqual(s['eternal']['total_earned'],0)

    def test_one_hour_qi_refining(self):
        s=self.setup_state()
        self.assertEqual(settle(s,NOW+timedelta(hours=1)),40)
        self.assertEqual(unpack_player(s['player']).qi,40)

    def test_idempotent_same_time(self):
        s=self.setup_state()
        settle(s,NOW+timedelta(hours=1))
        self.assertEqual(settle(s,NOW+timedelta(hours=1)),0)
        self.assertEqual(unpack_player(s['player']).qi,40)

    def test_fractional_accumulates_without_loss(self):
        s=self.setup_state()
        self.assertEqual(settle(s,NOW+timedelta(minutes=5)),3)
        self.assertEqual(settle(s,NOW+timedelta(minutes=15)),7)

    def test_qi_banks_when_full(self):
        s=self.setup_state()
        p=unpack_player(s['player']); p.qi=p.capacity
        s['player']=pack_player(p)
        settle(s,NOW+timedelta(days=2))
        self.assertEqual(s['eternal']['bank_qi'],1920)
        self.assertEqual(unpack_player(s['player']).qi,p.capacity)

    def test_foundation_passive_rate_and_overflow(self):
        s=self.setup_state()
        p=unpack_player(s['player']);p.advanced_to_foundation=True
        s['player']=pack_player(p)
        s['foundation_stage']=2;s['foundation_qi']=0
        settle(s,NOW+timedelta(hours=1))
        self.assertEqual(s['foundation_qi'],60)
        settle(s,NOW+timedelta(days=30))
        self.assertEqual(s['foundation_qi'],220)
        self.assertGreater(s['eternal']['bank_qi'],0)

    def test_bank_refills_after_foundation_advancement(self):
        s=self.setup_state()
        p=unpack_player(s['player']);p.advanced_to_foundation=True
        s['player']=pack_player(p);s['foundation_stage']=1;s['foundation_qi']=180
        s['eternal']['bank_qi']=400
        updated,_=apply(s,'foundation_advance',NOW)
        self.assertEqual(updated['foundation_stage'],2)
        self.assertEqual(updated['foundation_qi'],220)
        self.assertEqual(updated['eternal']['bank_qi'],180)

    def test_negative_time_no_credit(self):
        s=self.setup_state()
        self.assertEqual(settle(s,NOW-timedelta(days=1)),0)

    def test_status_query_non_destructive(self):
        s=self.setup_state()
        updated,reply=apply(s,'cultivation_status',NOW)
        self.assertIn('never expires',reply)
        self.assertEqual(updated,s)

if __name__=='__main__': unittest.main()
