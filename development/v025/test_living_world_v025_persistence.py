"""Story persistence, corrupted-save tolerance and reward-boundary regressions.

Pure rules plus the existing offline connection doubles; no SQL, socket or
Discord runtime is exercised. JSON round trips mimic JSONB storage.
"""
import json
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import combat_store
import living_dao
import living_world_v025 as world
from game import default_state
from heavens import ALL_ROOTS, RARE, relevant_root
from test_living_world_v025 import Clock, StoryConnection, T, prepare_choice
from test_persistence_regressions import CombatConnection, CombatDatabase


def trip(value):
    return json.loads(json.dumps(value))


def advance(state, chapter, choice, now=T):
    state, _ = world.encounter(state, world.CHAPTERS[chapter]['region'], now, 999)
    state, reply, marks = world.choose(trip(state), chapter, choice, now)
    return trip(state), reply, marks


class ChoiceDeltaTests(unittest.TestCase):
    def test_every_choice_has_exact_trust_deltas_and_one_memory_per_affected_npc(self):
        seen = 0
        for chapter, entry in enumerate(world.CHAPTERS):
            for key, _, _, changes, line in entry['choices']:
                state, reply, marks = world.choose(prepare_choice(chapter=chapter), chapter, key, T)
                before = prepare_choice(chapter=chapter)['living_world_v025']['npcs']
                npcs = state['living_world_v025']['npcs']
                for npc in world.NPCS:
                    old = before.get(npc, {})
                    gain = changes.get(npc, 0)
                    self.assertEqual(npcs.get(npc, {}).get('trust', 0), old.get('trust', 0) + gain, (key, npc))
                    added = len(npcs.get(npc, {}).get('memories', [])) - len(old.get('memories', []))
                    self.assertEqual(added, 1 if gain else 0, (key, npc))
                    if gain:
                        self.assertEqual(npcs[npc]['memories'][-1],
                                         {'chapter': chapter, 'choice': key, 'at': T.isoformat(), 'text': line})
                lead = max(changes, key=changes.get)
                self.assertEqual(sorted(changes.values()).count(changes[lead]), 1)
                self.assertTrue(reply.startswith(world.NPCS[lead]['name'] + ': ' + line))
                self.assertEqual(marks, world.CHAPTER_MARKS)
                seen += 1
        self.assertEqual(seen, 9)

    def test_documented_trust_tables(self):
        expected = {'return_jade': {'jian': 3, 'yue': 1}, 'trace_script': {'yue': 3, 'jian': 1},
                    'share_credit': {'zhao': 3, 'jian': 1}, 'protect_archive': {'jian': 3, 'zhao': 1},
                    'follow_yue': {'yue': 3, 'jian': 1}, 'train_zhao': {'zhao': 3, 'yue': 1},
                    'restore_beacon': {'jian': 3, 'yue': 1, 'zhao': 1},
                    'open_archive': {'yue': 3, 'jian': 1, 'zhao': 1},
                    'reconcile': {'zhao': 3, 'jian': 2, 'yue': 2}}
        actual = {key: changes for entry in world.CHAPTERS for key, _, _, changes, _ in entry['choices']}
        self.assertEqual(actual, expected)


