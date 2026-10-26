"""Unified release tests: Qi rebalancing, root mechanics, global events, elixirs."""
import unittest
from datetime import datetime,timedelta,timezone,date
from game import default_state,apply,unpack_player,pack_player
from heavens import (COMMON,RARE,ALL_ROOTS,ELIXIRS,PATHS,world_for_day,events_between,
                     add_boon,seek_omen,queue_elixir,multiplier_milli,
                     boost_boundaries,active_at,boost_summary)
from eternal import settle,rate_now,refill_after_advance
from combat import begin,turn,auto

NOW=datetime(2026,10,22,10,tzinfo=timezone.utc)

class NewQiTests(unittest.TestCase):
    def setUp(self):
        self.s=default_state(NOW);settle(self.s,NOW)

    def test_base_qi_refining(self):
        self.assertEqual(settle(self.s,NOW+timedelta(hours=1)),40)
    def test_base_foundation(self):
        p=unpack_player(self.s['player']);p.advanced_to_foundation=True
        self.s['player']=pack_player(p);self.s['foundation_stage']=2
        self.assertEqual(settle(self.s,NOW+timedelta(hours=1)),60)
    def test_all_roots_equal_innate_bonus(self):
        for root in ALL_ROOTS:
            s=default_state(NOW);settle(s,NOW)
            settle(s,NOW+timedelta(hours=1),root=root)
            self.assertEqual(unpack_player(s['player']).qi,44,root)
    def test_personal_2x_for_half_hour(self):
        add_boon(self.s,NOW,'rare')
        self.assertEqual(settle(self.s,NOW+timedelta(minutes=30)),40)
    def test_personal_bonus_expires_mid_offline(self):
        add_boon(self.s,NOW,'rare')
        # First 120m at 2x, last 60m at 1x = 200 Qi, banked beyond cap.
        self.assertEqual(settle(self.s,NOW+timedelta(hours=3)),200)
        self.assertEqual(unpack_player(self.s['player']).qi,100)
        self.assertEqual(self.s['eternal']['bank_qi'],100)
    def test_all_boosts_cap_sixfold(self):
        world={'mult':5000,'affinity':'Lightning'}
        personal={'mult':5000};elixir={'mult':2500}
        self.assertEqual(multiplier_milli(world,personal,elixir,'Lightning'),6000)
    def test_manual_advance_refills_bank(self):
        s=self.s
        p=unpack_player(s['player']);p.advanced_to_foundation=True
        s['player']=pack_player(p);s['foundation_stage']=1;s['foundation_qi']=180
        s['eternal']['bank_qi']=330
        changed,_=apply(s,'foundation_advance',NOW)
        self.assertEqual(changed['foundation_stage'],2)
        self.assertEqual(changed['foundation_qi'],220)
        self.assertEqual(changed['eternal']['bank_qi'],110)
    def test_upgrade_old_remainder(self):
        self.s['eternal']['scale_version']=1;self.s['eternal']['remainder']=1800
        self.assertEqual(settle(self.s,NOW+timedelta(minutes=3)),2)
        self.assertEqual(self.s['eternal']['scale_version'],2)
    def test_qi_status_is_stable(self):
        updated,msg=apply(self.s,'cultivation_status',NOW)
        self.assertIn('40 Qi/hour',msg)
        self.assertIn('never expires',msg)
        self.assertEqual(updated,self.s)
    def test_existing_legacy_character_not_backpaid(self):
        s=default_state(NOW-timedelta(days=100))
        self.assertEqual(settle(s,NOW),0)
    def test_one_year_offline_accrues_into_bank(self):
        s=default_state(NOW);settle(s,NOW)
        earned=settle(s,NOW+timedelta(days=365))
        self.assertEqual(earned,40*24*365)
        self.assertEqual(s['eternal']['bank_qi'],earned-100)

