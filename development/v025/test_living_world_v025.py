"""Deterministic authored-arc rules and transactional handler doubles.

These tests execute no SQL and prove no PostgreSQL runtime behavior. The doubles
serialize synthetic records and restore them on failure, making replay, stale
choice and reward invariants reviewable offline.
"""
import asyncio
import itertools
import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import combat
import combat_store
import living_dao
import living_world_v025 as world
import shattered_heavens_v023 as cultivation
from game import default_state
from heavens import ALL_ROOTS, relevant_root
from test_living_dao_stable import ProgressConnection
from test_persistence_regressions import CombatDatabase, CombatConnection


T = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return T


def prepare_choice(state=None, chapter=0):
    state = deepcopy(state or {})
    for number in range(chapter):
        state, _ = world.encounter(state, world.CHAPTERS[number]['region'], T, 999)
        state, _, _ = world.choose(state, number, world.CHAPTERS[number]['choices'][0][0], T)
    state, _ = world.encounter(state, world.CHAPTERS[chapter]['region'], T, 999)
    return state


class ArcRuleTests(unittest.TestCase):
    def test_legacy_status_is_read_only_and_points_to_mistwood(self):
        old = {'companion': 'jade_fox', 'bond': 3, 'future': {'keep': [1, 2]}}
        before = deepcopy(old)
        self.assertIn('region:Mistwood Vale', world.status(old))
        self.assertNotIn('mistwood', world.status(old))
        self.assertEqual(world.panel(old)['choices'], [])
        self.assertEqual(old, before)

    def test_wrong_region_cannot_unlock_choices(self):
        state, text = world.encounter({}, 'stonepeak', T, 999)
        self.assertIn('toward Mistwood Vale', text)
        self.assertNotIn(world.CHAPTERS[0]['text'], text)
        self.assertFalse(world.panel(state)['ready'])
        with self.assertRaisesRegex(ValueError, 'Explore Mistwood Vale'):
            world.choose(state, 0, 'return_jade', T)

    def test_region_proof_opens_three_authored_choices(self):
        state = prepare_choice()
        view = world.panel(state)
        self.assertEqual(view['chapter'], 0)
        self.assertTrue(view['ready'])
        self.assertEqual(len(view['choices']), 3)
        self.assertEqual(view['choices'][0]['key'], 'return_jade')
        self.assertIn('former Heavenly Palace guardian', world.NPCS['jian']['history'])

    def test_forged_or_wrong_region_proof_cannot_advance(self):
        state = {'living_world_v025': {'chapter': 0, 'encounter': {'chapter': 0, 'region': 'stonepeak'}}}
        with self.assertRaisesRegex(ValueError, 'Explore Mistwood Vale'):
            world.choose(state, 0, 'return_jade', T)

    def test_stale_chapter_and_invalid_choice_do_not_mutate(self):
        state = prepare_choice()
        before = deepcopy(state)
        for chapter, choice in ((1, 'follow_yue'), (0, 'invented')):
            with self.assertRaises(ValueError):
                world.choose(state, chapter, choice, T)
            self.assertEqual(state, before)

    def test_same_chapter_cannot_pay_after_resolution(self):
        state, reply, reward = world.choose(prepare_choice(), 0, 'return_jade', T)
        self.assertEqual(reward, 3)
        self.assertIn('+3 saved Spirit Stones', reply)
        with self.assertRaisesRegex(ValueError, 'already resolved'):
            world.choose(state, 0, 'trace_script', T)

    def test_one_expedition_never_proves_next_chapter(self):
        state, _, _ = world.choose(prepare_choice(), 0, 'trace_script', T)
        self.assertFalse(world.panel(state)['ready'])
        with self.assertRaisesRegex(ValueError, 'Explore Stonepeak Sanctuary'):
            world.choose(state, 1, 'follow_yue', T)

    def test_all_27_paths_complete_for_solo_f2p_with_exactly_nine_marks(self):
        for sequence in itertools.product(range(3), repeat=3):
            state = {}
            total = 0
            for chapter, option in enumerate(sequence):
                state, _ = world.encounter(state, world.CHAPTERS[chapter]['region'], T, 999)
                key = world.CHAPTERS[chapter]['choices'][option][0]
                state, _, marks = world.choose(state, chapter, key, T)
                total += marks
            self.assertEqual(total, 9)
            self.assertTrue(world.panel(state)['complete'])
            self.assertEqual(world.panel(state)['choices'], [])
            self.assertEqual(state['living_world_v025']['fragments'], 3)
            self.assertIn('no essential content is locked', world.panel(state)['text'])

    def test_npc_trust_and_structured_memories_are_bounded(self):
        story = {'npcs': {'jian': {'trust': 12, 'future': 'keep',
                                  'memories': [{'choice': 'old'}] * 15}}}
        state = prepare_choice({'living_world_v025': story})
        state, _, _ = world.choose(state, 0, 'return_jade', T)
        npc = state['living_world_v025']['npcs']['jian']
        self.assertEqual(npc['trust'], 12)
        self.assertEqual(len(npc['memories']), 6)
        self.assertEqual(npc['future'], 'keep')
        self.assertEqual(npc['memories'][-1]['chapter'], 0)
        self.assertEqual(npc['memories'][-1]['at'], T.isoformat())

    def test_saved_choices_change_future_authored_dialogue(self):
        for choice, text in (('return_jade', 'integrity'), ('trace_script', 'read beneath'),
                             ('share_credit', 'rival his due')):
            state, _, _ = world.choose(prepare_choice(), 0, choice, T)
            self.assertEqual(world.panel(state)['text'], '')  # Shown with the next chapter once its proof is found.
            ready, _ = world.encounter(state, 'stonepeak', T, 999)
            self.assertIn(text, world.panel(ready)['text'])

    def test_resolution_changes_future_exploration_without_rewards(self):
        outcomes = []
        for choice in ('restore_beacon', 'open_archive', 'reconcile'):
            state, _, _ = world.choose(prepare_choice(chapter=2), 2, choice, T)
            after, text = world.encounter(state, 'mistwood', T, 999)
            outcomes.append(text)
            self.assertEqual(after['living_world_v025']['choices']['2'], choice)
            self.assertEqual(after['living_world_v025']['fragments'], 3)
        self.assertEqual(len(set(outcomes)), 3)

    def test_choice_preserves_old_beast_and_unknown_fields(self):
        old = {'companion': 'jade_fox', 'bond': 4, 'beast_cultivation': {'qi': 441, 'bank': 94,
               'earned': 333, 'evolutions': 2}, 'companion_v024': {'trust': 18, 'memories': 7},
               'future': {'schema': [8]}, 'sect': 'Heavenly Dao', 'progress': {'explore': 41}}
        state = prepare_choice(old)
        state, text, _ = world.choose(state, 0, 'return_jade', T, 'wisdom')
        for key, value in old.items():
            self.assertEqual(state[key], value)
        self.assertIn('Jade Fox', text)
        self.assertIn('careful study', text)

    def test_companion_personality_and_doctrine_have_no_economic_bonus(self):
        reactions = []
        for companion, doctrine in (('jade_fox', 'wisdom'), ('storm_hawk', 'valor'),
                                     ('stone_tortoise', 'harmony')):
            state = prepare_choice({'companion': companion, 'sect': 'One'})
            _, reply, reward = world.choose(state, 0, 'return_jade', T, doctrine)
            reactions.append(reply)
            self.assertEqual(reward, 3)
        self.assertEqual(len(set(reactions)), 3)

    def test_daily_lore_discoveries_are_cosmetic_bounded_and_utc(self):
        state, text = world.encounter({}, 'mistwood', T, 0)
        self.assertIn('lore only', text)
        state, again = world.encounter(state, 'stonepeak', T + timedelta(minutes=5), 0)
        self.assertNotIn('Rare discovery', again)
        self.assertEqual(state['living_world_v025']['lore_count'], 1)
        state, text = world.encounter(state, 'thunder_rift', T + timedelta(days=1), 0)
        self.assertIn('Rare discovery', text)
        self.assertEqual(state['living_world_v025']['lore_count'], 2)
        self.assertNotIn('marks', state)
        self.assertNotIn('holdings', state)

    def test_lore_count_never_exceeds_thirty(self):
        state = {'living_world_v025': {'lore_count': 30}}
        state, _ = world.encounter(state, 'mistwood', T, 0)
        self.assertEqual(state['living_world_v025']['lore_count'], 30)

    def test_invalid_roll_and_naive_time_rejected_without_mutation(self):
        for value in (-1, 1000, True, '0'):
            with self.assertRaises(ValueError):
                world.encounter({}, 'mistwood', T, value)
        with self.assertRaises(ValueError):
            world.encounter({}, 'mistwood', T.replace(tzinfo=None), 0)

    def test_panels_and_story_results_fit_discord_message_budget(self):
        state = {'companion': 'jade_fox', 'sect': 'A' * 30}
        for chapter in range(3):
            state, scene = world.encounter(state, world.CHAPTERS[chapter]['region'], T, 0, 'wisdom')
            self.assertLess(len(scene), 1400)
            self.assertLess(len(world.status(state)), 1800)
            state, reply, _ = world.choose(state, chapter, world.CHAPTERS[chapter]['choices'][0][0], T, 'wisdom')
            self.assertLess(len(reply), 1800)