class RoundTripTests(unittest.TestCase):
    def test_relationships_accumulate_across_chapters_through_json_storage(self):
        state = {}
        for chapter, choice in enumerate(('return_jade', 'follow_yue', 'reconcile')):
            state, _, _ = advance(state, chapter, choice)
            view = world.panel(state)
            self.assertEqual(view['chapter'], min(chapter + 1, 3))
            self.assertIn('Jian Wuchen', world.status(state))
        story = state['living_world_v025']
        self.assertEqual({k: v['trust'] for k, v in story['npcs'].items()}, {'jian': 6, 'yue': 6, 'zhao': 3})
        self.assertEqual([m['chapter'] for m in story['npcs']['jian']['memories']], [0, 1, 2])
        self.assertEqual(story['choices'], {'0': 'return_jade', '1': 'follow_yue', '2': 'reconcile'})
        self.assertEqual(world.relationship(story, 'jian'), ('Trusted ally', 6))
        self.assertEqual(world.relationship(story, 'zhao'), ('Growing trust', 3))
        self.assertTrue(world.panel(state)['complete'])
        self.assertIn('promise to return as teachers', world.panel(state)['text'])

    def test_proof_and_remembered_dialogue_survive_round_trip(self):
        state, _, _ = advance({}, 0, 'trace_script')
        state, _ = world.encounter(state, 'stonepeak', T, 999)
        state = trip(state)
        view = world.panel(state)
        self.assertTrue(view['ready'])
        self.assertIn('read beneath', view['text'])
        self.assertEqual(len(view['choices']), 3)
        state, _, _ = world.choose(state, 1, 'train_zhao', T)
        self.assertIn('breathing held the passage', world.panel(trip(state))['text'])

    def test_string_or_float_saved_chapter_is_normalized(self):
        state, _ = world.encounter({}, 'mistwood', T, 999)
        state['living_world_v025']['chapter'] = '0'
        self.assertTrue(world.panel(state)['ready'])
        state, _, marks = world.choose(state, 0, 'return_jade', T)
        self.assertEqual((state['living_world_v025']['chapter'], marks), (1, 3))
        state['living_world_v025']['chapter'] = 1.0
        self.assertEqual(world.panel(state)['region'], 'stonepeak')

    def test_relationship_tier_labels_and_clamps(self):
        for trust, expected in ((0, ('Acquaintance', 0)), (2, ('Acquaintance', 2)),
                                (3, ('Growing trust', 3)), (5, ('Growing trust', 5)),
                                (6, ('Trusted ally', 6)), (12, ('Trusted ally', 12)),
                                (99, ('Trusted ally', 12)), (-4, ('Acquaintance', 0)),
                                ('7', ('Trusted ally', 7)), ('x', ('Acquaintance', 0)),
                                (None, ('Acquaintance', 0)), (float('inf'), ('Trusted ally', 12)),
                                (float('nan'), ('Acquaintance', 0)), (10 ** 400, ('Trusted ally', 12)),
                                ([3], ('Acquaintance', 0))):
            self.assertEqual(world.relationship({'npcs': {'yue': {'trust': trust}}}, 'yue'), expected, trust)
        self.assertEqual(world.relationship({}, 'zhao'), ('Acquaintance', 0))


class ExplorationProofTests(unittest.TestCase):
    def test_only_current_chapter_region_opens_in_order(self):
        state = {}
        for chapter, entry in enumerate(world.CHAPTERS):
            for region in ('mistwood', 'stonepeak', 'thunder_rift'):
                if region == entry['region']:
                    continue
                probe, text = world.encounter(state, region, T, 999)
                self.assertFalse(world.panel(probe)['ready'], (chapter, region))
                self.assertIn(entry['region'], text)
                with self.assertRaisesRegex(ValueError, 'Explore ' + entry['region']):
                    world.choose(probe, chapter, entry['choices'][0][0], T)
            state, _, _ = advance(state, chapter, entry['choices'][1][0])
            self.assertFalse(world.panel(state)['ready'])
        for region in ('mistwood', 'stonepeak', 'thunder_rift'):
            after, _ = world.encounter(state, region, T, 999)
            self.assertNotIn('encounter', after['living_world_v025'])
            for chapter in range(3):
                with self.assertRaises(ValueError):
                    world.choose(after, chapter, world.CHAPTERS[chapter]['choices'][0][0], T)

    def test_unknown_region_rejected(self):
        with self.assertRaises(ValueError):
            world.encounter({}, 'heaven', T, 999)

    def test_proof_for_old_chapter_cannot_resolve_next(self):
        state, _ = world.encounter({}, 'mistwood', T, 999)
        state['living_world_v025']['chapter'] = 1
        self.assertFalse(world.panel(state)['ready'])
        with self.assertRaisesRegex(ValueError, 'Explore stonepeak'):
            world.choose(state, 1, 'follow_yue', T)


