"""Living Dao rules and production-handler regressions; in-memory connections only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import beast_v022
import combat
import combat_store
import companion_bonds_v024 as bonds
import inventory
import living_dao
import living_world_v024 as world
import sect_halls
import sect_projects_v024 as projects
from combat_journey_v024 import (prepare, finish, recover, heal_over_time,
                                 journey, training_bonuses)
from eternal import settle
from game import default_state
from onboarding_v024 import guide, next_step
from shattered_heavens_v023 import practice
from test_persistence_regressions import CombatDatabase
from test_sect_companion_v023 import _Connection, _repo

T = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return T if tz else T.replace(tzinfo=None)


def character():
    s = default_state(T)
    settle(s, T)
    return s


def companion(key='jade_fox'):
    return beast_v022.settle({'companion': key, 'bond': 1}, T)


class CombatRulesTests(unittest.TestCase):
    def test_training_is_bounded_and_does_not_change_cultivation(self):
        s = character()
        self.assertEqual(training_bonuses(s), {'hp': 0, 'attack': 0})
        s['player']['stage'] = 3
        before = deepcopy(s)
        self.assertEqual(training_bonuses(s), {'hp': 8, 'attack': 1})
        self.assertEqual(s, before)
        s['player'].update(stage=1, advanced_to_foundation=True)
        self.assertEqual(training_bonuses(s), {'hp': 32, 'attack': 4})
        s.update(golden_core=True, golden_stage=9)
        self.assertEqual(training_bonuses(s), {'hp': 32, 'attack': 4})

    def test_unequipped_wolf_is_winnable_for_all_common_roots(self):
        for root in ('Unawakened', 'Fire', 'Water', 'Wood', 'Earth', 'Wind', 'Metal', 'Lightning', 'Ice'):
            with self.subTest(root=root):
                b = combat.auto(combat.begin(root=root))
                self.assertEqual(b['status'], 'victory')
                self.assertGreater(b['hp'], 0)
                self.assertLessEqual(b['turn'], combat.MAX_TURNS)

    def test_assisted_stops_before_heavy_for_every_enemy(self):
        for enemy in combat.ENEMIES:
            with self.subTest(enemy=enemy):
                b = combat.assisted(combat.begin(enemy))
                self.assertEqual((b['turn'], b['status']), (2, 'active'))
                self.assertIn('STRIKE' if enemy != 'thunder_dragon' else 'Heavenfall', combat.enemy_intent(b))

    def test_assisted_resumes_after_human_guard(self):
        b = combat.assisted(combat.begin('thunder_serpent'))
        b = combat.turn(b, 'lightning_guard')
        b = combat.assisted(b)
        self.assertEqual((b['turn'], b['status']), (5, 'active'))

    def test_second_wind_once_heals_and_enemy_still_attacks(self):
        b = combat.begin('thunder_serpent')
        b['hp'], b['qi'] = 35, 8
        before = deepcopy(b)
        b = combat.turn(b, 'second_wind')
        self.assertEqual(before['hp'], 35)
        self.assertEqual(b['last_action'], dict(action='second_wind', damage=0, healed=18, received=7, qi_delta=4))
        self.assertEqual(b['hp'], 46)
        with self.assertRaisesRegex(ValueError, 'once per battle'):
            combat.turn(b, 'second_wind')

    def test_full_health_cannot_spend_second_wind(self):
        b = combat.begin()
        before = deepcopy(b)
        with self.assertRaisesRegex(ValueError, 'full'):
            combat.turn(b, 'second_wind')
        self.assertEqual(b, before)

    def test_auto_selects_second_wind_only_when_injured(self):
        b = combat.begin()
        b['hp'] = 25
        self.assertEqual(combat.select_auto_action(b), 'second_wind')
        b['second_wind_used'] = True
        self.assertNotEqual(combat.select_auto_action(b), 'second_wind')

    def test_action_feedback_has_hp_damage_and_combat_qi(self):
        b = combat.turn(combat.begin(), 'thunder_palm')
        self.assertEqual(b['last_action']['damage'], 19)
        text = combat.display(b)
        for term in ('HP 95', 'Combat Qi 24', 'enemy 58', 'Choose your move'):
            self.assertIn(term, text)

    def test_legacy_completed_battle_starts_rested_without_losing_mastery(self):
        old = combat.auto(combat.begin())
        old['hp'] = 0
        fresh = prepare(combat.begin(mastery=old['mastery'], gear={'hp': 8}), old, T, 'assisted', 42)
        self.assertEqual(fresh['hp'], fresh['max_hp'])
        self.assertEqual(fresh['mastery'], old['mastery'])
        self.assertEqual(fresh['battle_id'], 42)

    def test_natural_recovery_retains_fractional_time_and_never_backtracks(self):
        info = dict(hp=0, maximum=95, last_at=T.isoformat(), future='kept')
        many = deepcopy(info)
        for i in range(1, 26):
            many = heal_over_time(many, T+timedelta(seconds=i*0.4))
        once = heal_over_time(info, T+timedelta(seconds=10))
        self.assertEqual(many, once)
        self.assertEqual(once['hp'], 2)
        self.assertEqual(once['future'], 'kept')
        self.assertEqual(heal_over_time(once, T-timedelta(days=1)), once)

    def test_camp_recovery_is_free_capped_and_not_available_in_battle(self):
        b = combat.begin()
        with self.assertRaisesRegex(ValueError, 'Retreat'):
            recover(b, T)
        b['status'] = 'defeat'
        b['trail_v024'] = dict(hp=0, maximum=95, last_at=T.isoformat(), first_clears={'spirit_wolf': True})
        text = recover(b, T)
        self.assertEqual(b['trail_v024']['hp'], 57)
        self.assertIn('No Spirit Stones or loot', text)
        with self.assertRaisesRegex(ValueError, 'ready in'):
            recover(b, T+timedelta(seconds=1))
        recover(b, T+timedelta(minutes=2))
        self.assertEqual(b['trail_v024']['hp'], 95)
        self.assertEqual(b['trail_v024']['first_clears'], {'spirit_wolf': True})

    def test_recovery_cooldown_survives_new_hunt(self):
        old = combat.begin()
        old['status'] = 'defeat'
        old['trail_v024'] = dict(hp=0, maximum=95, last_at=T.isoformat())
        recover(old, T)
        new = prepare(combat.begin(), old, T, 'manual', 2)
        self.assertEqual(new['hp'], 57)
        self.assertEqual(new['trail_v024']['rest_next_at'], (T+timedelta(minutes=2)).isoformat())

    def test_zero_hp_requires_recovery(self):
        old = combat.begin()
        old['trail_v024'] = dict(hp=0, maximum=95, last_at=T.isoformat())
        with self.assertRaisesRegex(ValueError, '/recover'):
            prepare(combat.begin(), old, T, 'manual', 2)

    def test_first_clear_awarded_once_and_never_for_defeat_or_retreat(self):
        before = prepare(combat.begin(), {}, T, 'manual', 1)
        after = combat.auto(before)
        self.assertEqual(finish(after, before, T), 4)
        next_battle = prepare(combat.begin(), after, T+timedelta(minutes=10), 'auto', 2)
        victory = combat.auto(next_battle)
        self.assertEqual(finish(victory, next_battle, T), 0)
        for status in ('defeat', 'draw', 'retreated'):
            lost = deepcopy(next_battle)
            lost['status'] = status
            self.assertEqual(finish(lost, next_battle, T), 0)
            self.assertEqual(lost['trail_v024']['first_clears'], {'spirit_wolf': True})

    def test_starter_blade_is_affordable_without_delaying_second_win_fox(self):
        stock = inventory.award(inventory.empty(), 'spirit_wolf', 12)
        stock = inventory.equip(inventory.craft(stock, 'mistwood_blade'), 'mistwood_blade')
        self.assertEqual(inventory.bonuses(stock)['attack'], 2)
        stock = inventory.award(stock, 'spirit_wolf', 8)
        self.assertEqual(stock['marks'], 16)
        self.assertEqual(inventory.RECIPES['thunderfang_sword']['attack'], 5)


class CompanionRulesTests(unittest.TestCase):
    def test_first_activity_guarantees_personality_memory_and_free_beast_qi(self):
        for key, (_, favorite, memory) in bonds.PROFILES.items():
            with self.subTest(key=key):
                s = companion(key)
                before = deepcopy(s)
                after, reply = bonds.bond_action(s, favorite, T, 999)
                self.assertEqual(s, before)
                self.assertEqual(after['companion_v024']['trust'], 4)
                self.assertEqual(after['companion_v024']['memories'], 1)
                self.assertEqual(after['beast_cultivation']['bank'], 6)
                self.assertIn(memory, reply)
                self.assertTrue(bonds.attuned(after, T))

    def test_nonfavorite_still_progresses_without_penalty(self):
        s, _ = bonds.bond_action(companion(), 'roam', T, 999)
        self.assertEqual(s['companion_v024']['trust'], 3)

    def test_cooldown_and_attunement_exact_boundaries(self):
        s, _ = bonds.bond_action(companion(), 'listen', T, 999)
        before = deepcopy(s)
        with self.assertRaisesRegex(ValueError, 'content'):
            bonds.bond_action(s, 'roam', T+timedelta(hours=6, microseconds=-1), 0)
        self.assertEqual(s, before)
        again, _ = bonds.bond_action(s, 'roam', T+timedelta(hours=6), 999)
        self.assertEqual(again['companion_v024']['activities'], 2)
        self.assertTrue(bonds.attuned(s, T+timedelta(hours=12, microseconds=-1)))
        self.assertFalse(bonds.attuned(s, T+timedelta(hours=12)))
        self.assertFalse(bonds.attuned(s, T-timedelta(seconds=1)))

    def test_rare_memory_boundaries(self):
        for roll, count in ((149, 2), (150, 1), (999, 1)):
            s, _ = bonds.bond_action(companion(), 'listen', T, 999)
            s, _ = bonds.bond_action(s, 'listen', T+timedelta(hours=6), roll)
            self.assertEqual(s['companion_v024']['memories'], count)

    def test_trust_levels_preserve_existing_evolution_and_qi(self):
        s = companion()
        s['bond'] = 3
        s['companion_v024'] = {'trust': 19, 'future': 'keep'}
        s['beast_cultivation'].update(qi=70, bank=8, evolutions=2, stage=4)
        after, _ = bonds.bond_action(s, 'listen', T, 999)
        self.assertEqual(after['bond'], 4)
        self.assertEqual(after['companion_v024']['trust'], 3)
        self.assertEqual(after['companion_v024']['future'], 'keep')
        self.assertEqual((after['beast_cultivation']['qi'], after['beast_cultivation']['bank'],
                          after['beast_cultivation']['stage'], after['beast_cultivation']['evolutions']), (70, 14, 4, 2))

    def test_maximum_bond_still_allows_memories_without_overflow(self):
        s = companion()
        s['bond'] = 5
        s['companion_v024'] = {'trust': 20}
        after, _ = bonds.bond_action(s, 'listen', T, 999)
        self.assertEqual((after['bond'], after['companion_v024']['trust']), (5, 20))

    def test_invalid_activity_or_roll_does_not_mutate(self):
        s = companion()
        for activity, roll in (('paid', 0), ('listen', True), ('listen', 1000), ('listen', -1)):
            before = deepcopy(s)
            with self.assertRaises(ValueError):
                bonds.bond_action(s, activity, T, roll)
            self.assertEqual(s, before)

    def test_no_companion_gives_earned_adoption_guidance(self):
        with self.assertRaisesRegex(ValueError, '16 earned'):
            bonds.bond_action({}, 'listen', T, 0)

    def test_fox_attunement_is_only_three_extra_healing(self):
        normal = combat.begin('thunder_serpent', gear={'beast': 'jade_fox', 'beast_bond': 1})
        normal['hp'] = 40
        attuned = deepcopy(normal)
        attuned['beast_attuned'] = True
        a, b = combat.turn(normal, 'beast_skill'), combat.turn(attuned, 'beast_skill')
        self.assertEqual(b['hp']-a['hp'], 3)
        self.assertEqual(b['enemy_hp'], a['enemy_hp'])

    def test_permanent_rate_and_manual_evolution_remain(self):
        s, _ = bonds.bond_action(companion(), 'listen', T, 999)
        after = beast_v022.settle(s, T+timedelta(days=30))
        self.assertEqual(after['beast_cultivation']['earned'], 6+30*24*18)
        self.assertEqual(after['beast_cultivation']['stage'], 1)
        self.assertEqual(after['beast_cultivation']['evolutions'], 0)
        self.assertEqual(after['bond'], 1)


class LivingWorldTests(unittest.TestCase):
    def test_chapter_is_shared_deterministic_and_utc(self):
        self.assertEqual(world.chapter(7, T), world.chapter(7, T.astimezone(timezone(timedelta(hours=-5)))))
        self.assertIn(world.chapter(7, T)['region'], living_dao.REGIONS)

    def test_featured_and_rare_rewards_are_each_once_per_utc_day(self):
        region = world.chapter(7, T)['region']
        original = {'future': {'x': 1}}
        s, count, reply = world.exploration(original, 7, region, T, 0)
        self.assertEqual(count, 2)
        self.assertIn('Rare trail', reply)
        self.assertEqual(original, {'future': {'x': 1}})
        repeated, count, _ = world.exploration(s, 7, region, T+timedelta(minutes=5), 0)
        self.assertEqual(count, 0)
        self.assertEqual(repeated['world_v024']['memories'], 1)
        tomorrow = T+timedelta(days=1)
        _, count, _ = world.exploration(s, 7, world.chapter(7, tomorrow)['region'], tomorrow, 0)
        self.assertEqual(count, 2)

    def test_rare_chance_boundary_and_wrong_region(self):
        featured = world.chapter(7, T)['region']
        other = next(k for k in living_dao.REGIONS if k != featured)
        self.assertEqual(world.exploration({}, 7, other, T, 79)[1], 1)
        self.assertEqual(world.exploration({}, 7, other, T, 80)[1], 0)

    def test_invalid_world_rolls_do_not_change_input(self):
        s = {}
        for roll in (-1, 1000, True, 0.1):
            with self.assertRaises(ValueError):
                world.exploration(s, 7, 'mistwood', T, roll)
        self.assertEqual(s, {})

    def test_three_beginner_epiphanies_lifetime_then_no_extra_qi(self):
        s = character()
        before = s['player']['qi'] + s['eternal']['bank_qi']
        for i in range(5):
            reply = practice(s, 'Earth', T+timedelta(hours=6*i), roll=999)
            self.assertEqual('Beginner epiphany' in reply, i < 3)
        self.assertEqual(s['player']['qi']+s['eternal']['bank_qi']-before, 36)
        self.assertEqual(s['player']['stage'], 1)
        self.assertEqual(s['living_dao_v024']['epiphanies'], 3)

    def test_late_realms_receive_no_new_qi_acceleration(self):
        for stage, foundation, golden in ((4, False, False), (1, True, False), (1, True, True)):
            s = character()
            s['player'].update(stage=stage, advanced_to_foundation=foundation)
            s['golden_core'] = golden
            world.practice_story(s, 'Earth', T, 999, 80)
            self.assertEqual(s['eternal']['bank_qi'], 0)
            self.assertNotIn('epiphanies', s['living_dao_v024'])

    def test_practice_memory_preserves_existing_boon_and_has_readable_text(self):
        s = character()
        first = practice(s, 'Earth', T, 0)
        from heavens import add_boon
        add_boon(s, T+timedelta(hours=6), 'legendary')
        boon = deepcopy(s['personal_boons'])
        self.assertIn('Dao memory: A white deer', first)
        self.assertNotIn('D\na\no', first)
        practice(s, 'Earth', T+timedelta(hours=6), 0)
        self.assertEqual(s['personal_boons'], boon)
        self.assertEqual(s['living_dao_v024']['memories'], 2)


class SectProjectTests(unittest.TestCase):
    def complete(self):
        s = {}
        for user, activity in ((1, 'patrol'), (2, 'archives'), (1, 'gardens')):
            s, _ = projects.record_commission(s, user, activity, T)
        return s

    def test_requires_distinct_people_and_all_three_activities(self):
        s = {}
        for activity in projects.ACTIVITIES:
            s, _ = projects.record_commission(s, 1, activity, T)
        self.assertFalse(s['project_v024']['complete'])
        s, reply = projects.record_commission(s, 2, 'patrol', T)
        self.assertTrue(s['project_v024']['complete'])
        self.assertIn('+15 sect renown', reply)
        self.assertEqual(s['project_vouchers_v024'], {'1': 2, '2': 2})

    def test_shared_reward_once_per_day_and_claim_once(self):
        s = self.complete()
        s, _ = projects.record_commission(s, 3, 'gardens', T)
        self.assertEqual(s['renown'], 15)
        self.assertNotIn('3', s['project_vouchers_v024'])
        after, amount = projects.claim(s, 1)
        self.assertEqual(amount, 2)
        self.assertEqual(projects.claim(after, 1)[1], 0)
        self.assertEqual(projects.claim(after, 2)[1], 2)
        self.assertEqual(s['project_vouchers_v024']['1'], 2)

    def test_old_vouchers_survive_daily_reset_and_absence(self):
        s = self.complete()
        s, _ = projects.record_commission(s, 3, 'patrol', T+timedelta(days=90))
        self.assertFalse(s['project_v024']['complete'])
        self.assertEqual(projects.claim(s, 1)[1], 2)

    def test_treasury_halls_unknown_fields_and_input_preserved(self):
        original = {'spent': 100, 'levels': {'martial': 4}, 'future': {'x': [1]}}
        before = deepcopy(original)
        after, _ = projects.record_commission(original, 1, 'patrol', T)
        self.assertEqual(original, before)
        for key in before:
            self.assertEqual(after[key], before[key])

    def test_invalid_activity_cannot_fill_the_project(self):
        with self.assertRaises(ValueError):
            projects.record_commission({}, 1, 'paid', T)


class LivingDaoHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_victories_pay_twenty_total_first_clear_is_not_repeated(self):
        db = CombatDatabase()
        db.living = None
        repo = db.repository()
        with patch.object(combat_store, 'datetime', Clock):
            await combat_store.execute(repo, 7, 101, 1, 'start', mode='auto')
            await combat_store.execute(repo, 7, 101, 2, 'auto')
            await combat_store.execute(repo, 7, 101, 3, 'recover')
            await combat_store.execute(repo, 7, 101, 4, 'start')
            await combat_store.execute(repo, 7, 101, 5, 'auto')
        self.assertEqual(db.stock['marks'], 20)
        self.assertEqual(db.stock['holdings']['wolf_fang'], 2)
        self.assertEqual(db.battle['trail_v024']['wins'], 2)

    async def test_duplicate_retreat_and_recovery_never_award_resources(self):
        db = CombatDatabase()
        repo = db.repository()
        with patch.object(combat_store, 'datetime', Clock):
            await combat_store.execute(repo, 7, 101, 1, 'start')
            await combat_store.execute(repo, 7, 101, 2, 'turn', action='strike')
            replies = await asyncio.gather(*(combat_store.execute(repo, 7, 101, 3, 'retreat') for _ in range(2)))
            self.assertEqual(replies[0], replies[1])
            replies = await asyncio.gather(*(combat_store.execute(repo, 7, 101, 4, 'recover') for _ in range(2)))
            self.assertEqual(replies[0], replies[1])
        self.assertEqual(db.stock['marks'], 0)
        self.assertEqual(db.stock['holdings'], {})
        self.assertEqual(db.battle['trail_v024']['first_clears'], {})
        self.assertEqual(db.battle['status'], 'retreated')

    async def test_old_battle_panel_cannot_spend_a_new_hunts_turn(self):
        db = CombatDatabase()
        repo = db.repository()
        await combat_store.execute(repo, 7, 101, 10, 'start')
        before = deepcopy(db.battle)
        for stale in (9, 'legacy'):
            with self.assertRaisesRegex(ValueError, 'earlier hunt'):
                await combat_store.execute(repo, 7, 101, 11, 'turn', action='strike', expected_battle_id=stale)
            self.assertEqual(db.battle, before)
        await combat_store.execute(repo, 7, 101, 12, 'turn', action='strike', expected_battle_id=10)
        self.assertEqual(db.battle['turn'], 1)

    async def test_legacy_active_battle_can_finish_without_hp_reset(self):
        db = CombatDatabase()
        db.battle = combat.begin()
        db.battle['hp'] = 10
        await combat_store.execute(db.repository(), 7, 101, 1, 'retreat', expected_battle_id='legacy')
        self.assertEqual(db.battle['trail_v024']['hp'], 10)
        self.assertEqual(db.stock['marks'], 0)

    async def test_attunement_snapshot_bonuses_are_bounded(self):
        for key, field, expected in (('storm_hawk', 'qi', 28), ('stone_tortoise', 'gear_ward', 2)):
            db = CombatDatabase()
            db.living, _ = bonds.bond_action(companion(key), 'listen', T, 999)
            with patch.object(combat_store, 'datetime', Clock):
                await combat_store.execute(db.repository(), 7, 101, 1, 'start')
            self.assertEqual(db.battle[field], expected)
            self.assertTrue(db.battle['beast_attuned'])
            self.assertEqual(db.living['beast_cultivation']['earned'], 6)

    async def test_bond_handler_settles_legacy_and_replays_reward_once(self):
        con = _Connection(living={'companion': 'jade_fox', 'bond': 2, 'future': [1]})
        with patch('datetime.datetime', Clock), patch('secrets.randbelow', return_value=999) as draw:
            reply = await beast_v022.action(_repo(con), 7, 1, 12, 'bond', 'listen')
            repeated = await beast_v022.action(_repo(con), 7, 1, 12, 'bond', 'listen')
        self.assertEqual(reply, repeated)
        draw.assert_called_once_with(1000)
        self.assertEqual(con.living['beast_cultivation']['earned'], 6)
        self.assertEqual(con.living['beast_cultivation']['qi'], 6)
        self.assertEqual(con.living['bond'], 2)
        self.assertEqual(con.living['future'], [1])
        self.assertEqual(con.marks, 100)

    async def test_project_claim_duplicate_does_not_double_pay_and_uses_lock_order(self):
        con = _Connection(hall={'project_vouchers_v024': {'1': 2}})
        reply = await sect_halls.act(_repo(con), 7, 1, 12, 'project_claim')
        self.assertIn('Claimed 2', reply)
        self.assertEqual(await sect_halls.act(_repo(con), 7, 1, 12, 'project_claim'), reply)
        self.assertEqual(con.marks, 102)
        calls = [sql for sql, _ in con.calls]
        inventory_lock = next(i for i, sql in enumerate(calls) if 'FROM player_inventory' in sql)
        sect_lock = next(i for i, sql in enumerate(calls) if 'FROM living_dao_sects' in sql)
        self.assertLess(inventory_lock, sect_lock)

    async def test_commission_completion_grants_vouchers_atomically_with_existing_reward(self):
        prior, _ = projects.record_commission({}, 1, 'patrol', T)
        prior, _ = projects.record_commission(prior, 2, 'archives', T)
        con = _Connection(hall=prior)
        with patch.object(sect_halls, 'datetime', Clock):
            reply = await sect_halls.act(_repo(con), 7, 1, 12, 'commission', 'gardens')
        self.assertIn('Shared project completed', reply)
        self.assertEqual((con.hall['renown'], con.marks), (20, 106))
        self.assertEqual(con.hall['project_vouchers_v024'], {'1': 2, '2': 2})

    async def test_world_exploration_replay_keeps_single_story_and_reward(self):
        con = _Connection(living={'companion': None})
        region = world.chapter(7, T)['region']
        with patch.object(living_dao, 'datetime', Clock), patch('secrets.randbelow', return_value=0) as draw:
            reply = await living_dao.action(_repo(con), 7, 1, 12, 'explore', region)
            repeated = await living_dao.action(_repo(con), 7, 1, 12, 'explore', region)
        self.assertEqual(reply, repeated)
        draw.assert_called_once_with(1000)
        self.assertIn(world.chapter(7, T)['name'], reply)
        self.assertNotIn('R\na\nr\ne', reply)
        saved = next(args for sql, args in con.calls if sql.startswith('UPDATE player_inventory'))
        self.assertEqual(json.loads(saved[2])[living_dao.REGIONS[region][2]], 3)


class GuidanceTests(unittest.TestCase):
    def test_next_step_tracks_existing_setup_and_never_mutates(self):
        s = character()
        self.assertIn('/awaken', next_step(s))
        self.assertIn('/choose_path', next_step(s, 'Fire'))
        s['cultivation_path'] = 'Spirit'
        self.assertIn('/cultivation_focus', next_step(s, 'Fire'))
        s['cultivation_v023'] = {'focus': 'insight'}
        before = deepcopy(s)
        self.assertIn('/hunt', next_step(s, 'Fire'))
        self.assertEqual(s, before)

    def test_guidance_fits_discord_and_preserves_manual_progression_commands(self):
        for living in ({}, {'companion': 'jade_fox'}):
            text = guide(character(), 'Fire', living, {'marks': 4})
            self.assertLessEqual(len(text), 2000)
            self.assertNotIn('/advance ', text)
            for command in ('/breakthrough', '/hunt', '/recover', '/sect_project', '/cultivation_focus'):
                self.assertIn(command, text)
        self.assertIn('/start', guide(None))


if __name__ == '__main__':
    unittest.main()
