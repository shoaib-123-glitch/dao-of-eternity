import unittest
from combat import begin, turn, assisted, auto, display, enemy_intent, ENEMIES

class BossTests(unittest.TestCase):
    def setup_boss(self):
        return begin('thunder_dragon','Lightning',{'thunder_palm':7})

    def test_boss_added(self):
        self.assertTrue(ENEMIES['thunder_dragon']['boss'])

    def test_legacy_fight_works(self):
        b=turn(begin('spirit_wolf','Lightning'),'thunder_palm')
        self.assertEqual(b['turn'],1)

    def test_assisted_pauses_on_heavy(self):
        b=assisted(self.setup_boss())
        self.assertEqual(b['turn'],2)
        self.assertIn('Heavenfall', enemy_intent(b))
        self.assertEqual(b['status'],'active')

    def test_assist_resume_after_human(self):
        b=assisted(self.setup_boss())
        b=turn(b,'lightning_guard')
        self.assertEqual(b['turn'],3)
        b=assisted(b)
        self.assertEqual(b['turn'],5)

    def test_phase_and_ward(self):
        b=self.setup_boss()
        b=turn(b,'thunder_judgment')
        b=turn(b,'thunder_judgment')
        self.assertEqual(b['phase'],2)
        self.assertEqual(b['ward'],1)
        self.assertIn('Storm Ward',display(b))
        b=turn(b,'strike')
        self.assertEqual(b['ward'],0)

    def test_auto_terminates(self):
        b=auto(self.setup_boss())
        self.assertNotEqual(b['status'],'active')
        self.assertLessEqual(b['turn'],14)

    def test_boss_loot_on_win(self):
        b=self.setup_boss()
        b['enemy_hp']=1
        b=turn(b,'strike')
        self.assertEqual(b['status'],'victory')
        self.assertEqual(b['last_reward'],32)
        self.assertIn('Thunder Dragon Scale',b['loot'])

    def test_cultivation_separation(self):
        b=self.setup_boss()
        self.assertNotIn('foundation_qi',b)
        self.assertNotIn('stage',b)

if __name__=='__main__': unittest.main()
