import unittest
from combat import begin, turn, auto, display, unlocked, mastery_tier, select_auto_action

class CombatV015Tests(unittest.TestCase):
    def test_mastery_increments(self):
        b=turn(begin(root='Lightning'),'thunder_palm')
        self.assertEqual(b['mastery']['thunder_palm'],1)
    def test_mastery_survives_new_battle(self):
        old=turn(begin(),'thunder_palm')
        fresh=begin('spirit_wolf','Lightning',old['mastery'])
        self.assertEqual(fresh['mastery']['thunder_palm'],1)
        self.assertEqual(fresh['turn'],0)
    def test_basic_strike_storm_break(self):
        b=turn(begin(),'thunder_palm')
        before=b['enemy_hp']
        b=turn(b,'strike')
        self.assertEqual(before-b['enemy_hp'],19)
        self.assertEqual(b['charge'],0)
    def test_no_unlock_before_six_uses(self):
        b=begin()
        self.assertFalse(unlocked(b,'thunder_judgment'))
        with self.assertRaisesRegex(ValueError,'unlocks'):
            turn(b,'thunder_judgment')
        self.assertEqual(b['turn'],0)
    def test_unlock_at_six_uses(self):
        b=begin(mastery={'thunder_palm':6})
        self.assertTrue(unlocked(b,'thunder_judgment'))
        result=turn(b,'thunder_judgment')
        self.assertEqual(result['mastery']['thunder_judgment'],1)
    def test_mastery_bonus(self):
        beginner=turn(begin(),'strike')
        trained=turn(begin(mastery={'strike':8}),'strike')
        self.assertEqual(beginner['enemy_hp']-trained['enemy_hp'],2)
    def test_telegraph(self):
        b=begin(); self.assertIn('Normal attack',display(b))
        b=turn(b,'focus_qi')
        self.assertIn('HEAVY STRIKE',display(b) if b['turn']==2 else display(turn(b,'focus_qi')))
    def test_guard_heavy_strike(self):
        b=turn(turn(begin(),'focus_qi'),'focus_qi')
        guarded=turn(b,'lightning_guard')
        open_to_attack=turn(b,'focus_qi')
        self.assertLess(guarded['max_hp']-guarded['hp'],open_to_attack['max_hp']-open_to_attack['hp'])
    def test_auto_uses_same_engine(self):
        b=auto(begin(root='Lightning'))
        self.assertIn(b['status'],('victory','defeat','draw'))
        self.assertEqual(sum(b['mastery'].values()),b['turn'])
    def test_auto_strategy_combos(self):
        b=turn(begin(),'thunder_palm')
        self.assertEqual(select_auto_action(b),'strike')
    def test_malformed_action_no_mutation(self):
        b=begin()
        with self.assertRaises(ValueError): turn(b,'hack')
        self.assertEqual(b['mastery']['strike'],0)
    def test_mastery_tiers(self):
        self.assertEqual([mastery_tier(i) for i in (0,3,8,20)], [0,1,2,3])
    def test_no_cultivation_fields_in_battle(self):
        b=auto(begin())
        for k in ('foundation_stage','cultivation_qi','advanced_to_foundation'):
            self.assertNotIn(k,b)

if __name__=='__main__':unittest.main()
