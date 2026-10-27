"""Story and teacher UI with synthetic interactions; never imports bot/config or touches a database."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import living_world_ui_v025 as ui
import living_world_v025 as world
import security_observer_v025 as observer
from living_world_ui_v025 import DuelView, StoryView, story_embed
from test_living_dao_ui_v024 import interaction
from test_living_world_v025 import T, prepare_choice


def load(names, namespace):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
    for name in names:
        node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated_v025_' + name, 'exec'), namespace)
    return namespace


def slash_names():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
    return [node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
            and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'command'
                    for d in node.decorator_list)]


def archive_done():
    state, _, _ = world.choose(prepare_choice(chapter=1), 1, 'train_zhao', T)
    return state


def complete():
    state = prepare_choice(chapter=2)
    state, _, _ = world.choose(state, 2, 'reconcile', T)
    return state


class Emitter:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def emit(self, kind, **fields):
        self.calls.append((kind, fields))
        if self.fail:
            raise RuntimeError('emitter down')


def callbacks():
    return dict(choose=AsyncMock(), refresh=AsyncMock(), explore=AsyncMock(), train=AsyncMock())


def pick(menu, values):
    # Public Select.values, not discord.py's private _values.
    return patch.object(type(menu), 'values', new_callable=PropertyMock, return_value=values)


def labels(view):
    return [c.label for c in view.children if isinstance(c, discord.ui.Button)]


def teacher_buttons(view):
    return [c for c in view.children if isinstance(c, discord.ui.Button) and c.row == 2]


def within_limits(test, view):
    test.assertLessEqual(len(view.children), 25)
    test.assertLessEqual(max(c.row for c in view.children), 4)
    for row in range(5):
        width = sum(5 if isinstance(c, discord.ui.Select) else 1 for c in view.children if c.row == row)
        test.assertLessEqual(width, 5)
    for c in view.children:
        if isinstance(c, discord.ui.Button):
            test.assertLessEqual(len(c.label), 80)
        else:
            test.assertLessEqual(len(c.placeholder), 150)


class StoryViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_player_sees_refresh_and_explore_but_no_teachers(self):
        cb = callbacks()
        view = StoryView(7, {}, now=T, **cb)
        self.assertEqual(labels(view), ['Refresh story', 'Explore Mistwood Vale', 'Bonds'])
        item = interaction(discord.InteractionType.component)
        await view.children[1].callback(item)
        cb['explore'].assert_awaited_once_with(item, 'mistwood')
        await view.children[0].callback(item)
        cb['refresh'].assert_awaited_once_with(item)
        within_limits(self, view)
        view.stop()

    async def test_ready_chapter_offers_three_choices_and_no_explore(self):
        cb = callbacks()
        view = StoryView(7, prepare_choice(), now=T, **cb)
        self.assertEqual(len(labels(view)), 5)  # three choices, Refresh, Bonds
        self.assertNotIn('Explore Mistwood Vale', labels(view))
        item = interaction(discord.InteractionType.component)
        await view.children[0].callback(item)
        cb['choose'].assert_awaited_once_with(item, 0, 'return_jade')
        cb['train'].assert_not_awaited()
        view.stop()

    async def test_archive_chapter_unlocks_three_ready_teachers(self):
        cb = callbacks()
        view = StoryView(7, archive_done(), now=T, **cb)
        self.assertIn('Explore Thunder Rift Expanse', labels(view))
        teachers = teacher_buttons(view)
        self.assertEqual([b.label for b in teachers], ['Jian Wuchen · Sword lesson',
            'Yue Lian · Archive investigation', 'Zhao Tianming · Friendly three-exchange duel'])
        self.assertFalse(any(b.disabled for b in teachers))
        item = interaction(discord.InteractionType.component)
        await teachers[0].callback(item)
        cb['train'].assert_awaited_once_with(item, 'jian', None)
        await teachers[1].callback(item)
        cb['train'].assert_awaited_with(item, 'yue', None)
        within_limits(self, view)
        view.stop()

    async def test_completed_arc_keeps_teachers_without_region_or_choices(self):
        view = StoryView(7, complete(), now=T, **callbacks())
        self.assertEqual(labels(view)[0], 'Refresh story')
        self.assertFalse(any(label.startswith('Explore') for label in labels(view)))
        self.assertEqual(len(teacher_buttons(view)), 3)
        self.assertEqual(len(view.children), 5)  # Refresh, Bonds, three teachers
        view.stop()

    async def test_training_after_todays_lesson_is_disabled_and_explains_next_utc_day(self):
        state, _ = world.train(archive_done(), 'yue', T)
        view = StoryView(7, state, now=T + timedelta(hours=2), **callbacks())
        self.assertTrue(all(b.disabled for b in teacher_buttons(view)))
        field = story_embed(state, T + timedelta(hours=2)).fields[0]
        tomorrow = int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp())
        self.assertIn(f'<t:{tomorrow}:R>', field.value)
        self.assertIn('Seeking Thread', field.value)
        view.stop()
        ready = StoryView(7, state, now=T + timedelta(days=1), **callbacks())
        self.assertFalse(any(b.disabled for b in teacher_buttons(ready)))
        ready.stop()

    async def test_locked_training_explains_stonepeak_and_legacy_callers_get_no_teacher_buttons(self):
        field = story_embed(prepare_choice(), T).fields[0]
        self.assertIn('Stonepeak', field.value)
        self.assertNotIn('<t:', field.value)
        cb = callbacks()
        cb.pop('train')
        view = StoryView(7, archive_done(), now=T, **cb)
        self.assertEqual(teacher_buttons(view), [])
        view.stop()

    async def test_story_controls_reject_another_player(self):
        cb = callbacks()
        view = StoryView(7, archive_done(), now=T, **cb)
        stranger = interaction(user=8)
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once()
        self.assertTrue(stranger.response.send_message.await_args.kwargs['ephemeral'])
        self.assertTrue(await view.interaction_check(interaction()))
        duel = DuelView(7, train=cb['train'], tells=list(world.TELLS.values()))
        other = interaction(user=8)
        self.assertFalse(await duel.interaction_check(other))
        other.response.send_message.assert_awaited_once_with('Open your own story from /guide.', ephemeral=True)
        view.stop(); duel.stop()

    async def test_embeds_fit_discord_limits_for_every_chapter(self):
        for state in ({}, prepare_choice(), archive_done(), complete()):
            embed = story_embed(state, T)
            self.assertLessEqual(len(embed.description), 4096)
            self.assertLessEqual(len(embed), 6000)
            self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))

    async def test_default_clock_just_before_utc_midnight_keeps_todays_lesson_spent(self):
        late = datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc)
        clock = type('LateClock', (datetime,), {'now': classmethod(lambda cls, tz=None: late)})
        state, _ = world.train(archive_done(), 'jian', late)
        with patch.object(ui, 'datetime', clock):
            view = StoryView(7, state, **callbacks())
            field = story_embed(state).fields[0]
        self.assertTrue(all(b.disabled for b in teacher_buttons(view)))
        self.assertIn(f'<t:{int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp())}:R>', field.value)
        view.stop()

    async def test_non_string_saved_form_name_still_renders_panel(self):
        state = archive_done()
        state['living_world_v025']['technique'] = 5
        field = story_embed(state, T).fields[0]
        self.assertNotIn('Personal form', field.value)
        StoryView(7, state, now=T, **callbacks()).stop()


class DuelTests(unittest.IsolatedAsyncioTestCase):
    async def opened(self):
        cb = callbacks()
        view = StoryView(7, archive_done(), now=T, **cb)
        item = interaction(discord.InteractionType.component)
        await teacher_buttons(view)[2].callback(item)
        options = item.response.send_message.await_args.kwargs
        self.assertTrue(options['ephemeral'])
        self.assertIn('Watch how I move', item.response.send_message.await_args.args[0])
        tells = [world.TELLS[stance] for stance in world.duel_order(T)]
        self.assertEqual([menu.placeholder for menu in options['view'].menus],
                         [f'Exchange {n} · {tell}' for n, tell in enumerate(tells, 1)])
        view.stop()
        return cb['train'], options['view']

    async def test_duel_requires_all_three_tactics_before_submitting(self):
        train, duel = await self.opened()
        self.assertIsInstance(duel, DuelView)
        within_limits(self, duel)
        self.assertEqual([m.row for m in duel.menus], [0, 1, 2])
        begin = duel.children[-1]
        self.assertEqual(begin.label, 'Begin duel')
        click = interaction(discord.InteractionType.component)
        await begin.callback(click)
        self.assertIn('missing: 1, 2, 3', click.response.send_message.await_args.args[0])
        menu = duel.menus[0]
        with pick(menu, ['guard']):
            await menu.callback(click)
        click.response.defer.assert_awaited()
        self.assertTrue(next(o for o in menu.options if o.value == 'guard').default)
        await begin.callback(click)
        self.assertIn('missing: 2, 3', click.response.send_message.await_args.args[0])
        train.assert_not_awaited()
        duel.stop()

    async def test_complete_duel_submits_chosen_tactics_in_order(self):
        train, duel = await self.opened()
        click = interaction(discord.InteractionType.component)
        for menu, move in zip(duel.menus, ('guard', 'strike', 'feint')):
            with pick(menu, [move]):
                await menu.callback(click)
        await duel.children[-1].callback(click)
        train.assert_awaited_once_with(click, 'zhao', ['guard', 'strike', 'feint'])
        duel.stop()

    async def test_unknown_select_value_is_not_accepted(self):
        train, duel = await self.opened()
        click = interaction(discord.InteractionType.component)
        for menu, move in zip(duel.menus, ('guard', 'strike', 'rush')):
            with pick(menu, [move]):
                await menu.callback(click)
        await duel.children[-1].callback(click)
        self.assertIn('missing: 3', click.response.send_message.await_args.args[0])
        train.assert_not_awaited()
        duel.stop()


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.emitter = Emitter()
        observer.enable(self.emitter)
        self.addCleanup(observer.disable)
        self.log = MagicMock()
        self.ns = load(['observe', 'story_denied', 'story_train', 'story_choose', 'story_refresh', 'story_open',
                        'story_send', 'living_run'],
            dict(discord=discord, authorized=lambda item: item.guild_id == 1, datetime=datetime, timezone=timezone,
                 bot=SimpleNamespace(repo=SimpleNamespace(get_state=AsyncMock(return_value={'qi': 1}))),
                 logging=SimpleNamespace(exception=self.log), explore=SimpleNamespace(callback=AsyncMock())))
        self.ns['living_action'] = AsyncMock(return_value='Explored')
        self.ns['living_read'] = AsyncMock(return_value={})

    async def test_training_happy_path_replies_then_refreshes_panel(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_world_v025.train_story', AsyncMock(return_value='Lesson done')) as train, \
             patch('living_dao.read', AsyncMock(return_value=archive_done())):
            await self.ns['story_train'](item, 'zhao', ['guard', 'strike', 'feint'])
        train.assert_awaited_once_with(self.ns['bot'].repo, 1, 7, 123, 'zhao', ['guard', 'strike', 'feint'])
        item.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        first, second = item.followup.send.await_args_list
        self.assertEqual(first.args[0], 'Lesson done')
        self.assertIsInstance(second.kwargs['view'], StoryView)
        self.assertEqual(len(teacher_buttons(second.kwargs['view'])), 3)
        second.kwargs['view'].stop()
        self.assertEqual(self.emitter.calls, [])

    async def test_training_value_error_shows_message_without_refresh(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_world_v025.train_story', AsyncMock(side_effect=ValueError('Lesson returns tomorrow.'))):
            await self.ns['story_train'](item, 'jian')
        item.followup.send.assert_awaited_once_with('Lesson returns tomorrow.', ephemeral=True)
        self.log.assert_not_called()
        self.assertEqual(self.emitter.calls, [])

    async def test_training_unexpected_error_is_generic_logged_and_observed(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_world_v025.train_story', AsyncMock(side_effect=RuntimeError('secret dsn text'))):
            await self.ns['story_train'](item, 'yue')
        self.log.assert_called_once()
        text = item.followup.send.await_args.args[0]
        self.assertNotIn('secret', text)
        self.assertIn('could not be confirmed', text)
        self.assertEqual(self.emitter.calls, [('unexpected_error', dict(actor=7, guild=1, operation='technique',
                                                                       outcome='failure', reason='unexpected'))])

    async def test_other_guild_is_denied_and_observed_for_every_story_handler(self):
        for name, args in (('story_train', ('jian',)), ('story_choose', (0, 'return_jade')), ('story_open', ())):
            with self.subTest(name=name):
                self.emitter.calls.clear()
                item = interaction(discord.InteractionType.component)
                item.guild_id = 2
                with patch('living_world_v025.train_story', AsyncMock()) as train, \
                     patch('living_world_v025.choose_story', AsyncMock()) as choose:
                    await self.ns[name](item, *args)
                train.assert_not_awaited(); choose.assert_not_awaited()
                item.response.defer.assert_not_awaited()
                item.response.send_message.assert_awaited_once_with('Private server only.', ephemeral=True)
                self.assertEqual(self.emitter.calls, [('unauthorized_command', dict(actor=7, guild=2,
                    operation='story', outcome='denied', reason='not_guild'))])

    async def test_failing_emitter_never_changes_the_reply(self):
        failing = Emitter(fail=True)
        observer.enable(failing)
        item = interaction(discord.InteractionType.component)
        item.guild_id = 2
        await self.ns['story_train'](item, 'jian')
        item.response.send_message.assert_awaited_once_with('Private server only.', ephemeral=True)
        self.assertEqual(len(failing.calls), 1)

    async def test_disabled_observer_records_nothing(self):
        observer.disable()
        item = interaction(discord.InteractionType.component)
        item.guild_id = 2
        await self.ns['story_choose'](item, 0, 'return_jade')
        self.assertEqual(self.emitter.calls, [])

    async def test_confirmed_choice_is_not_reported_unconfirmed_when_panel_refresh_fails(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_world_v025.choose_story', AsyncMock(return_value='Chapter complete')), \
             patch('living_dao.read', AsyncMock(side_effect=RuntimeError('pool closed'))):
            await self.ns['story_choose'](item, 0, 'return_jade')
        texts = [c.args[0] for c in item.followup.send.await_args_list]
        self.assertEqual(texts[0], 'Chapter complete')
        self.assertTrue(texts[1].startswith('Saved.'))
        self.assertFalse(any('could not be confirmed' in t for t in texts))
        self.log.assert_called_once()
        self.assertEqual(self.emitter.calls, [('unexpected_error', dict(actor=7, guild=1, operation='story', outcome='failure', reason='unexpected'))])

    async def test_choice_value_error_shows_message_without_refresh_or_observe(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_world_v025.choose_story', AsyncMock(side_effect=ValueError('Explore mistwood first.'))), \
             patch('living_dao.read', AsyncMock()) as read:
            await self.ns['story_choose'](item, 0, 'return_jade')
        item.followup.send.assert_awaited_once_with('Explore mistwood first.', ephemeral=True)
        read.assert_not_awaited()
        self.log.assert_not_called()
        self.assertEqual(self.emitter.calls, [])

    async def test_driver_or_decode_value_errors_are_never_echoed(self):
        errors = (json.JSONDecodeError('secret payload', 'secret doc', 0),
                  UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'secret bytes'),
                  type('DataError', (ValueError,), {})('invalid input for query argument $3: secret'))
        for name, target, operation, args in (('story_choose', 'choose_story', 'story', (0, 'return_jade')),
                                              ('story_train', 'train_story', 'technique', ('jian',))):
            for error in errors:
                with self.subTest(name=name, error=type(error).__name__):
                    self.emitter.calls.clear(); self.log.reset_mock()
                    item = interaction(discord.InteractionType.component)
                    with patch('living_world_v025.' + target, AsyncMock(side_effect=error)), \
                         patch('living_dao.read', AsyncMock()) as read:
                        await self.ns[name](item, *args)
                    text = item.followup.send.await_args.args[0]
                    self.assertNotIn('secret', text)
                    self.assertIn('could not be confirmed', text)
                    read.assert_not_awaited()
                    self.log.assert_called_once()
                    self.assertEqual(self.emitter.calls, [('unexpected_error', dict(actor=7, guild=1,
                        operation=operation, outcome='failure', reason='unexpected'))])

    async def test_choice_unexpected_error_is_observed_as_story_failure(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_world_v025.choose_story', AsyncMock(side_effect=KeyError('x'))):
            await self.ns['story_choose'](item, 0, 'return_jade')
        self.assertIn('could not be confirmed', item.followup.send.await_args.args[0])
        self.assertEqual(self.emitter.calls[0][1]['operation'], 'story')

    async def test_story_open_sends_panel_and_explore_button_reaches_real_command_callback(self):
        item = interaction(discord.InteractionType.component)
        with patch('living_dao.read', AsyncMock(return_value={})):
            await self.ns['story_open'](item)
        view = item.followup.send.await_args.kwargs['view']
        click = interaction(discord.InteractionType.component)
        await next(c for c in view.children if c.label == 'Explore Mistwood Vale').callback(click)
        self.ns['explore'].callback.assert_awaited_once_with(click, region='mistwood')
        self.assertIs(view.callbacks['refresh'], self.ns['story_open'])
        self.assertIs(view.callbacks['train'], self.ns['story_train'])
        view.stop()

    async def test_story_open_failure_answers_deferred_interaction(self):
        self.ns['bot'].repo.get_state = AsyncMock(side_effect=RuntimeError('down'))
        item = interaction(discord.InteractionType.component)
        await self.ns['story_open'](item)
        self.assertIn('could not be opened', item.followup.send.await_args.args[0])
        self.assertEqual(self.emitter.calls[0][0], 'unexpected_error')

    async def test_story_open_without_character_points_to_start(self):
        self.ns['bot'].repo.get_state = AsyncMock(return_value=None)
        item = interaction(discord.InteractionType.component)
        await self.ns['story_open'](item)
        self.assertIn('/start', item.followup.send.await_args.args[0])

    async def test_confirmed_explore_is_not_reported_unconfirmed_when_panel_refresh_fails(self):
        item = interaction()
        with patch('living_dao.read', AsyncMock(side_effect=RuntimeError('pool closed'))):
            await self.ns['living_run'](item, 'explore', 'mistwood')
        texts = [c.args[0] for c in item.followup.send.await_args_list]
        self.assertEqual(texts[0], 'Explored')
        self.assertTrue(texts[1].startswith('Saved.'))
        self.log.assert_called_once()
        self.assertEqual(self.emitter.calls, [('unexpected_error', dict(actor=7, guild=1, operation='story', outcome='failure', reason='unexpected'))])

    async def test_non_explore_living_action_does_not_refresh_story(self):
        item = interaction()
        with patch('living_dao.read', AsyncMock()) as read:
            await self.ns['living_run'](item, 'quest', 'scout')
        read.assert_not_awaited()
        item.followup.send.assert_awaited_once_with('Explored', ephemeral=True)

    async def test_living_run_errors_reply_without_refresh(self):
        for error, expected, logged in ((ValueError('Try again in 30s.'), 'Try again in 30s.', False),
                                        (RuntimeError('pool closed'), 'Could not confirm action.', True)):
            with self.subTest(error=type(error).__name__):
                self.log.reset_mock()
                self.ns['living_action'] = AsyncMock(side_effect=error)
                item = interaction()
                with patch('living_dao.read', AsyncMock()) as read:
                    await self.ns['living_run'](item, 'explore', 'mistwood')
                read.assert_not_awaited()
                item.followup.send.assert_awaited_once()
                self.assertTrue(item.followup.send.await_args.args[0].startswith(expected))
                self.assertEqual(self.log.called, logged)


class BondsViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_bonds_button_shows_trust_stance_and_memories(self):
        state, _, _ = world.choose(prepare_choice(), 0, 'return_jade', T)
        view = StoryView(7, state, now=T, **callbacks())
        button = next(c for c in view.children if isinstance(c, discord.ui.Button) and c.label == 'Bonds')
        item = interaction(discord.InteractionType.component)
        await button.callback(item)
        text = item.response.send_message.await_args.args[0]
        self.assertTrue(item.response.send_message.await_args.kwargs['ephemeral'])
        self.assertIn('Jian Wuchen', text)
        self.assertIn('The seal is entrusted to its former guardian.', text)
        self.assertIn('I · The Broken Jade', text)
        self.assertIn('No shared memories yet.', text)  # Zhao was not part of this choice.
        self.assertLessEqual(len(text), 2000)
        view.stop()

    def test_bonds_text_survives_corrupted_memories(self):
        state = {'living_world_v025': {'npcs': {'yue': {'trust': 7, 'memories': [{'text': 5}, {'chapter': 99, 'text': 'x'}, 'bad']}}}}
        text = world.bonds_text(state)
        self.assertIn('Trusted ally 7/12', text)
        self.assertIn('A shared moment: x', text)


class CommandBudgetTests(unittest.TestCase):
    def test_teacher_training_adds_no_slash_command(self):
        names = slash_names()
        self.assertEqual(len(names), 100)  # v0.25 adds only /abode; Discord allows at most 100.
        self.assertEqual(len(names), len(set(names)))
        for name in ('story_train', 'story_open', 'story_choose', 'observe'):
            self.assertNotIn(name, names)

    def test_bot_never_imports_root_observability_package(self):
        text = Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig')
        self.assertNotIn('import observability', text)
        self.assertNotIn('from observability', text)


if __name__ == '__main__':
    unittest.main()
