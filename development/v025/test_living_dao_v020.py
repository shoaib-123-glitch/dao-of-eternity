import unittest
from datetime import datetime,timezone,timedelta
from living_dao import initial,decode,progress,available_quests,rate_limit,REGIONS,COMPANIONS
class LivingDaoTests(unittest.TestCase):
 def test_defaults(self):self.assertIsNone(initial()['sect'])
 def test_backward_compat(self):self.assertEqual(decode({'sect':'Azure'})['sect'],'Azure')
 def test_progress(self):self.assertEqual(progress(initial(),'explore')['progress']['explore'],1)
 def test_quests(self):self.assertEqual(len(available_quests(initial())),3)
 def test_explore_counts(self):self.assertEqual(len(REGIONS),3)
 def test_companions(self):self.assertEqual(len(COMPANIONS),3)
 def test_rate_limit(self):
  now=datetime.now(timezone.utc)
  with self.assertRaises(ValueError):rate_limit(now.isoformat(),now,300)
  rate_limit((now-timedelta(seconds=301)).isoformat(),now,300)
if __name__=='__main__':unittest.main()