class WorldEventTests(unittest.TestCase):
    def test_repeatable_across_calls(self):
        self.assertEqual(world_for_day(101,date(2026,10,21)),world_for_day(101,date(2026,10,21)))
    def test_empty_without_guild(self):
        self.assertIsNone(world_for_day(0,date(2026,10,21)))
    def test_reasonable_frequency(self):
        seen=sum(world_for_day(101,date(2026,10,22)+timedelta(days=i)) is not None for i in range(365))
        self.assertGreater(seen,60)
        self.assertLess(seen,190)
    def test_event_all_players_even_when_offline(self):
        guild=13557
        day=next(date(2026,10,22)+timedelta(days=i) for i in range(60)
                 if world_for_day(guild,date(2026,10,22)+timedelta(days=i)))
        ev=world_for_day(guild,day)
        t0=ev['start']-timedelta(hours=1);t1=ev['end']+timedelta(hours=1)
        a=default_state(t0);b=default_state(t0)
        settle(a,t0);settle(b,t0)
        self.assertEqual(settle(a,t1,guild),settle(b,t1,guild))
        plain=default_state(t0);settle(plain,t0)
        base=settle(plain,t1)
        self.assertGreater(a['eternal']['total_earned'],base)
    def test_event_detectable_during_window(self):
        day=next(date(2026,10,22)+timedelta(days=i) for i in range(30)
                 if world_for_day(42,date(2026,10,22)+timedelta(days=i)))
        ev=world_for_day(42,day)
        events=events_between(42,ev['start']-timedelta(hours=1),ev['end']+timedelta(hours=1))
        self.assertTrue(any(x['id']==ev['id'] for x in events))
    def test_root_matching_world_boost(self):
        w={'mult':2000,'affinity':'Fire'}
        self.assertGreater(multiplier_milli(w,None,None,'Fire'),multiplier_milli(w,None,None,'Water'))
    def test_every_root_same_base_affinity(self):
        for root in ALL_ROOTS:self.assertEqual(multiplier_milli(None,None,None,root),1100)

class OmenAndElixirTests(unittest.TestCase):
    def setUp(self):
        self.s=default_state(NOW);settle(self.s,NOW)
    def test_safe_success(self):
        text=seek_omen(self.s,NOW,'Fire','safe',150)
        self.assertIn('Volcanic Spirit Cave',text)
        self.assertEqual(self.s['personal_boons'][0]['mult'],2000)
    def test_legendary_success(self):
        text=seek_omen(self.s,NOW,'Lightning','safe',0)
        self.assertIn('5x',text)
        self.assertEqual(self.s['personal_boons'][0]['mult'],5000)
    def test_failure_gives_bank_qi(self):
        text=seek_omen(self.s,NOW,'Water','safe',999)
        self.assertIn('+5',text)
        self.assertEqual(self.s['eternal']['bank_qi'],5)
    def test_fail_cooldown(self):
        seek_omen(self.s,NOW,'Water','safe',999)
        with self.assertRaises(ValueError):seek_omen(self.s,NOW+timedelta(minutes=10),'Water','safe',0)
    def test_bold_failure_increases_impurity(self):
        updated,_=apply(self.s,'omen_seek',NOW,root='Fire',approach='bold',roll=999)
        self.assertEqual(updated['player']['impurity'],1)
    def test_omen_on_old_encounter_can_trigger(self):
        state=self.s
        state['encounter']={'key':'vortex','expires_at':(NOW+timedelta(minutes=5)).isoformat()}
        changed,msg=apply(state,'encounter_choice',NOW,choice='stabilize',omen_roll=10)
        self.assertIn('Rare cultivation resonance',msg)
        self.assertTrue(changed['personal_boons'])
    def test_queue_starts_sequentially(self):
        first=queue_elixir(self.s,'minor',NOW)
        second=queue_elixir(self.s,'greater',NOW)
        self.assertEqual(second['start_at'],first['end_at'])
        self.assertEqual(len(self.s['elixir_queue']),2)
    def test_elixir_queue_max_five(self):
        for i in range(5):queue_elixir(self.s,'minor',NOW)
        with self.assertRaises(ValueError):queue_elixir(self.s,'minor',NOW)
    def test_elixir_offline_credits_exact_window(self):
        queue_elixir(self.s,'minor',NOW)
        # 45min at 1.5x = 45 Qi, then 15min at 1x = 10 Qi
        self.assertEqual(settle(self.s,NOW+timedelta(hours=1)),55)
        self.assertFalse(self.s['elixir_queue'])
    def test_two_queued_elixirs_without_reactivation(self):
        queue_elixir(self.s,'minor',NOW)
        queue_elixir(self.s,'greater',NOW)
        # 45min*40*1.5 + 60min*40*2 = 125, bank=25
        self.assertEqual(settle(self.s,NOW+timedelta(minutes=105)),125)
        self.assertEqual(self.s['eternal']['bank_qi'],25)
    def test_path_is_once_only(self):
        changed,msg=apply(self.s,'path_select',NOW,path='Sword')
        self.assertIn('Sword',msg)
        with self.assertRaises(ValueError):apply(changed,'path_select',NOW,path='Spirit')
    def test_path_changes_passive_rate(self):
        self.s['cultivation_path']='Spirit'
        self.assertEqual(settle(self.s,NOW+timedelta(hours=1)),44)