class TechniqueMatrixTests(unittest.TestCase):
    def test_every_archive_choice_and_root_including_rare(self):
        self.assertTrue(set(RARE) <= set(ALL_ROOTS))
        effects = {'guard': 'HP restored', 'flow': 'Combat Qi restored', 'strike': 'extra damage'}
        self.assertEqual({style for _, style in world.STYLE.values()}, set(effects))
        for choice, (name, style) in world.STYLE.items():
            state, _, _ = world.choose(prepare_choice(chapter=1), 1, choice, T)
            state = trip(state)
            for root in ALL_ROOTS:
                form = world.technique(state, {}, root)
                self.assertEqual(form, {'name': f'{root} · {name}', 'root': relevant_root(root),
                                        'style': style, 'rank': 1, 'effect': effects[style]})
            for root in (None, 'Unawakened', '', 'Dragon'):
                self.assertIsNone(world.technique(state, {}, root))

    def test_rank_progression_at_zero_four_and_eight_practices(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'follow_yue', T)
        char = {}
        ranks = {0: world.technique(state, char, 'Void')['rank']}
        for number in range(1, 10):
            world.practice_growth(char, 'Void', T + timedelta(hours=6 * number))
            char = trip(char)
            ranks[number] = world.technique(state, char, 'Void')['rank']
        self.assertEqual(ranks, {0: 1, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 3})
        self.assertEqual(char['dao_practice_v025']['root'], 'Void')

    def test_no_form_before_archive_even_with_practice(self):
        char = {'dao_practice_v025': {'practices': 9}}
        state, _, _ = advance({}, 0, 'return_jade')
        self.assertIsNone(world.technique(state, char, 'Fire'))