class TechniqueTests(unittest.TestCase):
    def setUp(self):
        self.state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'train_zhao', T)

    def test_legacy_or_unawakened_has_no_new_combat_power(self):
        self.assertIsNone(world.technique({}, {}, 'Fire'))
        self.assertIsNone(world.technique(self.state, {}, 'Unawakened'))

    def test_every_root_uses_existing_rare_correspondence(self):
        for root in ALL_ROOTS:
            form = world.technique(self.state, {}, root)
            self.assertEqual(form['root'], relevant_root(root))
            self.assertIn(root, form['name'])

    def test_practice_growth_capped_at_nine_and_form_at_three(self):
        char = {'dao_practice_v025': {'future': 'keep'}}
        for number in range(20):
            world.practice_growth(char, 'Fire', T + timedelta(hours=6 * number))
        self.assertEqual(char['dao_practice_v025']['practices'], 9)
        self.assertEqual(char['dao_practice_v025']['future'], 'keep')
        self.assertEqual(world.technique(self.state, char, 'Fire')['rank'], 3)
        self.assertNotIn('eternal', char)

    def test_three_archive_choices_give_distinct_equal_bounded_forms(self):
        for choice, style in (('protect_archive', 'guard'), ('follow_yue', 'flow'),
                              ('train_zhao', 'strike')):
            state, _, _ = world.choose(prepare_choice(chapter=1), 1, choice, T)
            form = world.technique(state, {}, 'Fire')
            self.assertEqual((form['style'], form['rank']), (style, 1))

    def battle(self, style, rank=3):
        return combat.begin('stone_guardian', 'Fire', gear={'dao_technique': {
            'name': 'Fire · Personal Form', 'style': style, 'rank': rank}})

    def test_damage_form_triggers_only_once_on_primary_art(self):
        base = combat.begin('stone_guardian', 'Fire')
        form = self.battle('strike')
        base = combat.turn(base, 'elemental_art')
        form = combat.turn(form, 'elemental_art')
        self.assertEqual(base['enemy_hp'] - form['enemy_hp'], 3)
        self.assertTrue(form['dao_technique_used'])
        self.assertIn('once this battle', form['log'][-1])
        before = form['enemy_hp']
        form = combat.turn(form, 'elemental_art')
        self.assertEqual(before - form['enemy_hp'], 16)

    def test_basic_and_secondary_art_do_not_consume_personal_form(self):
        form = self.battle('strike')
        form['secondary_root'] = 'Water'
        for move in ('strike', 'secondary_art'):
            form = combat.turn(form, move)
            self.assertFalse(form['dao_technique_used'])
        form = combat.turn(form, 'elemental_art')
        self.assertTrue(form['dao_technique_used'])

    def test_patient_edge_wards_the_next_hit_by_two_per_rank_once(self):
        for rank in (1, 2, 3):
            plain = combat.turn(self.battle(None, rank), 'elemental_art')
            form = combat.turn(self.battle('guard', rank), 'elemental_art')
            self.assertEqual(form['last_action']['received'], plain['last_action']['received'] - 2 * rank)
            self.assertEqual(form['last_action']['healed'], 0)  # A ward, never a heal.
            self.assertIn(f'ward blocks {2 * rank} damage', form['log'][-1])
            self.assertEqual(form['dao_ward'], 0)
            again = combat.turn(form, 'elemental_art')
            self.assertNotIn('ward', again['log'][-1])
            self.assertNotIn('eternal', form)

    def test_patient_edge_ward_never_blocks_more_than_the_hit(self):
        form = self.battle('guard', 3)
        form['gear_ward'] = 99  # The hit is already reduced to its minimum of 1.
        result = combat.turn(form, 'elemental_art')
        self.assertIn('ward blocks 1 damage', result['log'][-1])
        self.assertEqual(result['last_action']['received'], 0)

    def test_flow_form_refunds_at_most_three_combat_qi_once(self):
        form = self.battle('flow')
        result = combat.turn(form, 'elemental_art')
        self.assertEqual(result['qi'], form['qi'] - 6 + 3)
        self.assertEqual(result['last_action']['qi_delta'], -3)

    def test_untrusted_oversized_rank_is_clamped_in_combat(self):
        base = combat.turn(combat.begin('stone_guardian', 'Fire'), 'elemental_art')
        form = combat.turn(self.battle('strike', 999), 'elemental_art')
        self.assertEqual(base['enemy_hp'] - form['enemy_hp'], 3)

    def test_practice_cooldown_rejects_growth_without_mutating_accepted_state(self):
        char = default_state(T)
        cultivation.practice(char, 'Fire', T, roll=999)
        before = deepcopy(char)
        with self.assertRaisesRegex(ValueError, 'ready'):
            cultivation.practice(char, 'Fire', T + timedelta(minutes=1), roll=999)
        self.assertEqual(char, before)
        self.assertEqual(char['dao_practice_v025']['practices'], 1)

    def test_new_practice_field_does_not_replace_eternal_or_existing_history(self):
        char = default_state(T)
        char['future'] = {'keep': [1]}
        char['cultivation_v023'] = {'mastery': {'Fire': 10}, 'windows': [], 'practices': 20}
        cultivation.practice(char, 'Fire', T, roll=999)
        self.assertEqual(char['cultivation_v023']['practices'], 21)
        self.assertEqual(char['cultivation_v023']['mastery']['Fire'], 14)
        self.assertEqual(char['future'], {'keep': [1]})
        self.assertGreaterEqual(char['eternal']['bank_qi'], 0)


