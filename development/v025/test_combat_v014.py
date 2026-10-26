import unittest
from combat import begin, turn, auto, display, ENEMIES
class CombatTests(unittest.TestCase):
    def test_starts_all_enemies(self):
        for enemy in ENEMIES: self.assertEqual(begin(enemy)['status'],'active')
    def test_bad_enemy(self):
        with self.assertRaises(ValueError): begin('unknown')
    def test_lightning_root_bonus(self):
        a=turn(begin(root='Lightning'),'thunder_palm')
        b=turn(begin(),'thunder_palm')
        self.assertEqual(b['enemy_hp']-a['enemy_hp'],4)
    def test_guard_reduces_damage(self):
        a=turn(begin(),'lightning_guard'); b=turn(begin(),'focus_qi')
        self.assertLess(a['max_hp']-a['hp'],b['max_hp']-b['hp'])
    def test_insufficient_qi_no_mutation(self):
        b=begin(); b['qi']=0
        with self.assertRaises(ValueError): turn(b,'thunder_palm')
        self.assertEqual(b['turn'],0)
    def test_auto_terminates(self):
        for enemy in ENEMIES:
            b=auto(begin(enemy,'Lightning'))
            self.assertIn(b['status'],('victory','defeat','draw'))
            self.assertLessEqual(b['turn'],14)
    def test_manual_victory(self):
        b=begin(root='Lightning')
        for _ in range(14):
            if b['status']!='active': break
            b=turn(b,'thunder_palm' if b['qi']>=8 else 'strike')
        self.assertEqual(b['status'],'victory')
    def test_no_duplicate_turn_after_finished(self):
        b=auto(begin())
        with self.assertRaises(ValueError): turn(b,'strike')
    def test_display(self): self.assertIn('Spirit Wolf',display(begin()))
    def test_data_isolation(self):
        b=begin(); turn(b,'strike')
        self.assertEqual(b['turn'],0)
    def test_invalid_action(self):
        with self.assertRaises(ValueError): turn(begin(),'exploit')
if __name__=='__main__':unittest.main()
