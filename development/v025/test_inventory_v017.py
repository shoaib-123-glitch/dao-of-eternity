import unittest
from inventory import empty,award,craft,equip,bonuses,DROPS,RECIPES,format_inventory,format_recipes
from combat import begin,turn,display

class InventoryTests(unittest.TestCase):
    def test_wolf_drop_and_marks(self):
        s=award(empty(),'spirit_wolf',8)
        self.assertEqual(s['holdings']['wolf_fang'],1)
        self.assertEqual(s['marks'],8)
    def test_boss_rewards(self):
        s=award(empty(),'thunder_dragon',32)
        self.assertEqual(s['holdings']['dragon_scale'],1)
        self.assertEqual(s['holdings']['heavenly_spark'],1)
    def test_sequential_rewards(self):
        s=award(award(empty(),'spirit_wolf',8),'spirit_wolf',8)
        self.assertEqual(s['holdings']['wolf_fang'],2)
    def test_cannot_craft_without_resources(self):
        with self.assertRaises(ValueError): craft(empty(),'thunderfang_sword')
    def test_sword_craft_consumes_exact_ingredients(self):
        s=award(award(empty(),'thunder_dragon',32),'spirit_wolf',8)
        c=craft(s,'thunderfang_sword')
        self.assertEqual(c['marks'],0)
        self.assertEqual(c['holdings']['dragon_scale'],0)
        self.assertEqual(c['holdings']['thunderfang_sword'],1)
        self.assertEqual(s['holdings']['dragon_scale'],1)
    def test_equip_requires_owned_item(self):
        with self.assertRaises(ValueError): equip(empty(),'stormguard_robe')
    def test_bonus_from_equipped_sword(self):
        s=empty();s['holdings']['thunderfang_sword']=1
        result=bonuses(equip(s,'thunderfang_sword'))
        self.assertEqual(result['attack'],5)
    def test_unequipped_item_does_not_boost(self):
        s=empty();s['holdings']['thunderfang_sword']=1
        self.assertEqual(bonuses(s)['attack'],0)
    def test_old_battle_compatible(self):
        b=begin('spirit_wolf','Lightning')
        self.assertEqual(b['hp'],95)
        self.assertEqual(b['gear_attack'],0)
    def test_new_battle_gear_stats(self):
        b=begin('spirit_wolf','Lightning',gear={'attack':5,'hp':20,'ward':2})
        self.assertEqual(b['hp'],115)
        self.assertIn('Equipment grants +5 damage',display(turn(b,'strike')))
    def test_invalid_recipe(self):
        with self.assertRaises(ValueError): craft(empty(),'cheat_item')
    def test_recipe_output(self):
        self.assertIn('Thunderfang Sword',format_recipes())
    def test_inventory_output(self):
        self.assertIn('Spirit Stones:',format_inventory(empty()))
    def test_gear_item_must_still_exist(self):
        s=empty();s['equipped']['weapon']='thunderfang_sword'
        self.assertEqual(bonuses(s)['attack'],0)
