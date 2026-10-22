import unittest
from datetime import datetime,timezone,timedelta
from sect_halls import unpack,cost,bonuses,HALLS
from combat import begin,turn

class SectDomainTests(unittest.TestCase):
 def test_initial_halls(self):
  self.assertEqual(unpack(None)['levels'],{'gathering':0,'martial':0,'sanctuary':0})
 def test_upgrade_prices(self):
  self.assertEqual(cost('martial',0),20)
  self.assertEqual(cost('martial',2),60)
 def test_bad_hall_rejected(self):
  with self.assertRaises(ValueError):cost('missing',0)
 def test_upgrade_bonuses(self):
  self.assertEqual(bonuses({'levels':{'martial':2,'sanctuary':3}}),{'attack':4,'hp':18})
 def test_bonus_caps(self):
  self.assertEqual(bonuses({'levels':{'martial':99,'sanctuary':99}}),{'attack':10,'hp':30})
 def test_battle_breakdown_preserved(self):
  b=begin('spirit_wolf','Lightning',{}, {'attack':8,'hp':0,'ward':0})
  b['bonus_sources']={'weapon':5,'companion':3,'sect':0}
  out=turn(b,'strike')
  self.assertIn('weapon +5',out['log'][-1])
  self.assertIn('companion +3',out['log'][-1])
 def test_previous_battle_compatibility(self):
  b=begin('spirit_wolf','Lightning',{}, {'attack':5,'hp':0,'ward':0})
  out=turn(b,'strike')
  self.assertIn('Equipment grants +5 damage',out['log'][-1])

if __name__=='__main__':unittest.main()