class StoryConnection(ProgressConnection):
    async def fetchval(self, sql, *args):
        if sql.startswith('SELECT data FROM sect_hall_state'):
            return json.dumps(self.hall)
        return await super().fetchval(sql, *args)


class StoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def database(self):
        db = StoryConnection()
        db.living[1].update(companion='jade_fox', bond=2,
            beast_cultivation={'stage': 1, 'qi': 10, 'bank': 5, 'earned': 20,
                               'last_at': T.isoformat(), 'remainder': 0, 'evolutions': 1})
        db.hall['doctrine'] = 'wisdom'
        return db

    async def explore(self, db, event=1, region='mistwood', now=T, roll=999):
        clock = type('InjectedClock', (Clock,), {'now': classmethod(lambda cls, tz=None: now)})
        with patch.object(living_dao, 'datetime', clock), patch('secrets.randbelow', return_value=roll):
            return await living_dao.action(db.repository(), 7, 1, event, 'explore', region)

    async def choose(self, db, event=2, chapter=0, choice='return_jade'):
        with patch.object(living_dao, 'datetime', Clock):
            return await world.choose_story(db.repository(), 7, 1, event, chapter, choice)

    async def test_exploration_stores_proof_and_preserves_old_earned_rewards(self):
        db = self.database()
        reply = await self.explore(db)
        self.assertTrue(world.panel(db.living[1])['ready'])
        self.assertEqual(db.stocks[1]['marks'], 3)
        self.assertIn('a new chapter awaits', reply)
        self.assertNotIn(world.CHAPTERS[0]['text'], reply)  # The blurb belongs to the story panel.
        self.assertTrue(any(line in reply for line in world.DOCTRINE_REACTIONS['wisdom'][0]))
        self.assertEqual(db.living[1]['bond'], 2)
        self.assertEqual(db.living[1]['beast_cultivation']['evolutions'], 1)

    async def test_same_event_concurrent_story_delivery_pays_one_reward(self):
        db = self.database()
        await self.explore(db)
        replies = await asyncio.gather(*(self.choose(db) for _ in range(2)))
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(db.stocks[1]['marks'], 6)
        self.assertEqual(db.living[1]['living_world_v025']['npcs']['jian']['trust'], 3)
        self.assertEqual(db.living[1]['living_world_v025']['chapter'], 1)

    async def test_new_event_cannot_replay_resolved_or_change_choice(self):
        db = self.database()
        await self.explore(db)
        await self.choose(db)
        before = deepcopy((db.living, db.stocks, db.receipts))
        with self.assertRaisesRegex(ValueError, 'already resolved'):
            await self.choose(db, event=3, choice='trace_script')
        self.assertEqual((db.living, db.stocks, db.receipts), before)

    async def test_two_distinct_simultaneous_choices_only_one_can_commit(self):
        db = self.database()
        await self.explore(db)
        results = await asyncio.gather(self.choose(db), self.choose(db, event=3, choice='trace_script'),
                                       return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(db.stocks[1]['marks'], 6)
        self.assertEqual(len(db.living[1]['living_world_v025']['choices']), 1)

    async def test_receipt_failure_rolls_back_choice_trust_and_marks_then_retry_works(self):
        db = self.database()
        await self.explore(db)
        before = deepcopy((db.living, db.stocks, db.receipts))
        db.fail_on = 'INSERT INTO living_dao_actions'
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            await self.choose(db)
        self.assertEqual((db.living, db.stocks, db.receipts), before)
        db.fail_on = None
        await self.choose(db)
        self.assertEqual(db.stocks[1]['marks'], 6)

    async def test_inventory_failure_leaves_no_half_saved_story(self):
        db = self.database()
        await self.explore(db)
        before = deepcopy((db.living, db.stocks, db.receipts))
        db.fail_on = 'UPDATE player_inventory'
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            await self.choose(db)
        self.assertEqual((db.living, db.stocks, db.receipts), before)

    async def test_exploration_replay_has_no_reroll_or_second_lore_reward(self):
        db = self.database()
        first = await self.explore(db, roll=0)
        with patch('secrets.randbelow') as draw:
            repeated = await living_dao.action(db.repository(), 7, 1, 1, 'explore', 'mistwood')
        self.assertEqual(first, repeated)
        draw.assert_not_called()
        self.assertEqual(db.living[1]['living_world_v025']['lore_count'], 1)
        self.assertEqual(db.living[1]['world_v024']['memories'], 1)

    async def test_cooldown_cannot_be_bypassed_by_story_progress(self):
        db = self.database()
        await self.explore(db)
        await self.choose(db)
        with self.assertRaisesRegex(ValueError, 'recovering'):
            await self.explore(db, event=3, region='stonepeak', now=T + timedelta(seconds=1))
        self.assertFalse(world.panel(db.living[1])['ready'])

    async def test_invalid_choice_adapter_payload_never_awards(self):
        db = self.database()
        for value in ('nonsense', 'z:follow_yue', None):
            with self.assertRaises(ValueError):
                await living_dao.action(db.repository(), 7, 1, 2, 'story_choice', value)
        self.assertEqual(db.stocks[1]['marks'], 0)

    async def test_whole_arc_persists_without_schema_or_treasury_changes(self):
        db = self.database()
        old_hall, old_treasury = deepcopy(db.hall), db.treasury
        for chapter in range(3):
            await self.explore(db, event=chapter * 2 + 1, region=world.CHAPTERS[chapter]['region'],
                               now=T + timedelta(minutes=5 * chapter))
            await self.choose(db, event=chapter * 2 + 2, chapter=chapter,
                              choice=world.CHAPTERS[chapter]['choices'][0][0])
        self.assertTrue(world.panel(db.living[1])['complete'])
        self.assertEqual(db.stocks[1]['marks'], 3 + 6 + 10 + 9)
        self.assertEqual((db.hall, db.treasury), (old_hall, old_treasury))
        self.assertEqual(db.living[1]['future'], {'keep': True})


class TechniqueSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_story_form_is_snapshotted_without_spending_old_beast_progress(self):
        db = CombatDatabase()
        db.living, _, _ = world.choose(prepare_choice(db.living, chapter=1), 1, 'train_zhao', T)
        before = deepcopy(db.living)
        with patch.object(combat_store, 'datetime', Clock):
            await combat_store.execute(db.repository(), 7, 101, 1, 'start')
        self.assertEqual(db.battle['dao_technique']['style'], 'strike')
        self.assertEqual(db.battle['dao_technique']['rank'], 1)
        self.assertFalse(db.battle['dao_technique_used'])
        self.assertEqual(db.living['living_world_v025'], before['living_world_v025'])
        form = deepcopy(db.battle['dao_technique'])
        db.living['living_world_v025']['technique_style'] = 'flow'
        with patch.object(combat_store, 'datetime', Clock):
            await combat_store.execute(db.repository(), 7, 101, 2, 'turn', action='elemental_art')
        self.assertEqual(db.battle['dao_technique'], form)
        self.assertTrue(db.battle['dao_technique_used'])


class TeacherTests(unittest.TestCase):
    def setUp(self):
        self.state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'protect_archive', T)

    def test_all_teachers_available_without_trust_or_multiplayer_requirement(self):
        info = world.training_panel(self.state, T)
        self.assertTrue(info['unlocked'])
        self.assertTrue(info['ready'])
        self.assertEqual([item['key'] for item in info['teachers']], ['jian', 'yue', 'zhao'])

    def test_before_archive_training_is_unavailable_without_mutation(self):
        state = prepare_choice()
        before = deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'Stonepeak'):
            world.train(state, 'jian', T)
        self.assertEqual(state, before)

    def test_sword_lesson_retunes_form_preserving_saved_story_choices(self):
        old = deepcopy(self.state['living_world_v025']['choices'])
        state, reply = world.train(self.state, 'jian', T)
        self.assertIn('guardian form', reply)
        self.assertEqual(state['living_world_v025']['technique_style'], 'guard')
        self.assertEqual(state['living_world_v025']['choices'], old)

    def test_yue_research_offers_an_alternative_form_without_erasing_history(self):
        state, reply = world.train(self.state, 'yue', T)
        self.assertIn('Both histories remain available', reply)
        self.assertEqual(state['living_world_v025']['technique_style'], 'flow')
        self.assertEqual(state['living_world_v025']['npcs']['yue']['memories'][-1]['kind'], 'training')

    def test_zhao_duel_is_three_deterministic_nonlethal_exchanges(self):
        order = world.duel_order(T)
        state, reply = world.train(self.state, 'zhao', T, [world.COUNTERS[stance] for stance in order])
        self.assertIn('3/3 clean counters', reply)
        for number, stance in enumerate(order, 1):
            self.assertIn(f'{number}. Zhao {stance.title()}', reply)
        self.assertIn('No injuries', reply)
        self.assertEqual(state['living_world_v025']['technique_style'], 'strike')
        self.assertEqual(state['living_world_v025']['npcs']['zhao']['trust'], 3)  # One archive trust + two lesson.

    def test_unsuccessful_duel_has_learning_and_no_loss(self):
        old = {'beast_cultivation': {'qi': 500, 'evolutions': 2}, 'bond': 4}
        self.state.update(old)
        wrong = [next(move for move in world.COUNTERS.values() if move != world.COUNTERS[stance])
                 for stance in world.duel_order(T)]
        state, reply = world.train(self.state, 'zhao', T, wrong)
        self.assertIn('0/3 clean counters', reply)
        self.assertIn('no defeat penalty', reply)
        for key, value in old.items():
            self.assertEqual(state[key], value)
        self.assertNotIn('marks', state)

    def test_shared_daily_limit_blocks_teacher_hopping_and_resets_in_utc(self):
        state, _ = world.train(self.state, 'jian', T)
        self.assertFalse(world.training_panel(state, T)['ready'])
        with self.assertRaisesRegex(ValueError, 'next UTC day'):
            world.train(state, 'yue', T + timedelta(hours=1))
        state, _ = world.train(state, 'yue', T + timedelta(days=1))
        self.assertEqual(state['living_world_v025']['training_count'], 2)

    def test_daily_training_trust_memories_and_form_power_remain_bounded(self):
        state = self.state
        for day in range(20):
            state, _ = world.train(state, 'zhao', T + timedelta(days=day), ['guard', 'strike', 'feint'])
        self.assertEqual(state['living_world_v025']['npcs']['zhao']['trust'], 12)
        self.assertEqual(len(state['living_world_v025']['npcs']['zhao']['memories']), 6)
        self.assertEqual(world.technique(state, {}, 'Fire')['rank'], 1)  # Lessons never replace cultivation practice.

    def test_invalid_duel_input_does_not_consume_daily_lesson(self):
        for tactics in (None, [], ['guard'], ['guard', 'strike', 'invalid']):
            with self.assertRaises(ValueError):
                world.train(self.state, 'zhao', T, tactics)
        self.assertTrue(world.training_panel(self.state, T)['ready'])


class TeacherPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_training_replay_changes_trust_and_memory_once_without_inventory_writes(self):
        db = StoryConnection()
        db.living[1], _, _ = world.choose(prepare_choice(chapter=1), 1, 'protect_archive', T)
        before_stock = deepcopy(db.stocks)
        with patch.object(living_dao, 'datetime', Clock):
            replies = await asyncio.gather(*(world.train_story(db.repository(), 7, 1, 10, 'yue') for _ in range(2)))
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(db.living[1]['living_world_v025']['training_count'], 1)
        self.assertEqual(db.stocks, before_stock)

    async def test_training_receipt_failure_rolls_back_daily_limit_and_memory(self):
        db = StoryConnection()
        db.living[1], _, _ = world.choose(prepare_choice(chapter=1), 1, 'protect_archive', T)
        before = deepcopy((db.living, db.stocks, db.receipts))
        db.fail_on = 'INSERT INTO living_dao_actions'
        with patch.object(living_dao, 'datetime', Clock):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                await world.train_story(db.repository(), 7, 1, 10, 'zhao', ['guard', 'strike', 'feint'])
        self.assertEqual((db.living, db.stocks, db.receipts), before)


if __name__ == '__main__':
    unittest.main()


class PolishV0251Tests(unittest.TestCase):
    def test_duel_order_is_daily_deterministic_and_varies(self):
        days = [T + timedelta(days=n) for n in range(30)]
        self.assertEqual(world.duel_order(T), world.duel_order(T.replace(hour=23, minute=59)))
        self.assertGreater(len({world.duel_order(day) for day in days}), 1)
        for day in days:
            self.assertEqual(sorted(world.duel_order(day)), sorted(world.STANCES))

    def test_teacher_panel_gives_todays_tells_not_the_answer(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'train_zhao', T)
        lesson = world.training_panel(state, T)
        self.assertEqual(lesson['tells'], [world.TELLS[stance] for stance in world.duel_order(T)])
        for tell in lesson['tells']:
            self.assertIn(tell, lesson['text'])
        self.assertNotIn('answer with', lesson['text'])
        self.assertNotIn('Guard → Strike → Feint', lesson['text'])

    def test_rewound_chapter_never_pays_twice(self):
        state, _, first = world.choose(prepare_choice(), 0, 'return_jade', T)
        story = state['living_world_v025']
        self.assertEqual((first, story['paid']), (3, [0]))
        story.update(chapter=0, choices={}, fragments=0)  # What the console rewind leaves behind.
        state, _ = world.encounter(state, 'mistwood', T, 999)
        state, reply, again = world.choose(state, 0, 'trace_script', T)
        self.assertEqual(again, 0)
        self.assertIn('already paid once', reply)
        self.assertEqual(state['living_world_v025']['paid'], [0])

    def test_saves_from_before_the_paid_record_count_their_choices_as_paid(self):
        story = world._story({'living_world_v025': {'chapter': 2, 'choices': {'0': 'return_jade', '1': 'follow_yue'}}})
        self.assertEqual(story['paid'], [0, 1])
        story = world._story({'living_world_v025': {'paid': [0, 7, 'x', True, 2]}})
        self.assertEqual(story['paid'], [0, 2])

    def test_player_text_uses_region_names(self):
        state, _, _ = world.choose(prepare_choice(), 0, 'return_jade', T)
        texts = [world.status({}), world.encounter({}, 'thunder_rift', T, 999)[1],
                 world.choose(prepare_choice(), 0, 'return_jade', T)[1], world.status(state)]
        try:
            world.choose(state, 1, 'follow_yue', T)
        except ValueError as error:
            texts.append(str(error))
        for text in texts:
            for raw in ('mistwood', 'stonepeak', 'thunder_rift'):
                self.assertNotIn(raw, text)
        self.assertIn('Next region: Stonepeak Sanctuary', texts[2])
        self.assertEqual(len(texts), 5)

    def test_blurb_is_shown_once_and_repeat_explores_are_short(self):
        state, first = world.encounter({}, 'mistwood', T, 999)
        state, again = world.encounter(state, 'mistwood', T, 999)
        blurb = world.CHAPTERS[0]['text']
        self.assertNotIn(blurb, first)
        self.assertIn('already carry the proof', again)
        self.assertIn(blurb, world.panel(state)['text'])
        self.assertNotIn(blurb, world.panel({})['text'])
        state, _, _ = world.choose(state, 0, 'return_jade', T)
        _, later = world.encounter(state, 'mistwood', T, 999)
        self.assertNotIn(blurb, later)
        self.assertIn('toward Stonepeak Sanctuary', later)

    def test_reaction_pools_are_deterministic_varied_and_bounded(self):
        for pools in (*world.COMPANION_REACTIONS.values(), *world.DOCTRINE_REACTIONS.values()):
            self.assertEqual(len(pools), 3)
            for pool in pools:
                self.assertTrue(3 <= len(pool) <= 4 and len(set(pool)) == len(pool))
        seen = set()
        for explorations in range(12):
            state = {'companion': 'storm_hawk', 'sect': 'S', 'explorations': explorations}
            line = world._reaction(state, 1, 'valor')
            self.assertEqual(line, world._reaction(deepcopy(state), 1, 'valor'))
            self.assertTrue(any(option in line for option in world.COMPANION_REACTIONS['storm_hawk'][1]))
            self.assertTrue(any(option in line for option in world.DOCTRINE_REACTIONS['valor'][1]))
            seen.add(line)
        self.assertGreater(len(seen), 1)
        self.assertEqual(world._reaction({'sect': 'S'}, 9, ['bad']), world._reaction({'sect': 'S'}, 2, None))

    def test_combat_panel_shows_latest_turn_and_opening_advice_only(self):
        from combat_journey_v024 import prepare
        battle = prepare(combat.begin('thunder_serpent', 'Fire'), {}, T, 'manual', 1)
        self.assertIn('Dangerous hunt', combat.display(battle, T))
        first = combat.turn(battle, 'strike')
        second = combat.turn(first, 'strike')
        text = combat.display(second, T)
        self.assertNotIn('Dangerous hunt', text)
        self.assertIn(second['log'][-1], text)
        self.assertNotIn(first['log'][-1], text)
