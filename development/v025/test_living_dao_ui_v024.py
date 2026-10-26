"""Exercise real UI callbacks with synthetic interactions; never import bot/config."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import combat
from onboarding_v024 import guide as guide_text
from qi_interface_v023 import CultivationView, TrailView, dashboard_embed, dashboard_model
from test_living_dao_v024 import character, companion, T


def load_definition(name, namespace):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, (ast.ClassDef, ast.AsyncFunctionDef)) and n.name == name)
    if isinstance(node, ast.AsyncFunctionDef):
        node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated_v024_'+name, 'exec'), namespace)
    return namespace[name]


def interaction(kind=discord.InteractionType.application_command, user=7):
    return SimpleNamespace(user=SimpleNamespace(id=user), guild_id=1, id=123, type=kind,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(),edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())


class LivingDaoUITests(unittest.IsolatedAsyncioTestCase):
    def qi_namespace(self):
        repo = SimpleNamespace(get_state=AsyncMock(return_value=character()),
                               get_origin=AsyncMock(return_value={'root': 'Fire'}))
        namespace = dict(discord=discord, authorized=lambda item: item.guild_id == 1,
            bot=SimpleNamespace(repo=repo), datetime=datetime, timezone=timezone,
            logging=SimpleNamespace(exception=lambda *a: None), run_op=AsyncMock(),
            guide=SimpleNamespace(callback=AsyncMock()),
            hunt=SimpleNamespace(callback=AsyncMock()),
            companion=SimpleNamespace(callback=AsyncMock()))
        handler = load_definition('qi', namespace)
        handler.callback = handler
        return namespace, handler

    async def test_qi_refresh_edits_one_panel_removes_old_art_and_dispatches_navigation(self):
        ns, handler = self.qi_namespace()
        item = interaction(discord.InteractionType.component)
        with patch('beast_v022.view', AsyncMock(return_value='Jade Fox · 18 Qi/hour')):
            await handler(item)
        item.response.defer.assert_awaited_once_with()
        item.followup.send.assert_not_awaited()
        item.edit_original_response.assert_awaited_once()
        options = item.edit_original_response.await_args.kwargs
        self.assertEqual(options['attachments'], [])
        self.assertIsNone(options['embed'].image.url)
        self.assertNotIn('file', options)
        view = options['view']
        for label, name in (('Guide', 'guide'), ('Hunt · Assisted', 'hunt'), ('Companion', 'companion')):
            button = next(c for c in view.children if c.label == label)
            await button.callback(item)
            ns[name].callback.assert_awaited_once()
        ns['hunt'].callback.assert_awaited_once_with(item, enemy='spirit_wolf', mode='assisted')
        view.stop()

    async def test_qi_slash_has_single_embed_no_image_or_repeated_content(self):
        _, handler = self.qi_namespace()
        item = interaction()
        with patch('beast_v022.view', AsyncMock(return_value='Jade Fox')):
            await handler(item)
        item.edit_original_response.assert_not_awaited()
        item.followup.send.assert_awaited_once()
        options = item.followup.send.await_args.kwargs
        self.assertNotIn('content', options)
        self.assertNotIn('file', options)
        self.assertIsNone(options['embed'].image.url)
        self.assertEqual(len(options['view'].children), 7)
        options['view'].stop()

    async def test_details_toggle_keeps_one_panel_and_performs_no_extra_database_reads(self):
        ns,handler=self.qi_namespace()
        item=interaction()
        with patch('beast_v022.view',AsyncMock(return_value='Jade Fox')):
            await handler(item)
        options=item.followup.send.await_args.kwargs
        view=options['view']
        button=next(c for c in view.children if c.label=='Details')
        click=interaction(discord.InteractionType.component)
        await button.callback(click)
        self.assertEqual(button.label,'Compact')
        expanded=click.response.edit_message.await_args.kwargs['embed']
        self.assertIn('Heavenly influences',[f.name for f in expanded.fields])
        await button.callback(click)
        self.assertEqual(button.label,'Details')
        compact=click.response.edit_message.await_args.kwargs['embed']
        self.assertNotIn('Heavenly influences',[f.name for f in compact.fields])
        self.assertLess(len(compact),len(expanded))
        ns['bot'].repo.get_state.assert_awaited_once()
        ns['bot'].repo.get_origin.assert_awaited_once()
        click.followup.send.assert_not_awaited()
        view.stop()

    async def test_dashboard_identifies_the_test_bot_when_two_applications_share_a_guild(self):
        ns,handler=self.qi_namespace()
        ns['bot'].user=SimpleNamespace(display_name='Dao TEST v024')
        item=interaction()
        with patch('beast_v022.view',AsyncMock(return_value='Jade Fox')):
            await handler(item)
        options=item.followup.send.await_args.kwargs
        self.assertIn('Dao TEST v024',options['embed'].footer.text)
        options['view'].stop()

    async def test_dashboard_navigation_rejects_another_player(self):
        callbacks = {k: AsyncMock() for k in ('refresh', 'practice', 'journal', 'guide', 'hunt', 'companion')}
        view = CultivationView(7, **callbacks)
        self.assertFalse(await view.interaction_check(interaction(user=8)))
        for cb in callbacks.values():
            cb.assert_not_awaited()
        view.stop()

    async def test_trail_buttons_dispatch_and_are_owner_bound(self):
        cb = AsyncMock()
        view = TrailView(7, {'Guide': cb})
        stranger, owner = interaction(user=8), interaction()
        self.assertFalse(await view.interaction_check(stranger))
        self.assertTrue(await view.interaction_check(owner))
        await view.children[0].callback(owner)
        cb.assert_awaited_once_with(owner)
        view.stop()

    async def test_other_guild_cannot_open_dashboard(self):
        ns, handler = self.qi_namespace()
        item = interaction()
        item.guild_id = 2
        await handler(item)
        ns['bot'].repo.get_state.assert_not_awaited()
        item.response.send_message.assert_awaited_once()
        self.assertTrue(item.response.send_message.await_args.kwargs['ephemeral'])

    async def test_battle_manual_choice_automatically_resumes_assisted_and_keeps_identity(self):
        battle = combat.assisted(combat.begin('thunder_serpent'))
        battle.update(battle_id=42, mode='assisted')
        after_guard = combat.turn(battle, 'lightning_guard')
        after_assist = combat.assisted(after_guard)
        execute, current = AsyncMock(return_value='battle reply'), AsyncMock(side_effect=[after_guard, after_assist])
        ns = dict(discord=discord, ACTIONS=combat.ACTIONS, unlocked=combat.unlocked,
                  authorized=lambda item: True, combat_execute=execute, combat_current=current,
                  bot=SimpleNamespace(repo=object()), logging=SimpleNamespace(exception=lambda *a: None))
        cls = load_definition('BattleView', ns)
        view = cls(7, battle)
        item = interaction()
        await view.callback_for('lightning_guard')(item)
        self.assertEqual(execute.await_count, 2)
        self.assertEqual(execute.await_args_list[0].args[-1], 'turn')
        self.assertEqual(execute.await_args_list[1].args[-1], 'assist')
        self.assertEqual(execute.await_args_list[1].args[-2], -item.id)
        for call in execute.await_args_list:
            self.assertEqual(call.kwargs['expected_battle_id'], 42)
        item.followup.send.await_args.kwargs['view'].stop()
        view.stop()

    async def test_battle_retreat_is_exposed_and_does_not_resume_assisted(self):
        b = combat.begin()
        b.update(mode='assisted', battle_id=42, second_wind_used=True)
        ended = deepcopy(b)
        ended['status'] = 'retreated'
        execute = AsyncMock(return_value='retreated')
        ns = dict(discord=discord, ACTIONS=combat.ACTIONS, unlocked=combat.unlocked,
                  authorized=lambda item: True, combat_execute=execute,
                  combat_current=AsyncMock(return_value=ended), bot=SimpleNamespace(repo=object()),
                  logging=SimpleNamespace(exception=lambda *a: None))
        cls = load_definition('BattleView', ns)
        view = cls(7, b)
        self.assertTrue(any(c.label == 'Retreat · no rewards' for c in view.children))
        self.assertTrue(next(c for c in view.children if c.label.startswith('Second Wind')).disabled)
        await view.callback_for('retreat')(interaction())
        execute.assert_awaited_once()
        self.assertEqual(execute.await_args.args[-1], 'retreat')
        view.stop()

    async def test_hunt_passes_selected_mode_and_enemy_into_saved_session(self):
        execute = AsyncMock(return_value='Hunt ready')
        b = combat.begin('thunder_serpent')
        ns = dict(discord=discord, authorized=lambda item: True, combat_execute=execute,
                  combat_current=AsyncMock(return_value=b), bot=SimpleNamespace(repo=object()),
                  logging=SimpleNamespace(exception=lambda *a: None),
                  BattleView=lambda *a: SimpleNamespace())
        handler = load_definition('hunt', ns)
        item = interaction()
        await handler(item, enemy='thunder_serpent', mode='assisted')
        self.assertEqual(execute.await_args_list[0].kwargs, {'enemy': 'thunder_serpent', 'mode': 'assisted'})
        self.assertEqual(execute.await_args_list[1].args[-1], 'assist')

    async def test_guide_new_player_and_existing_companion_dispatch_real_handlers(self):
        for state in (None, character()):
            ns = dict(discord=discord, authorized=lambda item: True,
                bot=SimpleNamespace(repo=SimpleNamespace(
                    get_state=AsyncMock(return_value=state), get_origin=AsyncMock(return_value={'root': 'Fire'}))),
                inventory_system=SimpleNamespace(current=AsyncMock(return_value={'marks': 3})),
                qi=SimpleNamespace(callback=AsyncMock()), hunt=SimpleNamespace(callback=AsyncMock()),
                companion=SimpleNamespace(callback=AsyncMock()))
            handler = load_definition('guide', ns)
            item = interaction()
            with patch('living_dao.read', AsyncMock(return_value=companion())):
                await handler(item)
            text = item.followup.send.await_args.args[0]
            self.assertLessEqual(len(text), 2000)
            self.assertIn('/beast_bond' if state else '/start', text)
            item.followup.send.await_args.kwargs['view'].stop()

    async def test_guide_topic_switch_edits_existing_message_without_reading_progress(self):
        from qi_interface_v023 import GuideView
        from onboarding_v024 import SECTIONS,guide
        current=character()
        pages={key:guide(current,'Fire',companion(),{},key) for key in SECTIONS}
        view=GuideView(7,pages,{'Qi':AsyncMock(),'Hunt':AsyncMock(),'Companion':AsyncMock()})
        self.assertEqual(view.menu.row,0)
        self.assertTrue(all(c.row==1 for c in view.children if c is not view.menu))
        self.assertFalse(await view.interaction_check(interaction(user=8)))
        item=interaction(discord.InteractionType.component)
        self.assertTrue(await view.interaction_check(item))
        for topic in SECTIONS:
            view.menu._values=[topic]  # The selection Discord would deliver.
            await view.menu.callback(item)
            options=item.response.edit_message.await_args.kwargs
            self.assertEqual(options['content'],pages[topic])
            self.assertIs(options['view'],view)
        item.followup.send.assert_not_awaited()
        self.assertEqual(len(view.menu.options),6)
        view.stop()

    async def test_battle_has_cost_labels_and_owner_bound_technique_help_without_spending_a_turn(self):
        battle=combat.begin()
        execute=AsyncMock()
        ns=dict(discord=discord,ACTIONS=combat.ACTIONS,unlocked=combat.unlocked,
            authorized=lambda item:item.guild_id==1,combat_execute=execute,
            combat_current=AsyncMock(),bot=SimpleNamespace(repo=object()))
        cls=load_definition('BattleView',ns)
        view=cls(7,battle)
        self.assertTrue(any(c.label=='Thunder Palm · 8 Qi' for c in view.children))
        self.assertTrue(any(c.label=='Auto Finish' for c in view.children))
        self.assertTrue(any(c.label=='Assist Until Danger' for c in view.children))
        self.assertLessEqual(len(view.children),25)
        await view.show_help(interaction(user=8))
        owner=interaction()
        await view.show_help(owner)
        self.assertIn('Combat Qi is separate',owner.response.send_message.await_args.args[0])
        execute.assert_not_awaited()
        self.assertEqual(battle['turn'],0)
        view.stop()

    async def test_start_offers_non_destructive_guide_and_dashboard_shortcuts(self):
        ns = dict(discord=discord, authorized=lambda item: True, datetime=datetime, timezone=timezone,
                  bot=SimpleNamespace(repo=SimpleNamespace(start=AsyncMock(return_value='Welcome'))),
                  guide=SimpleNamespace(callback=AsyncMock()), qi=SimpleNamespace(callback=AsyncMock()))
        handler = load_definition('start', ns)
        item = interaction()
        await handler(item)
        view = item.followup.send.await_args.kwargs['view']
        for child in view.children:
            await child.callback(item)
        ns['guide'].callback.assert_awaited_once_with(item)
        ns['qi'].callback.assert_awaited_once_with(item)
        view.stop()


class DashboardReadabilityTests(unittest.TestCase):
    def test_no_block_bar_and_single_exact_progress_display(self):
        embed = dashboard_embed(dashboard_model(character(), 'Fire', T))
        text = str(embed.to_dict())
        for char in ('▰', '▱', '■', '□'):
            self.assertNotIn(char, text)
        self.assertIsNone(embed.image.url)
        dantian = next(f.value for f in embed.fields if f.name.startswith('DANTIAN'))
        self.assertIn('%', dantian)
        self.assertIn('Qi to the next stage', dantian)
        self.assertLess(len(embed), 6000)

    def test_compact_dashboard_has_six_fields_and_keeps_boost_details_available(self):
        from heavens import add_boon
        current=character()
        add_boon(current,T,'rare')
        model=dashboard_model(current,'Fire',T)
        compact=dashboard_embed(model)
        details=dashboard_embed(model,detailed=True)
        self.assertLessEqual(len(compact.fields),6)
        self.assertIn('Temporary boost active',compact.description)
        self.assertIn('expiry',compact.description)
        for influence in model['influences']:
            self.assertIn(influence,str(details.to_dict()))
        self.assertIn('/breakthrough',str(compact.to_dict()))
        self.assertLess(len(compact),len(details))

    def test_every_guide_page_fits_discord_and_references_only_registered_command_names(self):
        import re
        from onboarding_v024 import guide,SECTIONS
        tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
        names={node.name for node in tree.body if isinstance(node,ast.AsyncFunctionDef)
               and any(isinstance(dec,ast.Call) and isinstance(dec.func,ast.Attribute) and dec.func.attr=='command'
                       for dec in node.decorator_list)}
        for living in ({},{'companion':'jade_fox','bond':3,'sect':'Heavenly Dao'}):
            for topic in SECTIONS:
                with self.subTest(topic=topic):
                    text=guide(character(),'Fire',living,{},topic)
                    self.assertLessEqual(len(text),2000)
                    for name in re.findall(r'(?<!\w)/([a-z_]+)',text):
                        self.assertIn(name,names)
        self.assertLess(len(guide(character(),'Fire')),900)


if __name__ == '__main__':
    unittest.main()
