"""Pure domain and offline settlement regression tests for the v0.22 expansion."""
import unittest
from datetime import datetime,timedelta,timezone
from copy import deepcopy
from game import default_state,apply
from golden_core_v022 import capacity, start, choose, advance
from beast_v022 import settle as beast_settle,cap as beast_cap,advance as beast_advance,evolve
from bonds_v022 import normal,npc_recruit,can_dual,dual_boon,NPCS,settle_npcs,npc_advance,npc_capacity
from sect_v022 import raid_default,raid_join,raid_launch,raid_strike,contributor_rewards
from combat import begin,turn,auto
from heavens import multiplier_milli
from eternal import settle, rate_now

T=datetime(2026,10,22,12,0,tzinfo=timezone.utc)

def golden_ready():
    state=default_state(T)
    state['player']['advanced_to_foundation']=True
    state['foundation_stage']=9;state['foundation_peak']=True
    state['dao_comprehension']=70
    state['eternal']={'last_at':T.isoformat(),'remainder':0,'bank_qi':5000,'total_earned':5000,'scale_version':2}
    return state

class GoldenTests(unittest.TestCase):
    def test_requires_finished_foundation(self):
        with self.assertRaisesRegex(ValueError,'Stage 9'):start(default_state(T))
    def test_ritual_and_banked_qi(self):
        st=golden_ready();self.assertIn('ritual begins',start(st))
        self.assertIn('Phase 2',choose(st,'foundation'))
        choose(st,'clarity');reply=choose(st,'harmony')
        self.assertIn('Golden Core formed',reply)
        self.assertEqual(st['golden_stage'],1)
        self.assertEqual(st['golden_qi'],capacity(1))
        self.assertEqual(st['eternal']['bank_qi'],5000-capacity(1))
    def test_force_can_fail_without_destroying_foundation(self):
        st=golden_ready();start(st)
        for _ in range(3):reply=choose(st,'force')
        self.assertIn('dispersed',reply)
        self.assertFalse(st.get('golden_core'))
        self.assertTrue(st['foundation_peak'])
        self.assertIsNone(st['golden_trial'])
    def test_successful_nine_stages_and_stop(self):
        st=golden_ready();start(st)
        for key in ('foundation','clarity','harmony'):choose(st,key)
        for stage in range(1,10):
            st['golden_qi']=capacity(stage)
            advance(st)
        self.assertTrue(st['golden_peak'])
        with self.assertRaisesRegex(ValueError,'peak'):advance(st)
    def test_no_double_begin(self):
        st=golden_ready();start(st)
        with self.assertRaisesRegex(ValueError,'already active'):start(st)
    def test_game_apply_dispatch(self):
        st=golden_ready();_,reply=apply(st,'golden_core_trial',T)
        self.assertIn('ritual begins',reply)
    def test_rate_and_existing_fields(self):
        st=golden_ready();start(st)
        for key in ('foundation','clarity','harmony'):choose(st,key)
        _,_,rate=rate_now(st,T,root='Lightning')
        self.assertEqual(rate,93.5)
        self.assertEqual(st['player']['advanced_to_foundation'],True)
        self.assertEqual(st['foundation_stage'],9)

class BeastTests(unittest.TestCase):
    def setUp(self):self.s={'companion':'storm_hawk','bond':2}
    def test_first_access_no_backdating(self):
        s=beast_settle(self.s,T)
        self.assertEqual(s['beast_cultivation']['qi'],0)
    def test_passive_after_hours(self):
        s=beast_settle(self.s,T)
        s=beast_settle(s,T+timedelta(hours=2))
        self.assertEqual(s['beast_cultivation']['qi'],36)
    def test_overflow_and_advance(self):
        s=beast_settle(self.s,T)
        s=beast_settle(s,T+timedelta(hours=20))
        self.assertEqual(s['beast_cultivation']['qi'],beast_cap(1))
        self.assertEqual(s['beast_cultivation']['bank'],280)
        self.assertIn('Stage 2',beast_advance(s))
        self.assertEqual(s['beast_cultivation']['qi'],beast_cap(2))
    def test_evolve_requires_nine(self):
        s=beast_settle(self.s,T)
        with self.assertRaises(ValueError):evolve(s)
        s['beast_cultivation']['stage']=9;s['beast_cultivation']['qi']=beast_cap(9)
        self.assertIn('evolved',evolve(s))
    def test_companion_art(self):
        battle=begin('spirit_wolf','Lightning',gear={'beast':'storm_hawk','beast_bond':2})
        b=turn(battle,'beast_skill')
        self.assertEqual(b['turn'],1)
        self.assertIn('Storm Hawk',b['log'][-1])
        with self.assertRaisesRegex(ValueError,'recovers'):turn(b,'beast_skill')
    def test_no_companion_art_without_bond(self):
        with self.assertRaisesRegex(ValueError,'Adopt'):turn(begin(),'beast_skill')