class CorruptedSaveTests(unittest.TestCase):
    BAD = (None, 'garbage', [1, 2], 7,
           {'chapter': 'x', 'choices': ['a'], 'npcs': 'b', 'fragments': 'c'},
           {'chapter': -5, 'npcs': {'jian': 'str', 'yue': {'trust': 'x', 'memories': 'abc'},
                                     'zhao': {'trust': 10 ** 9, 'memories': [1, 'x', {'ok': 1}]}},
            'fragments': 99, 'encounter': 'forged', 'lore_count': 'bad', 'training_count': None},
           {'chapter': float('nan'), 'choices': None, 'npcs': None},
           {'chapter': 7}, {'chapter': '5'}, {'chapter': True}, {'chapter': 2.9},
           {'chapter': 10 ** 400}, {'chapter': float('inf')}, {'chapter': [1]},
           {'chapter': 0, 'choices': {'0': ['x'], '1': {'a': 1}, '2': 5}, 'technique': 5})

    def test_over_range_or_non_whole_chapter_counts_only_saved_choices(self):
        # Intended: a chapter that is not a whole number in 0..3 never skips unpaid chapters;
        # it falls back to the contiguous saved choices, so a finished arc stays finished.
        done = {'0': 'return_jade', '1': 'follow_yue', '2': 'reconcile'}
        for chapter, choices, expected in ((7, {}, 0), ('5', {}, 0), (True, {}, 0), (2.9, {}, 0),
                                           (7, {'0': 'return_jade'}, 1), ('9', done, 3), (False, done, 3),
                                           ('2', {}, 2), (2.0, {}, 2), (3, done, 3), (-1, {'0': 'x', '2': 'y'}, 1)):
            story = {'chapter': chapter, 'choices': dict(choices)}
            self.assertEqual(world.panel({'living_world_v025': story})['chapter'], expected, (chapter, choices))
            self.assertEqual(world.panel({'living_world_v025': story})['complete'], expected == 3)

    def test_unhashable_saved_choice_never_breaks_panel_or_exploration(self):
        for choices in ({'0': ['x']}, {'1': {'k': 1}, '0': 'return_jade'}, {'0': 'a', '1': 'b', '2': {'r': 1}},
                        {'0': 'a', '1': 'b', '2': ['restore_beacon']}):
            for chapter in (1, 3):
                state = {'living_world_v025': {'chapter': chapter, 'choices': deepcopy(choices)}}
                world.panel(state)
                world.status(state)
                for region in ('mistwood', 'stonepeak', 'thunder_rift'):
                    after, text = world.encounter(state, region, T, 999)
                    self.assertTrue(all(isinstance(v, str) for v in after['living_world_v025']['choices'].values()))
        state = {'living_world_v025': {'chapter': 1, 'choices': {'0': 'trace_script', '1': ['x']}}}
        self.assertIn('read beneath', world.panel(state)['text'])

    def test_non_string_saved_technique_is_ignored_by_lesson_panel(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'follow_yue', T)
        self.assertEqual(world.training_panel(state, T)['current_style'], 'Seeking Thread')
        for bad in (5, ['x'], {'a': 1}, None):
            state['living_world_v025']['technique'] = bad
            self.assertIsNone(world.training_panel(state, T)['current_style'], bad)

    def test_unhashable_sect_doctrine_uses_generic_sect_line(self):
        state = {'sect': 'Azure', 'living_world_v025': {}}
        generic = 'remember the people behind the legend'
        for doctrine in (['valor'], {'x': 1}, 5, None, 'unknown'):
            _, text = world.encounter(state, 'mistwood', T, 999, doctrine)
            self.assertIn(generic, text, doctrine)
            _, reply, _ = world.choose(prepare_choice(state), 0, 'return_jade', T, doctrine)
            self.assertIn(generic, reply, doctrine)
        self.assertIn('discipline in the storm', world.encounter(state, 'mistwood', T, 999, 'valor')[1])

    def test_corrupted_saves_render_and_recover(self):
        for bad in self.BAD:
            state = {'living_world_v025': deepcopy(bad), 'companion': ['odd']}
            view = world.panel(state)
            self.assertEqual(view['chapter'], 0, bad)
            self.assertIn('Echoes of the Fallen Heaven', world.status(state))
            world.training_panel(state, T)
            state, _ = world.encounter(state, 'mistwood', T, 0)
            self.assertTrue(world.panel(state)['ready'], bad)
            state, _, marks = world.choose(state, 0, 'trace_script', T)
            story = trip(state)['living_world_v025']
            self.assertEqual((story['chapter'], story['fragments'], marks), (1, 1, 3))
            self.assertLessEqual(story['npcs']['yue']['trust'], world.MAX_TRUST)
            self.assertTrue(all(isinstance(m, dict) for m in story['npcs']['yue']['memories']))

    def test_huge_trust_clamps_and_negative_chapter_uses_first_region(self):
        story = {'chapter': -5, 'npcs': {'zhao': {'trust': 10 ** 9}}}
        self.assertIn('mistwood', world.status({'living_world_v025': story}))
        self.assertIn('Zhao Tianming · Trusted ally 12/12', world.status({'living_world_v025': story}))

    def test_training_day_formats_and_corruption(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'protect_archive', T)
        story = state['living_world_v025']
        for saved, ready in ((None, True), (123, True), ('', True), ('2026-10-24', True),
                             ('2026-10-25', False), ('2026-10-25T23:59:00+00:00', False),
                             ('2026-10-24T23:59:00+00:00', True)):
            story['training_day'] = saved
            self.assertEqual(world.training_panel(state, T)['ready'], ready, saved)
        story['training_day'] = None
        story['training_count'] = 'x'
        state, _ = world.train(state, 'jian', T)
        self.assertEqual(state['living_world_v025']['training_count'], 1)

    def test_corrupted_practice_counter_and_missing_form_name(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'train_zhao', T)
        state['living_world_v025'].pop('technique')
        for bad, rank, practices in (('x', 1, 1), (None, 1, 1), ([], 1, 1), ('nine', 1, 1),
                                     ({'practices': 'x'}, 1, 1), ({'practices': None}, 1, 1),
                                     ({'practices': -7}, 1, 1), ({'practices': float('nan')}, 1, 1),
                                     ({'practices': 10 ** 9}, 3, 9), ({'practices': float('inf')}, 3, 9),
                                     ({'practices': '5'}, 2, 6)):
            char = {'dao_practice_v025': deepcopy(bad)}
            form = world.technique(state, char, 'Fire')
            self.assertEqual(form['name'], 'Fire · Shared Horizon')
            self.assertEqual(form['rank'], rank, bad)
            world.practice_growth(char, 'Fire', T)
            self.assertEqual(char['dao_practice_v025']['practices'], practices, bad)

    def test_lore_counter_corruption_recovers(self):
        state, text = world.encounter({'living_world_v025': {'lore_count': 'bad'}}, 'mistwood', T, 0)
        self.assertIn('Rare discovery', text)
        self.assertEqual(state['living_world_v025']['lore_count'], 1)

    def test_teacher_npc_entry_corruption_does_not_block_lesson(self):
        state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'protect_archive', T)
        state['living_world_v025']['npcs']['yue'] = 'broken'
        state, reply = world.train(state, 'yue', T)
        self.assertEqual(state['living_world_v025']['npcs']['yue']['trust'], 1)
        self.assertIn('Trust 1/12', reply)