class ElementalCombatTests(unittest.TestCase):
    def test_all_roots_elemental_art(self):
        for root in ALL_ROOTS:
            result=turn(begin(root=root),'elemental_art')
            self.assertEqual(result['turn'],1,root)
            self.assertLess(result['qi'],24)
    def test_fire_burn(self):
        b=turn(begin(root='Fire'),'elemental_art')
        self.assertIn('Burn',b['log'][-1])
    def test_water_healing(self):
        b=begin(root='Water');b['hp']=60
        b=turn(b,'elemental_art')
        self.assertGreater(b['hp'],60-11)
    def test_earth_guard(self):
        b=turn(begin(root='Earth'),'elemental_art')
        c=turn(begin(root='Unawakened'),'focus_qi')
        self.assertLess(b['max_hp']-b['hp'],c['max_hp']-c['hp'])
    def test_metal_piercing(self):
        b=turn(begin(root='Metal'),'elemental_art')
        c=turn(begin(root='Unawakened'),'elemental_art')
        self.assertGreater(c['enemy_hp']-b['enemy_hp'],0)
    def test_ice_slow(self):
        b=turn(begin(root='Ice'),'elemental_art')
        self.assertIn('Slow',b['log'][-1])
    def test_wind_evasion(self):
        b=turn(begin(root='Wind'),'elemental_art')
        self.assertIn('Gale Steps',b['log'][-1])
    def test_lightning_charge(self):
        b=turn(begin(root='Lightning'),'elemental_art')
        self.assertEqual(b['charge'],1)
    def test_body_path_max_hp(self):
        self.assertEqual(begin(path='Body')['max_hp'],105)
    def test_sword_path_bonus(self):
        self.assertEqual(begin(path='Sword')['gear_attack'],2)
    def test_formation_path_bonus(self):
        self.assertEqual(begin(path='Formation')['gear_ward'],1)
    def test_secondary_art_available_to_dual_root(self):
        b=begin(root='Lightning',secondary_root='Ice')
        result=turn(b,'secondary_art')
        self.assertEqual(result['turn'],1)
        self.assertIn('Frost Seal',result['log'][-1])
    def test_secondary_art_rejected_without_dual_root(self):
        b=begin(root='Fire')
        with self.assertRaises(ValueError):turn(b,'secondary_art')
        self.assertEqual(b['turn'],0)
    def test_secondary_root_resonates_during_world_event(self):
        world={'mult':2000,'affinity':'Ice'}
        without=multiplier_milli(world,None,None,'Lightning')
        with_dual=multiplier_milli(world,None,None,'Lightning',secondary='Ice')
        self.assertGreater(with_dual,without)
    def test_dual_root_does_not_double_flat_qi_bonus(self):
        self.assertEqual(multiplier_milli(None,None,None,'Fire',secondary='Ice'),1100)
    def test_auto_uses_other_roots(self):
        b=auto(begin(root='Fire'))
        self.assertGreater(b['mastery']['elemental_art'],0)
        self.assertIn(b['status'],('victory','draw','defeat'))

if __name__=='__main__':unittest.main()
