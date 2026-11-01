"""Stable regressions: real handlers over transactional in-memory records only."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import combat
import beast_v022
import combat_store
import inventory
import living_dao
import sect_halls
import living_world_v024 as world
from heavens import RARE_CORRESPONDENCE
from test_living_dao_ui_v024 import load_definition, interaction
from test_living_dao_v024 import T, Clock, companion, character
from test_persistence_regressions import CombatDatabase, CombatConnection


class InventoryConnection:
    def __init__(self):
        self.stock = dict(marks=12, holdings={'wolf_fang': 3, 'thunderfang_sword': 1}, equipped={})
        self.receipts = {}
        self.lock = asyncio.Lock()
        self.fail_receipt = False

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            before = deepcopy((self.stock, self.receipts))
            try:
                yield
            except BaseException:
                self.stock, self.receipts = before
                raise

    async def fetchval(self, sql, *args):
        if sql.startswith('SELECT 1 FROM characters'):
            return 1
        if sql.startswith('SELECT response FROM processed_actions'):
            return self.receipts.get(args)
        raise AssertionError('Unexpected fake read')

    async def fetchrow(self, sql, *args):
        if 'FROM player_inventory' in sql:
            return deepcopy(self.stock)
        raise AssertionError('Unexpected fake row')

    async def execute(self, sql, *args):
        if sql.startswith('INSERT INTO player_inventory'):
            return
        if sql.startswith('UPDATE player_inventory'):
            self.stock = dict(holdings=json.loads(args[2]), marks=args[3], equipped=json.loads(args[4]))
        elif sql.startswith('INSERT INTO processed_actions'):
            if self.fail_receipt:
                raise RuntimeError('synthetic receipt failure')
            self.receipts[args[:3]] = args[3]
        else:
            raise AssertionError('Unexpected fake mutation')


class CraftReplayTests(unittest.IsolatedAsyncioTestCase):
    def handler(self, name, con):
        import discord
        ns = dict(discord=discord, inventory_system=inventory, authorized=lambda i: True,
                  bot=SimpleNamespace(repo=SimpleNamespace(pool=con)),
                  logging=SimpleNamespace(exception=lambda *a: None))
        return load_definition(name, ns)

    async def test_same_discord_craft_delivery_spends_resources_once(self):
        con = InventoryConnection()
        handler, item = self.handler('craft', con), interaction()
        await handler(item, 'mistwood_blade')
        await handler(item, 'mistwood_blade')
        self.assertEqual(con.stock['marks'], 8)
        self.assertEqual(con.stock['holdings']['wolf_fang'], 2)
        self.assertEqual(con.stock['holdings']['mistwood_blade'], 1)
        self.assertEqual(item.followup.send.await_args_list[0], item.followup.send.await_args_list[1])

    async def test_concurrent_duplicate_craft_is_paid_once(self):
        con = InventoryConnection()
        handler, item = self.handler('craft', con), interaction()
        await asyncio.gather(handler(item, 'mistwood_blade'), handler(item, 'mistwood_blade'))
        self.assertEqual(con.stock['marks'], 8)
        self.assertEqual(con.stock['holdings']['mistwood_blade'], 1)

    async def test_replayed_equip_does_not_undo_a_later_equipment_choice(self):
        con = InventoryConnection()
        con.stock['holdings']['mistwood_blade'] = 1
        handler = self.handler('equip', con)
        old, new = interaction(), interaction()
        new.id = old.id + 1
        await handler(old, 'mistwood_blade')
        await handler(new, 'thunderfang_sword')
        await handler(old, 'mistwood_blade')
        self.assertEqual(con.stock['equipped']['weapon'], 'thunderfang_sword')

    async def test_receipt_failure_rolls_back_materials_marks_and_gear(self):
        con = InventoryConnection()
        before = deepcopy(con.stock)
        con.fail_receipt = True
        repo = SimpleNamespace(pool=con)
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            await inventory.change(repo, 1, 7, 'craft', 'mistwood_blade', event_id=123)
        self.assertEqual(con.stock, before)
        self.assertEqual(con.receipts, {})
        con.fail_receipt = False
        await inventory.change(repo, 1, 7, 'craft', 'mistwood_blade', event_id=123)
        self.assertEqual(con.stock['marks'], 8)
        self.assertEqual(len(con.receipts), 1)

    async def test_explicit_new_craft_is_allowed_and_old_python_api_is_compatible(self):
        con = InventoryConnection()
        repo = SimpleNamespace(pool=con)
        await inventory.change(repo, 1, 7, 'craft', 'mistwood_blade', event_id=123)
        await inventory.change(repo, 1, 7, 'craft', 'mistwood_blade', event_id=124)
        self.assertEqual(con.stock['holdings']['mistwood_blade'], 2)
        await inventory.change(repo, 1, 7, 'equip', 'mistwood_blade')
        self.assertEqual(con.stock['equipped']['weapon'], 'mistwood_blade')


class CombatConsistencyTests(unittest.TestCase):
    def test_rare_roots_follow_advertised_common_effect_in_primary_and_secondary_art(self):
        for rare, common in RARE_CORRESPONDENCE.items():
            for secondary in (False, True):
                with self.subTest(root=rare, secondary=secondary):
                    a = combat.begin('thunder_serpent', 'Fire' if secondary else rare,
                                     secondary_root=rare if secondary else None)
                    b = combat.begin('thunder_serpent', 'Fire' if secondary else common,
                                     secondary_root=common if secondary else None)
                    a['hp'] = b['hp'] = 50
                    action = 'secondary_art' if secondary else 'elemental_art'
                    a, b = combat.turn(a, action), combat.turn(b, action)
                    for key in ('root', 'secondary_root'):
                        a.pop(key)
                        b.pop(key)
                    self.assertEqual(a, b)

    def test_rare_lightning_uses_the_common_thunder_technique_bonus(self):
        a = combat.turn(combat.begin(root='Chaos'), 'thunder_palm')
        b = combat.turn(combat.begin(root='Lightning'), 'thunder_palm')
        self.assertEqual(a['enemy_hp'], b['enemy_hp'])

    def test_dragon_resource_planning_does_not_override_emergency_second_wind(self):
        b = combat.begin('thunder_dragon')
        b.update(hp=1, turn=1, qi=0)
        saved = deepcopy(b)
        assisted = combat.assisted(b)
        self.assertTrue(assisted['second_wind_used'])
        self.assertEqual((assisted['turn'], assisted['status']), (2, 'active'))
        automated = combat.auto(b)
        self.assertTrue(automated['second_wind_used'])
        self.assertEqual(b, saved)

    def test_recovery_display_distinguishes_final_battle_hp_from_current_camp_hp(self):
        from combat_journey_v024 import recover
        b = combat.begin()
        b.update(status='defeat',hp=0,
                 trail_v024=dict(hp=0,maximum=95,last_at=T.isoformat()))
        recover(b,T)
        text=combat.display(b,T)
        self.assertIn('At battle end',text)
        self.assertIn('Camp HP now: 57/95',text)
        self.assertIn('Free /recover in 120s',text)
        self.assertEqual(b['hp'],0)

    def test_technique_help_and_battle_output_fit_discord(self):
        self.assertLessEqual(len(combat.technique_help()),2000)
        for enemy in combat.ENEMIES:
            b=combat.begin(enemy,'Fire',{'thunder_palm':20},dict(beast='jade_fox',beast_bond=5,
                beast_attuned=True,attack=10,hp=30))
            while b['status']=='active':
                b=combat.turn(b,combat.select_auto_action(b))
                self.assertLessEqual(len(combat.display(b,T)),2000)
        text=combat.technique_help()
        for term in ('8 Combat Qi','three-turn cooldown','once per fight','cultivation Qi','Manual','Auto','Assisted'):
            self.assertIn(term,text)


class ProgressConnection:
    """Atomic player/sect records, distinct per-user stocks and keyed receipts."""
    def __init__(self):
        self.living={1:living_dao.decode({'sect':'Heavenly Dao','future':{'keep':True}}),
                     2:living_dao.decode({'sect':'Heavenly Dao'})}
        self.stocks={user:inventory.empty() for user in self.living}
        self.hall={'spent':20,'levels':{'gathering':1},'future':{'keep':True}}
        self.treasury=70
        self.receipts={}
        self.lock=asyncio.Lock()
        self.fail_on=None

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            before=deepcopy((self.living,self.stocks,self.hall,self.treasury,self.receipts))
            try:
                yield
            except BaseException:
                self.living,self.stocks,self.hall,self.treasury,self.receipts=before
                raise

    def repository(self):
        return SimpleNamespace(pool=self)

    async def fetchval(self,sql,*args):
        if sql.startswith('SELECT 1 FROM characters'):return 1
        if 'FROM living_dao_actions' in sql:return self.receipts.get(('living',*args))
        if 'FROM sect_hall_actions' in sql:return self.receipts.get(('sect',*args))
        if 'FROM living_dao_players' in sql:return json.dumps(self.living[args[1]])
        if 'FROM sect_hall_state' in sql:return json.dumps(self.hall)
        raise AssertionError('Unexpected fake read')

    async def fetchrow(self,sql,*args):
        if 'FROM living_dao_players' in sql:return {'data':json.dumps(self.living[args[1]])}
        if 'FROM player_inventory' in sql:return deepcopy(self.stocks[args[1]])
        if 'FROM sect_hall_state' in sql:return {'data':json.dumps(self.hall)}
        if 'FROM living_dao_sects' in sql:return {'founder_id':1,'contribution':self.treasury}
        raise AssertionError('Unexpected fake row')

    async def execute(self,sql,*args):
        if self.fail_on and sql.startswith(self.fail_on):
            raise RuntimeError('synthetic transaction failure')
        if sql.startswith('UPDATE living_dao_players'):self.living[args[1]]=json.loads(args[2])
        elif sql.startswith('UPDATE player_inventory'):
            self.stocks[args[1]]=dict(holdings=json.loads(args[2]),marks=args[3],equipped=json.loads(args[4]))
        elif sql.startswith('UPDATE sect_hall_state'):self.hall=json.loads(args[2])
        elif sql.startswith('INSERT INTO living_dao_actions'):self.receipts[('living',*args[:3])]=args[3]
        elif sql.startswith('INSERT INTO sect_hall_actions'):self.receipts[('sect',*args[:3])]=args[3]
        elif not sql.startswith(('INSERT INTO living_dao_players','INSERT INTO player_inventory','INSERT INTO sect_hall_state')):
            raise AssertionError('Unexpected fake mutation')


class WorldAndSectInvariants(unittest.IsolatedAsyncioTestCase):
    async def explore(self,con,user,event,now,roll):
        clock=type('InjectedClock',(Clock,),{'now':classmethod(lambda cls,tz=None:now)})
        with patch.object(living_dao,'datetime',clock),patch('secrets.randbelow',return_value=roll) as draw:
            reply=await living_dao.action(con.repository(),7,user,event,'explore',world.chapter(7,now)['region'])
        return reply,draw.call_count

    async def commission(self,con,user,event,activity,now):
        clock=type('InjectedClock',(Clock,),{'now':classmethod(lambda cls,tz=None:now)})
        with patch.object(sect_halls,'datetime',clock):
            return await sect_halls.act(con.repository(),7,user,event,'commission',activity)

    async def complete(self,con):
        await self.commission(con,1,1,'patrol',T)
        await self.commission(con,2,2,'archives',T)
        return await self.commission(con,1,3,'gardens',T+timedelta(hours=6))

    async def test_featured_and_rare_awards_once_each_even_across_regions_and_new_interactions(self):
        con=ProgressConnection()
        first,_=await self.explore(con,1,1,T,0)
        repeated,draws=await self.explore(con,1,1,T,79)
        self.assertEqual(first,repeated)
        self.assertEqual(draws,0)
        await self.explore(con,1,2,T+timedelta(minutes=5),0)
        total=sum(con.stocks[1]['holdings'].values())
        self.assertEqual(total,4)  # Two base materials, one chapter, one rare.
        self.assertEqual(con.living[1]['world_v024']['memories'],1)
        tomorrow=T+timedelta(days=1)
        await self.explore(con,1,3,tomorrow,0)
        self.assertEqual(sum(con.stocks[1]['holdings'].values()),7)
        self.assertEqual(con.living[1]['future'],{'keep':True})

    async def test_explore_retry_cannot_reroll_a_nonrare_result_or_bypass_cooldown(self):
        con=ProgressConnection()
        reply,_=await self.explore(con,1,1,T,999)
        repeated,count=await self.explore(con,1,1,T,0)
        self.assertEqual((repeated,count),(reply,0))
        with patch.object(living_dao,'datetime',Clock),patch('secrets.randbelow') as draw:
            with self.assertRaisesRegex(ValueError,'recovering'):
                await living_dao.action(con.repository(),7,1,2,'explore','mistwood')
        draw.assert_not_called()
        self.assertNotIn('rare_day',con.living[1]['world_v024'])

    async def test_world_save_failure_rolls_back_daily_entitlement_and_loot(self):
        con=ProgressConnection()
        before=deepcopy((con.living,con.stocks,con.receipts))
        con.fail_on='INSERT INTO living_dao_actions'
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            await self.explore(con,1,1,T,0)
        self.assertEqual((con.living,con.stocks,con.receipts),before)
        con.fail_on=None
        await self.explore(con,1,1,T,0)
        self.assertEqual(sum(con.stocks[1]['holdings'].values()),3)

    async def test_two_distinct_players_complete_and_collect_exactly_once(self):
        con=ProgressConnection()
        reply=await self.complete(con)
        self.assertIn('Shared project completed',reply)
        self.assertEqual(con.hall['renown'],30)  # 3x5 commission + 15 project.
        self.assertEqual((con.treasury,con.hall['spent']),(70,20))
        self.assertEqual(con.hall['future'],{'keep':True})
        for user in (1,2):
            await asyncio.gather(*(sect_halls.act(con.repository(),7,user,10+user,'project_claim') for _ in range(2)))
            await sect_halls.act(con.repository(),7,user,20+user,'project_claim')
        self.assertEqual(con.stocks[1]['marks'],14)
        self.assertEqual(con.stocks[2]['marks'],8)
        self.assertEqual(con.hall['projects_completed_v024'],1)

    async def test_one_player_cannot_complete_by_repetition_or_retries(self):
        con=ProgressConnection()
        for event,activity,hours in ((1,'patrol',0),(2,'archives',6),(3,'gardens',12)):
            await self.commission(con,1,event,activity,T+timedelta(hours=hours))
        before=deepcopy((con.hall,con.stocks))
        await self.commission(con,1,3,'gardens',T+timedelta(hours=12))
        self.assertEqual((con.hall,con.stocks),before)
        await sect_halls.act(con.repository(),7,1,4,'project_claim')
        self.assertFalse(con.hall['project_v024']['complete'])
        self.assertEqual(con.stocks[1]['marks'],18)
        self.assertNotIn('projects_completed_v024',con.hall)

    async def test_failed_completion_and_failed_claim_can_retry_without_partial_rewards(self):
        con=ProgressConnection()
        await self.commission(con,1,1,'patrol',T)
        await self.commission(con,2,2,'archives',T)
        before=deepcopy((con.hall,con.stocks,con.receipts))
        con.fail_on='INSERT INTO sect_hall_actions'
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            await self.commission(con,1,3,'gardens',T+timedelta(hours=6))
        self.assertEqual((con.hall,con.stocks,con.receipts),before)
        con.fail_on=None
        await self.commission(con,1,3,'gardens',T+timedelta(hours=6))
        before=deepcopy((con.hall,con.stocks,con.receipts))
        con.fail_on='INSERT INTO sect_hall_actions'
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            await sect_halls.act(con.repository(),7,1,5,'project_claim')
        self.assertEqual((con.hall,con.stocks,con.receipts),before)
        con.fail_on=None
        await sect_halls.act(con.repository(),7,1,5,'project_claim')
        self.assertEqual(con.stocks[1]['marks'],14)

    async def test_companion_evolution_replay_preserves_trust_memories_bond_and_reserve(self):
        con=ProgressConnection()
        s=companion()
        s.update(bond=3,companion_v024={'trust':18,'memories':4,'last_memory':'A remembered path.'})
        s['beast_cultivation'].update(stage=9,qi=beast_v022.cap(9),bank=200,earned=1000)
        con.living[1]=s
        with patch('datetime.datetime',Clock):
            a=await beast_v022.action(con.repository(),7,1,5,'evolve')
            b=await beast_v022.action(con.repository(),7,1,5,'evolve')
        self.assertEqual(a,b)
        saved=con.living[1]
        self.assertEqual((saved['bond'],saved['companion_v024']),(3,s['companion_v024']))
        beast=saved['beast_cultivation']
        self.assertEqual((beast['stage'],beast['qi'],beast['bank'],beast['earned'],beast['evolutions']),
                         (1,80,120,1000,1))

    async def test_bond_receipt_failure_does_not_consume_activity_or_award_qi(self):
        con=ProgressConnection()
        con.living[1]=companion()
        before=deepcopy(con.living[1])
        con.fail_on='INSERT INTO living_dao_actions'
        with patch('datetime.datetime',Clock),patch('secrets.randbelow',return_value=999):
            with self.assertRaisesRegex(RuntimeError,'synthetic'):
                await beast_v022.action(con.repository(),7,1,5,'bond','listen')
        self.assertEqual(con.living[1],before)
        con.fail_on=None
        with patch('datetime.datetime',Clock),patch('secrets.randbelow',return_value=999):
            await beast_v022.action(con.repository(),7,1,5,'bond','listen')
        self.assertEqual(con.living[1]['companion_v024']['trust'],4)
        self.assertEqual(con.living[1]['beast_cultivation']['earned'],6)


class AtomicCombatConnection(CombatConnection):
    @asynccontextmanager
    async def transaction(self):
        before=deepcopy((self.db.battle,self.db.stock,self.db.living,self.db.actions))
        async with super().transaction():
            try:
                yield
            except BaseException:
                self.db.battle,self.db.stock,self.db.living,self.db.actions=before
                raise

    async def execute(self,sql,*args):
        if self.db.fail_on and sql.startswith(self.db.fail_on):
            raise RuntimeError('synthetic combat save failure')
        return await super().execute(sql,*args)


class AtomicCombatDatabase(CombatDatabase):
    fail_on=None

    @asynccontextmanager
    async def acquire(self):
        yield AtomicCombatConnection(self)


class CombatFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_reward_or_receipt_failure_rolls_back_victory_and_first_clear_then_retry_pays_once(self):
        for fail in ('UPDATE player_inventory','INSERT INTO combat_actions'):
            with self.subTest(stage=fail):
                db=AtomicCombatDatabase()
                db.battle=combat.begin()
                db.battle['enemy_hp']=1
                before=deepcopy((db.battle,db.stock,db.actions))
                db.fail_on=fail
                with self.assertRaisesRegex(RuntimeError,'synthetic'):
                    await combat_store.execute(db.repository(),7,101,1,'turn',action='strike')
                self.assertEqual((db.battle,db.stock,db.actions),before)
                db.fail_on=None
                reply=await combat_store.execute(db.repository(),7,101,1,'turn',action='strike')
                self.assertEqual(await combat_store.execute(db.repository(),7,101,1,'turn',action='strike'),reply)
                self.assertEqual(db.stock['marks'],12)
                self.assertEqual(db.stock['holdings'],{'wolf_fang':1})
                self.assertTrue(db.battle['trail_v024']['first_clears']['spirit_wolf'])