class QuestAndReplayTests(unittest.IsolatedAsyncioTestCase):
    async def run_action(self, db, event, op, argument, now=T, roll=999):
        clock = type('InjectedClock', (Clock,), {'now': classmethod(lambda cls, tz=None: now)})
        with patch.object(living_dao, 'datetime', clock), patch('secrets.randbelow', return_value=roll):
            return await living_dao.action(db.repository(), 7, 1, event, op, argument)

    async def test_story_marks_exactly_nine_and_quests_count_only_expeditions(self):
        db = StoryConnection()
        story_marks = 0
        event = 0
        for chapter, entry in enumerate(world.CHAPTERS):
            now = T + timedelta(minutes=5 * chapter)
            event += 1
            await self.run_action(db, event, 'explore', entry['region'], now)
            before = db.stocks[1]['marks']
            event += 1
            reply = await self.run_action(db, event, 'story_choice', f'{chapter}:{entry["choices"][2][0]}', now)
            story_marks += db.stocks[1]['marks'] - before
            again = await self.run_action(db, event, 'story_choice', f'{chapter}:{entry["choices"][2][0]}', now)
            self.assertEqual(reply, again)
            with self.assertRaisesRegex(ValueError, 'already resolved'):
                await self.run_action(db, 100 + chapter, 'story_choice', f'{chapter}:{entry["choices"][0][0]}', now)
        self.assertEqual(story_marks, 9)
        self.assertEqual(db.living[1]['progress'], {'explore': 3})
        self.assertEqual(db.living[1]['explorations'], 3)
        npcs = trip(db.living[1])['living_world_v025']['npcs']
        self.assertEqual({k: v['trust'] for k, v in npcs.items()}, {'zhao': 9, 'jian': 3, 'yue': 3})
        self.assertEqual({k: [m['chapter'] for m in v['memories']] for k, v in npcs.items()},
                         {'zhao': [0, 1, 2], 'jian': [0, 2], 'yue': [1, 2]})
        self.assertEqual([m['choice'] for m in npcs['zhao']['memories']], ['share_credit', 'train_zhao', 'reconcile'])
        marks = db.stocks[1]['marks']
        for quest, reward in (('scout', 6), ('adventurer', 12)):
            reply = await self.run_action(db, 200, 'quest', quest) if quest == 'scout' else \
                await self.run_action(db, 201, 'quest', quest)
            marks += reward
            self.assertEqual(db.stocks[1]['marks'], marks)
        self.assertEqual(await self.run_action(db, 201, 'quest', 'adventurer'), reply)
        with self.assertRaisesRegex(ValueError, 'already been claimed'):
            await self.run_action(db, 202, 'quest', 'adventurer')
        self.assertEqual(db.stocks[1]['marks'], marks)
        with self.assertRaises(ValueError):
            await self.run_action(db, 203, 'story_choice', '3:restore_beacon')
        self.assertEqual(db.stocks[1]['marks'], marks)

    async def test_story_choice_alone_never_completes_explore_quest(self):
        db = StoryConnection()
        db.living[1] = living_dao.decode(trip(prepare_choice(db.living[1])))
        await self.run_action(db, 1, 'story_choice', '0:return_jade')
        self.assertEqual(db.living[1]['progress'], {})
        with self.assertRaisesRegex(ValueError, '0/1'):
            await self.run_action(db, 2, 'quest', 'scout')
        self.assertEqual(db.stocks[1]['marks'], 3)

    async def test_missing_or_null_hall_row_has_no_doctrine_and_still_resolves(self):
        for hall in (None, {}):
            db = StoryConnection()
            db.hall = hall
            await self.run_action(db, 1, 'explore', 'mistwood')
            reply = await self.run_action(db, 2, 'story_choice', '0:share_credit')
            self.assertIn('remember the people behind the legend', reply)
            self.assertEqual(db.stocks[1]['marks'], 6)

    async def test_unhashable_hall_doctrine_or_saved_choice_does_not_block_story_actions(self):
        for hall, saved in (({'doctrine': ['valor']}, None), ({'doctrine': {}}, None),
                            ({'doctrine': 'valor'}, {'chapter': 0, 'choices': {'0': ['x']}})):
            db = StoryConnection()
            db.hall = hall
            if saved:
                db.living[1]['living_world_v025'] = saved
            await self.run_action(db, 1, 'explore', 'mistwood')
            reply = await self.run_action(db, 2, 'story_choice', '0:return_jade')
            self.assertIn('Chapter complete', reply)
            self.assertEqual(db.living[1]['living_world_v025']['choices'], {'0': 'return_jade'})

    async def test_corrupted_story_save_does_not_block_exploration(self):
        db = StoryConnection()
        db.living[1]['living_world_v025'] = {'chapter': 'x', 'npcs': 'b', 'encounter': 'forged'}
        await self.run_action(db, 1, 'explore', 'mistwood')
        self.assertTrue(world.panel(db.living[1])['ready'])
        await self.run_action(db, 2, 'story_choice', '0:return_jade')
        self.assertEqual(db.living[1]['living_world_v025']['chapter'], 1)


