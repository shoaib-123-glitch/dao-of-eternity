"""Accessible dashboard, owner controls and atomic practice rewards; offline only."""
import ast
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

from beast_v022 import settle as settle_beast
from eternal import settle
from game import default_state
from qi_interface_v023 import CultivationView, dashboard_model, dashboard_embed, render_dashboard
from storage import Repository


T = datetime(2026, 10, 24, tzinfo=timezone.utc)


def state():
    value = default_state(T)
    settle(value, T)
    return value


class DashboardTests(unittest.TestCase):
    def test_accessible_exact_values_and_indefinite_growth(self):
        current = state()
        current['eternal']['bank_qi'] = 123456789
        model = dashboard_model(current, 'Fire', T)
        embed = dashboard_embed(model)
        text = json.dumps(embed.to_dict())
        self.assertIn('123,456,789', text)
        self.assertIn('no expiry', embed.description)
        self.assertLess(len(embed), 6000)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertIn('/sect_commission', text)

    def test_current_realm_and_cap_are_taken_from_actual_character(self):
        current = state()
        current['player']['advanced_to_foundation'] = True
        current.update(foundation_stage=3, foundation_qi=250)
        model = dashboard_model(current, 'Water', T)
        self.assertIn('Foundation', model['realm'])
        self.assertEqual(model['current'], 250)
        current.update(golden_core=True, golden_stage=2, golden_qi=100)
        self.assertIn('Golden Core', dashboard_model(current, 'Water', T)['realm'])

    def test_model_and_journal_do_not_modify_state(self):
        current = state()
        original = deepcopy(current)
        model = dashboard_model(current, None, T)
        self.assertEqual(current, original)
        self.assertIn('Unawakened', model['identity'])
        self.assertEqual(model['rate'], 40)

    def test_render_png_with_explicit_dimensions(self):
        model = dashboard_model(state(), 'Lightning', T)
        output = render_dashboard(model)
        self.assertEqual(output.read(8), b'\x89PNG\r\n\x1a\n')
        output.seek(0)
        with Image.open(output) as image:
            self.assertEqual(image.size, (1200, 800))

    def test_long_labels_and_large_reserves_render_without_overflow(self):
        model = dashboard_model(state(), 'Metal', T)
        model.update(identity='A very long character identity ' * 80, bank=10**30, earned=10**40)
        model['journey'] = ['A very long line ' * 200]
        self.assertGreater(render_dashboard(model).getbuffer().nbytes, 1000)

    def test_application_command_names_remain_unique_and_within_guild_budget(self):
        tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
        names = []
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == 'command'):
                        names.append(next((ast.literal_eval(k.value) for k in decorator.keywords
                                           if k.arg == 'name'), node.name))
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(len(names), 100)
        for name in ('qi', 'start', 'cultivation_focus', 'cultivation_practice', 'journey',
                     'sect_commission', 'sect_doctrine'):
            self.assertIn(name, names)


class ButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_controls_are_owner_bound_and_ephemeral(self):
        callbacks = {k: AsyncMock() for k in ('refresh', 'practice', 'journal')}
        view = CultivationView(7, **callbacks)
        inter = SimpleNamespace(user=SimpleNamespace(id=8), response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(inter))
        inter.response.send_message.assert_awaited_once()
        self.assertTrue(inter.response.send_message.await_args.kwargs['ephemeral'])
        for callback in callbacks.values():
            callback.assert_not_awaited()
        view.stop()

    async def test_each_button_dispatches_the_existing_protected_handler(self):
        callbacks = {k: AsyncMock() for k in ('refresh', 'practice', 'journal')}
        view = CultivationView(7, **callbacks)
        inter = SimpleNamespace(user=SimpleNamespace(id=7))
        self.assertTrue(await view.interaction_check(inter))
        for item in view.children:
            await item.callback(inter)
        for callback in callbacks.values():
            callback.assert_awaited_once_with(inter)
        self.assertEqual(view.timeout, 180)
        view.stop()

    async def test_qi_text_dashboard_sends_once_without_rendering_an_image(self):
        # Compile only this handler; importing bot.py would load credentials.
        tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'qi')
        node.decorator_list = []
        repo = SimpleNamespace(get_state=AsyncMock(return_value=state()),
                               get_origin=AsyncMock(return_value={'root': 'Fire'}))
        namespace = dict(discord=discord, authorized=lambda item: True,
            bot=SimpleNamespace(repo=repo), datetime=datetime, timezone=timezone,
            logging=SimpleNamespace(exception=lambda *a: None), run_op=AsyncMock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated_qi_handler', 'exec'), namespace)
        namespace['qi'].callback = namespace['qi']
        inter = SimpleNamespace(user=SimpleNamespace(id=7), guild_id=1,type=discord.InteractionType.application_command,
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        with patch('beast_v022.view', AsyncMock(return_value='Spirit beast stage 1')), patch(
                'qi_interface_v023.render_dashboard', side_effect=RuntimeError('render unavailable')):
            await namespace['qi'](inter)
        inter.followup.send.assert_awaited_once()
        args = inter.followup.send.await_args.kwargs
        self.assertIn('embed', args)
        self.assertIn('view', args)
        self.assertNotIn('file', args)
        self.assertTrue(args['ephemeral'])
        args['view'].stop()


class PracticeConnection:
    def __init__(self):
        self.character = state()
        self.living = settle_beast({'companion': 'jade_fox', 'bond': 1}, T)
        self.responses, self.events = {}, []
        self.lock = asyncio.Lock()
        self.fail_living_save = False

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            before = deepcopy((self.character, self.living, self.responses))
            try:
                yield
            except BaseException:
                self.character, self.living, self.responses = before
                raise

    async def fetchrow(self, sql, *args):
        self.events.append(sql)
        if 'FROM characters' in sql:
            return {'state': json.dumps(self.character)}
        if 'FROM living_dao_players' in sql:
            return {'data': json.dumps(self.living)} if self.living else None
        raise AssertionError('Unexpected fake query')

    async def fetchval(self, sql, *args):
        self.events.append(sql)
        if 'FROM processed_actions' in sql:
            return self.responses.get(args[2])
        if 'FROM character_origins' in sql:
            return 'Fire'
        raise AssertionError('Unexpected fake query')

    async def execute(self, sql, *args):
        self.events.append(sql)
        if sql.startswith('UPDATE characters'):
            self.character = json.loads(args[2])
        elif sql.startswith('UPDATE living_dao_players'):
            if self.fail_living_save:
                raise RuntimeError('synthetic save failure')
            self.living = json.loads(args[2])
        elif sql.startswith('INSERT INTO processed_actions'):
            self.responses[args[2]] = args[3]
        else:
            raise AssertionError('Unexpected fake mutation')

    @asynccontextmanager
    async def acquire(self):
        yield self


class PracticePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.con = PracticeConnection()
        self.repo = Repository(self.con)
        self.repo.settle_character = AsyncMock()  # Focus on this handler's transaction.

    async def practice(self, event=99, now=T):
        # Even a caller-supplied roll must be replaced by the server's draw.
        with patch('storage.randbelow', return_value=999):
            return await self.repo.execute(1, 7, event, 'cultivation_practice', now, roll=0)

    async def test_duplicate_concurrent_practice_rewards_companion_once(self):
        replies = await asyncio.gather(self.practice(), self.practice())
        self.assertEqual(replies[0], replies[1])
        self.assertIn('+6 Beast Qi', replies[0])
        self.assertEqual(self.con.living['beast_cultivation']['earned'], 6)
        self.assertEqual(self.con.character['cultivation_v023']['practices'], 1)
        self.assertEqual(self.con.character['cultivation_v023'].get('discoveries', 0), 0)

    async def test_cooldown_rejection_preserves_all_progress(self):
        await self.practice()
        before = deepcopy((self.con.character, self.con.living))
        with self.assertRaises(ValueError):
            await self.practice(event=100)
        self.assertEqual(before, (self.con.character, self.con.living))
        self.assertNotIn(100, self.con.responses)

    async def test_companion_save_failure_rolls_back_both_rewards(self):
        before = deepcopy((self.con.character, self.con.living))
        self.con.fail_living_save = True
        with self.assertRaises(RuntimeError):
            await self.practice()
        self.assertEqual(before, (self.con.character, self.con.living))
        self.assertEqual(self.con.responses, {})

    async def test_practice_without_companion_keeps_free_personal_progress(self):
        self.con.living = None
        reply = await self.practice()
        self.assertNotIn('Beast Qi', reply)
        self.assertEqual(self.con.character['cultivation_v023']['mastery']['Fire'], 4)
        self.assertFalse(any('player_inventory' in sql for sql in self.con.events))


if __name__ == '__main__':
    unittest.main()