class HouseholdTests(unittest.TestCase):
    def test_npc_roles(self):
        d=normal(None)
        self.assertIn('Lin Yue',npc_recruit(d,'lin_yue','spouse'))
        self.assertEqual(d['npcs'][0]['role'],'spouse')
        with self.assertRaises(ValueError):npc_recruit(d,'lin_yue','consort')
    def test_npc_offline_qi_and_stage(self):
        d=normal(None);npc_recruit(d,'lin_yue','spouse',T)
        settle_npcs(d,T+timedelta(hours=9))
        npc=d['npcs'][0]
        self.assertEqual(npc['qi'],npc_capacity(1))
        self.assertEqual(npc['bank'],8)
        self.assertIn('Stage 2',npc_advance(d,'lin_yue'))
        self.assertEqual(npc['qi'],8)
    def test_npc_stage_needs_qi(self):
        d=normal(None);npc_recruit(d,'mei_xin','disciple',T)
        with self.assertRaisesRegex(ValueError,'needs'):npc_advance(d,'mei_xin')
    def test_invalid_roles(self):
        with self.assertRaises(ValueError):npc_recruit(normal(None),'lin_yue','owner')
    def test_three_romantic_partner_cap(self):
        d=normal(None)
        for name in NPCS:npc_recruit(d,name,'consort')
        self.assertEqual(len(d['npcs']),3)
    def test_dual_cooldown(self):
        d=normal(None);can_dual(d,T)
        d['last_dual_at']=T.isoformat()
        with self.assertRaises(ValueError):can_dual(d,T+timedelta(hours=1))
        can_dual(d,T+timedelta(hours=4))
    def test_nonstacking_safety_cap(self):
        char={'dual_boons':[]};dual_boon(char,T)
        self.assertEqual(len(char['dual_boons']),1)
        self.assertEqual(multiplier_milli(None,None,None,'Lightning',dual=char['dual_boons'][0]),1375)
        self.assertEqual(multiplier_milli({'mult':5000,'affinity':'Fire'}, {'mult':5000}, {'mult':2500},'Lightning',dual=char['dual_boons'][0]),6000)
    def test_dual_offline_time_only(self):
        st=default_state(T);st['eternal']={'last_at':T.isoformat(),'remainder':0,'bank_qi':0,'total_earned':0,'scale_version':2}
        dual_boon(st,T)
        settle(st,T+timedelta(hours=2),root='Lightning')
        # First hour 40*1.1*1.25=55, next hour 44. No boost after expiry.
        self.assertEqual(st['eternal']['total_earned'],99)

class SectTests(unittest.TestCase):
    def test_real_two_players_required(self):
        r=raid_default();r['status']='recruiting'
        raid_join(r,101)
        with self.assertRaisesRegex(ValueError,'two'):raid_launch(r)
        raid_join(r,202);msg=raid_launch(r)
        self.assertIn('2 cultivators',msg)
    def test_one_player_cannot_join_as_many(self):
        r=raid_default();r['status']='recruiting'
        for _ in range(3):raid_join(r,101)
        self.assertEqual(len(r['members']),1)
    def test_no_unjoined_attack(self):
        r=raid_default();r['status']='recruiting';raid_join(r,1);raid_join(r,2);raid_launch(r)
        with self.assertRaisesRegex(ValueError,'Join'):raid_strike(r,3,T,20)
    def test_real_damage_and_idempotent_reward_structure(self):
        r=raid_default();r['status']='recruiting';raid_join(r,1);raid_join(r,2);raid_launch(r)
        self.assertEqual(raid_strike(r,1,T,25),25)
        with self.assertRaisesRegex(ValueError,'cooling'):raid_strike(r,1,T,25)
        raid_strike(r,2,T,25)
        raid_strike(r,1,T+timedelta(seconds=31),200)
        self.assertEqual(r['status'],'victory')
        self.assertIn(1,contributor_rewards(r));self.assertIn(2,contributor_rewards(r))
        with self.assertRaises(ValueError):raid_strike(r,2,T+timedelta(seconds=31),5)
    def test_historical_gathering_only_after_upgrade(self):
        st=default_state(T);st['eternal']={'last_at':T.isoformat(),'remainder':0,'bank_qi':0,'total_earned':0,'scale_version':2}
        interval=[(T+timedelta(hours=1),T+timedelta(hours=2),1)]
        settle(st,T+timedelta(hours=2),root=None,sect_windows=interval)
        self.assertEqual(st['eternal']['total_earned'],82) # 40 first hour + 42 second hour

if __name__=='__main__':unittest.main()