class PracticeConnection(CombatConnection):
    async def fetchval(self, sql, *args):
        if sql.startswith('SELECT state FROM characters'):
            return json.dumps(self.db.character)
        if sql.startswith('SELECT root FROM character_origins'):
            return self.db.root
        return await super().fetchval(sql, *args)


class PracticeDatabase(CombatDatabase):
    def __init__(self):
        super().__init__()
        self.character = default_state(T)
        self.root = 'Lightning'

    @asynccontextmanager
    async def acquire(self):
        yield PracticeConnection(self)


class TeacherSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, db, event=1):
        with patch.object(combat_store, 'datetime', Clock):
            await combat_store.execute(db.repository(), 7, 101, event, 'start')
        return db.battle.get('dao_technique')

    async def test_teacher_retune_changes_next_battle_snapshot_only(self):
        db = PracticeDatabase()
        db.living, _, _ = world.choose(prepare_choice(db.living, chapter=1), 1, 'protect_archive', T)
        db.living = trip(db.living)
        self.assertEqual((await self.start(db))['style'], 'guard')
        db.battle = {}
        db.living, _ = world.train(db.living, 'yue', T)
        db.living = trip(db.living)
        form = await self.start(db, 2)
        self.assertEqual(form['name'], 'Lightning · Seeking Thread')
        self.assertEqual((form['style'], form['rank']), ('flow', 1))
        self.assertEqual(db.living['living_world_v025']['choices']['1'], 'protect_archive')

    async def test_practice_rank_rare_root_and_corruption_in_snapshot(self):
        db = PracticeDatabase()
        db.living, _, _ = world.choose(prepare_choice(db.living, chapter=1), 1, 'train_zhao', T)
        db.root = 'Chaos'
        db.character['dao_practice_v025'] = {'practices': 8}
        form = await self.start(db)
        self.assertEqual((form['root'], form['rank'], form['style']), ('Lightning', 3, 'strike'))
        db.battle = {}
        db.character['dao_practice_v025'] = {'practices': 'corrupt'}
        self.assertEqual((await self.start(db, 2))['rank'], 1)

    async def test_unawakened_or_no_story_has_no_snapshot(self):
        db = PracticeDatabase()
        db.root = None
        self.assertIsNone(await self.start(db))
        db = PracticeDatabase()
        db.living = None
        self.assertIsNone(await self.start(db))


class CorruptedBattleFormTests(unittest.TestCase):
    def test_corrupted_battle_snapshot_never_breaks_a_turn(self):
        import combat
        def run(form=None):
            b = combat.begin('stone_guardian', 'Fire')
            b['hp'] -= 5  # room for a guard heal, so a forged heal would show
            if form is not None:
                b['dao_technique'] = form
            b = combat.turn(b, 'elemental_art')
            self.assertEqual(b['turn'], 1)
            return b['enemy_hp'], b['hp'], b['qi']
        base = run()
        for form in ({'style': 'strike', 'rank': 'x'}, {'style': 'guard'}, 'broken', ['strike'],
                     {'style': 'strike', 'rank': float('inf')}, {'style': 'flow', 'rank': float('nan')},
                     {'style': 'guard', 'rank': -4}, {'style': 'nova', 'rank': 3}, {'rank': 3}):
            self.assertEqual(run(form), base, form)
        enemy, hp, qi = base
        self.assertEqual(run({'style': 'strike', 'rank': 99}), (enemy - 3, hp, qi))
        self.assertEqual(run({'style': 'guard', 'rank': 2}), (enemy, hp + 2, qi))
        self.assertEqual(run({'style': 'flow', 'rank': '1'}), (enemy, hp, qi + 1))

if __name__ == '__main__':
    unittest.main()
