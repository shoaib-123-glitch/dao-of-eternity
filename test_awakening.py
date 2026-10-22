import unittest
from awakening import validate_origin,profile_lines
from game import default_state
from datetime import datetime,timezone

class AwakeningTests(unittest.TestCase):
    def test_valid(self): self.assertTrue(validate_origin('Fire','Scholar'))
    def test_invalid_root(self):
        with self.assertRaises(ValueError): validate_origin('Void','Scholar')
    def test_invalid_background(self):
        with self.assertRaises(ValueError): validate_origin('Fire','Dragon King')
    def test_profile_legacy(self):
        p=profile_lines(default_state(datetime.now(timezone.utc)),None)
        self.assertIn('Unawakened',p['origin'])
    def test_profile_new(self):
        p=profile_lines(default_state(datetime.now(timezone.utc)),{'root':'Wood','background':'Scholar'})
        self.assertIn('Wood Root',p['origin'])
    def test_profile_full_cap(self):
        s=default_state(datetime.now(timezone.utc));s['player']['qi']=100
        self.assertIn('100/100',profile_lines(s,None)['progress'])
if __name__=='__main__':unittest.main()
