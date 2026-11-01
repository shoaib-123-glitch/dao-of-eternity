"""Startup failures reproduced with synthetic credentials and disconnected clients."""
import ast
import asyncio
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import asyncpg
import discord

import private_playtest as launch
from storage import Repository
from test_private_playtest import Connection, config, environment, extract_function, identity


PRIVATE = 'synthetic-private-token-password-url-parameters-record'
PROJECT = Path(__file__).resolve().parent
Stage = launch.StartupStage


def read_config():
    return replace(config(), readonly=True)


def server_error(code):
    return asyncpg.PostgresError.new({'C': code, 'M': PRIVATE, 'D': PRIVATE}, query=PRIVATE)


def http_error(kind, status):
    return kind(SimpleNamespace(status=status, reason=PRIVATE), PRIVATE)


class HostileError(Exception):
    def __str__(self):
        raise AssertionError('Exception stringification forbidden')

    def __repr__(self):
        raise AssertionError('Exception repr forbidden')

    def __getattribute__(self, name):
        if name in ('args', 'message', 'query', 'request', 'response', 'headers', 'filename'):
            raise AssertionError('Sensitive attribute access forbidden')
        return super().__getattribute__(name)


class SafeDiagnosticTests(unittest.TestCase):
    def test_every_boundary_uses_fixed_stage_without_reading_exception_details(self):
        for stage in Stage:
            with self.subTest(stage=stage), self.assertRaises(launch.StartupFailure) as caught:
                with launch.startup_stage(stage):
                    raise HostileError(PRIVATE)
            payload = caught.exception.details
            self.assertEqual(payload['stage'], stage.value)
            self.assertEqual(payload['family'], 'PYTHON_ERROR')
            self.assertNotIn(PRIVATE, json.dumps(payload))

    def test_nested_boundaries_keep_the_actual_failure(self):
        with self.assertRaises(launch.StartupFailure) as caught:
            with launch.startup_stage(Stage.DISCORD_LOGIN):
                with launch.startup_stage(Stage.DATABASE_CONNECT):
                    with launch.startup_stage(Stage.DATABASE_IDENTITY_QUERY):
                        raise TimeoutError(PRIVATE)
        self.assertEqual(caught.exception.details['stage'], 'DATABASE_IDENTITY_QUERY')
        self.assertEqual(caught.exception.details['family'], 'TIMEOUT')

    def test_python_and_transport_families_are_distinct(self):
        for error, family in ((ImportError(PRIVATE), 'DEPENDENCY_IMPORT'),
                (SyntaxError(PRIVATE), 'PYTHON_SYNTAX'), (TimeoutError(PRIVATE), 'TIMEOUT'),
                (ConnectionError(PRIVATE), 'CONNECTION'), (OSError(PRIVATE), 'OS_ERROR'),
                (TypeError(PRIVATE), 'PYTHON_ERROR')):
            with self.subTest(family=family):
                payload = launch.failure_details(Stage.RUNTIME, error)
                self.assertEqual(payload['family'], family)
                self.assertNotIn(PRIVATE, json.dumps(payload))

    def test_discord_authentication_and_http_status_are_sanitized(self):
        for error, family, status in ((discord.LoginFailure(PRIVATE), 'DISCORD_AUTHENTICATION', None),
                (http_error(discord.Forbidden, 403), 'DISCORD_FORBIDDEN', 403),
                (http_error(discord.NotFound, 404), 'DISCORD_NOT_FOUND', 404),
                (http_error(discord.HTTPException, 429), 'DISCORD_HTTP', 429)):
            with self.subTest(family=family):
                payload = launch.failure_details(Stage.DISCORD_GUILD_FETCH, error)
                self.assertEqual((payload['family'], payload['http_status']), (family, status))
                self.assertNotIn(PRIVATE, json.dumps(payload))

    def test_unallowlisted_http_status_is_suppressed(self):
        error = http_error(discord.HTTPException, 599)
        self.assertIsNone(launch.failure_details(Stage.DISCORD_LOGIN, error)['http_status'])

    def test_server_auth_permission_and_read_only_codes_are_preserved(self):
        for code in ('28P01', '42501', '25006', '53300'):
            with self.subTest(code=code):
                payload = launch.failure_details(Stage.DATABASE_CONNECT, server_error(code))
                self.assertEqual((payload['family'], payload['sqlstate_code'], payload['sqlstate_source']),
                                 ('SERVER_REJECTION', code, 'SERVER_FIELD'))
                self.assertNotIn(PRIVATE, json.dumps(payload))

    def test_unknown_sqlstate_code_and_local_driver_provenance_remain_safe(self):
        result = launch.failure_details(Stage.DATABASE_IDENTITY_QUERY, server_error('42Z99'))
        self.assertEqual(result['sqlstate_class'], '42')
        self.assertIsNone(result['sqlstate_code'])
        local = launch.failure_details(Stage.DATABASE_CONNECT, asyncpg.DataError(PRIVATE))
        self.assertEqual(local['family'], 'POSTGRES_UNATTRIBUTED')
        self.assertEqual(local['sqlstate_source'], 'CLASS_DEFAULT')

    def test_rejection_reasons_are_allowlisted(self):
        for reason in ('APPLICATION_ID_MISMATCH', 'PUBLIC_BOT_ENABLED', 'CLUSTER_ID_MISMATCH', PRIVATE):
            payload = launch.failure_details(Stage.DISCORD_APPLICATION_CHECK, launch.PlaytestRejected(reason))
            self.assertEqual(payload['family'], 'SAFETY')
            self.assertIn(payload['reason'], launch.REASONS)
            self.assertNotIn(PRIVATE, json.dumps(payload))

    def test_runtime_report_contains_versions_without_executable_paths(self):
        with patch.object(launch.metadata, 'version', return_value=PRIVATE):
            report = launch.runtime_info()
        self.assertEqual(set(report), {'python_version', 'virtual_environment', 'dependencies'})
        self.assertTrue(all(value is None for value in report['dependencies'].values()))
        self.assertNotIn(PRIVATE, json.dumps(report))
        with patch.object(launch.metadata, 'version', return_value='2.7.1'):
            self.assertEqual(launch.runtime_info()['dependencies']['discord.py'], '2.7.1')

    def test_each_missing_dependency_reports_its_import_stage(self):
        cases = (('discord', Stage.IMPORT_DISCORD), ('asyncpg', Stage.IMPORT_ASYNCPG),
                 ('dotenv', Stage.IMPORT_DOTENV), ('PIL.Image', Stage.IMPORT_PIL))
        for missing, stage in cases:
            def load(name):
                if name == missing:
                    raise ModuleNotFoundError(PRIVATE)
            with (self.subTest(module=missing), patch.object(launch.importlib, 'import_module', side_effect=load),
                    self.assertRaises(launch.StartupFailure) as caught):
                launch.check_dependencies()
            self.assertEqual(caught.exception.details['stage'], stage.value)

    def test_cli_mode_gates_precede_configuration_and_imports(self):
        for args in (['--diagnose-only'], ['--diagnose-only', '--authorize-discord-login'],
                ['--diagnose-only', '--authorize-read-only-connection'],
                ['--diagnose-only', '--authorize-read-only-connection', '--authorize-discord-login', '--authorize-database-writes'],
                ['--authorize-read-only-connection', '--authorize-database-writes', '--authorize-discord-login']):
            with (self.subTest(args=args), redirect_stdout(StringIO()), patch.object(launch,
                    'runtime_info', side_effect=AssertionError('Gate must precede runtime work'))):
                self.assertEqual(launch.main(args), 2)

    def test_bot_import_error_reports_once_without_disclosing_message(self):
        with (patch.dict(os.environ, environment(), clear=True), patch.object(launch, 'check_dependencies'),
                patch.object(launch, 'runtime_info', return_value={}), redirect_stdout(StringIO()) as output,
                patch.object(launch.importlib, 'import_module', side_effect=ImportError(PRIVATE)) as load):
            self.assertEqual(launch.main(['--authorize-database-writes', '--authorize-discord-login']), 1)
        load.assert_called_once_with('bot')
        self.assertIn('"stage": "BOT_IMPORT"', output.getvalue())
        self.assertIn('"family": "DEPENDENCY_IMPORT"', output.getvalue())
        self.assertNotIn(PRIVATE, output.getvalue())

    def test_script_entry_uses_canonical_module_for_error_identity(self):
        tree = ast.parse((PROJECT / 'private_playtest.py').read_text(encoding='utf-8'))
        entry = tree.body[-1]
        with patch.object(launch, 'main', return_value=7) as run, self.assertRaises(SystemExit) as caught:
            exec(compile(ast.fix_missing_locations(ast.Module(body=[entry], type_ignores=[])), 'entry', 'exec'),
                 {'__name__': '__main__'})
        self.assertEqual(caught.exception.code, 7)
        run.assert_called_once_with()


class ReadOnlyFlowTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        cfg = config()
        return SimpleNamespace(application=SimpleNamespace(id=cfg.application_id, bot_public=False),
            user=SimpleNamespace(bot=True), login=AsyncMock(), close=AsyncMock(),
            fetch_guild=AsyncMock(return_value=SimpleNamespace(id=cfg.guild_id)))

    def connection(self):
        con = Connection()
        con.close, con.terminate = AsyncMock(), Mock()
        return con

    async def invoke(self, client, con):
        with (patch.object(discord, 'Client', return_value=client),
                patch.object(asyncpg, 'connect', new_callable=AsyncMock, return_value=con) as connect,
                patch.object(asyncpg, 'create_pool', side_effect=AssertionError('Pool forbidden')),
                patch.object(Repository, 'connect', side_effect=AssertionError('Game startup forbidden'))):
            await launch.diagnose_read_only(read_config())
        return connect

    def test_read_only_authorization_cannot_load_writable_bot_configuration(self):
        env = environment()
        env['DAO_PLAYTEST_AUTHORIZED'] = 'read-only-and-discord'
        cfg = launch.PrivatePlaytestConfig.from_environment(env, readonly=True)
        self.assertTrue(cfg.readonly)
        with self.assertRaises(launch.PlaytestRejected):
            launch.PrivatePlaytestConfig.from_environment(env)
        self.assertEqual(cfg.connection_kwargs()['server_settings']['default_transaction_read_only'], 'on')

    async def test_read_only_config_cannot_open_writable_repository_pool(self):
        with patch.object(asyncpg, 'create_pool', new_callable=AsyncMock) as create:
            with self.assertRaises(launch.PlaytestRejected):
                await launch.create_private_pool(read_config())
        create.assert_not_awaited()

    async def test_diagnostic_rejects_writable_config_before_constructing_client(self):
        with patch.object(discord, 'Client') as create, self.assertRaises(launch.PlaytestRejected):
            await launch.diagnose_read_only(config())
        create.assert_not_called()

    async def test_success_checks_only_rest_and_fixed_read_only_catalog_then_closes(self):
        client, con = self.client(), self.connection()
        connect = await self.invoke(client, con)
        client.login.assert_awaited_once_with(config().token)
        client.fetch_guild.assert_awaited_once_with(config().guild_id)
        self.assertEqual(connect.call_args.kwargs['server_settings']['default_transaction_read_only'], 'on')
        self.assertNotIn('dsn', connect.call_args.kwargs)
        self.assertEqual(con.calls, [('begin', {'readonly': True}), ('fetch', launch.IDENTITY_SQL, ()), ('end',)])
        con.close.assert_awaited_once_with(timeout=5)
        client.close.assert_awaited_once_with()

    async def test_token_rejection_stops_before_database_and_closes_http(self):
        client = self.client()
        client.login.side_effect = discord.LoginFailure(PRIVATE)
        with (patch.object(discord, 'Client', return_value=client),
                patch.object(asyncpg, 'connect', new_callable=AsyncMock) as connect,
                self.assertRaises(launch.StartupFailure) as caught):
            await launch.diagnose_read_only(read_config())
        self.assertEqual(caught.exception.details['stage'], 'DISCORD_LOGIN')
        self.assertEqual(caught.exception.details['family'], 'DISCORD_AUTHENTICATION')
        connect.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_public_bot_is_a_specific_rejection_before_database(self):
        client = self.client()
        client.application.bot_public = True
        with (patch.object(discord, 'Client', return_value=client),
                patch.object(asyncpg, 'connect', new_callable=AsyncMock) as connect,
                self.assertRaises(launch.StartupFailure) as caught):
            await launch.diagnose_read_only(read_config())
        self.assertEqual(caught.exception.details['reason'], 'PUBLIC_BOT_ENABLED')
        connect.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_guild_forbidden_has_fetch_stage_and_no_database_call(self):
        client = self.client()
        client.fetch_guild.side_effect = http_error(discord.Forbidden, 403)
        with (patch.object(discord, 'Client', return_value=client),
                patch.object(asyncpg, 'connect', new_callable=AsyncMock) as connect,
                self.assertRaises(launch.StartupFailure) as caught):
            await launch.diagnose_read_only(read_config())
        self.assertEqual(caught.exception.details['stage'], 'DISCORD_GUILD_FETCH')
        self.assertEqual(caught.exception.details['http_status'], 403)
        connect.assert_not_awaited()

    async def test_database_authentication_rejection_does_not_hide_behind_discord(self):
        client = self.client()
        with (patch.object(discord, 'Client', return_value=client),
                patch.object(asyncpg, 'connect', new_callable=AsyncMock, side_effect=server_error('28P01')) as connect,
                self.assertRaises(launch.StartupFailure) as caught):
            await launch.diagnose_read_only(read_config())
        self.assertEqual(caught.exception.details['stage'], 'DATABASE_CONNECT')
        self.assertEqual(caught.exception.details['sqlstate_code'], '28P01')
        connect.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_query_timeout_preserves_query_stage_and_closes_both(self):
        client, con = self.client(), self.connection()
        con.fetchrow = AsyncMock(side_effect=TimeoutError(PRIVATE))
        with self.assertRaises(launch.StartupFailure) as caught:
            await self.invoke(client, con)
        self.assertEqual(caught.exception.details['stage'], 'DATABASE_IDENTITY_QUERY')
        con.close.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_identity_rejections_report_fixed_check_without_returned_values(self):
        cases = (('database_name', 'DATABASE_NAME_MISMATCH'), ('role_name', 'ROLE_NAME_MISMATCH'),
            ('session_role', 'SESSION_ROLE_MISMATCH'), ('database_owner', 'DATABASE_OWNER_MISMATCH'),
            ('system_identifier', 'CLUSTER_ID_MISMATCH'), ('read_only', 'READ_ONLY_MISMATCH'),
            ('search_path', 'SEARCH_PATH_MISMATCH'), ('schema_name', 'SCHEMA_MISMATCH'))
        for key, reason in cases:
            con = self.connection()
            con.row[key] = PRIVATE
            with self.subTest(key=key), self.assertRaises(launch.StartupFailure) as caught:
                await self.invoke(self.client(), con)
            self.assertEqual(caught.exception.details['stage'], 'DATABASE_IDENTITY_CHECK')
            self.assertEqual(caught.exception.details['reason'], reason)
            self.assertNotIn(PRIVATE, json.dumps(caught.exception.details))

    async def test_failed_connection_close_terminates_and_still_closes_http(self):
        client, con = self.client(), self.connection()
        con.close.side_effect = ConnectionError(PRIVATE)
        with self.assertRaises(launch.StartupFailure) as caught:
            await self.invoke(client, con)
        self.assertEqual(caught.exception.details['stage'], 'CLEANUP')
        con.terminate.assert_called_once_with()
        client.close.assert_awaited_once()

    def test_diagnostic_cli_reports_success_without_importing_bot(self):
        with (patch.dict(os.environ, environment(), clear=True), redirect_stdout(StringIO()) as output,
                patch.object(launch, 'check_dependencies'), patch.object(launch, 'runtime_info', return_value={}),
                patch.object(launch, 'diagnose_read_only', new=Mock(return_value=None)) as diagnose,
                patch.object(launch.asyncio, 'run', return_value=None),
                patch.object(launch.importlib, 'import_module', side_effect=AssertionError('Bot import forbidden'))):
            result = launch.main(['--diagnose-only', '--authorize-read-only-connection', '--authorize-discord-login'])
        self.assertEqual(result, 0)
        self.assertTrue(diagnose.call_args.args[0].readonly)
        self.assertIn('PRIVATE_PLAYTEST_DIAGNOSTIC_PASSED', output.getvalue())


class BotBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_and_gateway_have_distinct_boundaries(self):
        client = SimpleNamespace(login=AsyncMock(), connect=AsyncMock(side_effect=ConnectionError(PRIVATE)))
        with self.assertRaises(launch.StartupFailure) as caught:
            await launch.start_private_client(client, PRIVATE, reconnect=False)
        self.assertEqual(caught.exception.details['stage'], 'GATEWAY_CONNECT')
        client.login.assert_awaited_once_with(PRIVATE)
        client.connect.assert_awaited_once_with(reconnect=False)

    async def test_setup_failure_inside_login_keeps_inner_stage_and_never_connects_gateway(self):
        error = launch.StartupFailure(Stage.SETUP_COMBAT, TypeError(PRIVATE))
        client = SimpleNamespace(login=AsyncMock(side_effect=error), connect=AsyncMock())
        with self.assertRaises(launch.StartupFailure) as caught:
            await launch.start_private_client(client, PRIVATE)
        self.assertIs(caught.exception, error)
        client.connect.assert_not_awaited()

    async def test_setup_modules_sync_and_announcer_each_keep_their_own_stage(self):
        module_names = ('combat_store', 'living_dao', 'sect_halls', 'sect_v022', 'bonds_v022', 'golden_sandbox_v022')
        stages = ('SETUP_COMBAT', 'SETUP_LIVING_DAO', 'SETUP_SECT_HALLS', 'SETUP_SECT_V022', 'SETUP_BONDS_V022', 'SETUP_GOLDEN_SANDBOX')
        for failed in stages + ('GUILD_COMMAND_COPY', 'GUILD_COMMAND_SYNC', 'ANNOUNCER_START'):
            mocks = {}
            for name, stage in zip(module_names, stages):
                module = ModuleType(name)
                module.setup = AsyncMock(side_effect=TypeError(PRIVATE) if failed == stage else None)
                mocks[name] = module
            tree = SimpleNamespace(copy_global_to=Mock(side_effect=TypeError(PRIVATE) if failed == 'GUILD_COMMAND_COPY' else None),
                sync=AsyncMock(side_effect=TypeError(PRIVATE) if failed == 'GUILD_COMMAND_SYNC' else None, return_value=[]))
            loop = SimpleNamespace(is_running=Mock(return_value=False),
                start=Mock(side_effect=TypeError(PRIVATE) if failed == 'ANNOUNCER_START' else None))
            namespace = dict(PRIVATE_PLAYTEST=True, SETTINGS=config(), DSN=config(), GUILD=object(),
                Repository=SimpleNamespace(connect=AsyncMock(return_value=object())),
                logging=Mock(), qi_event_announcer=loop, os=SimpleNamespace(environ={}))
            exec(extract_function('bot.py', 'setup_hook', 'DaoBot'), namespace)
            with (self.subTest(stage=failed), patch.dict(sys.modules, mocks),
                    patch.object(launch, 'verify_discord', new_callable=AsyncMock),
                    self.assertRaises(launch.StartupFailure) as caught):
                await namespace['setup_hook'](SimpleNamespace(repo=None, tree=tree))
            self.assertEqual(caught.exception.details['stage'], failed)

    async def test_partial_developer_console_setup_stops_in_its_own_stage(self):
        mocks = {name: ModuleType(name) for name in ('combat_store', 'living_dao', 'sect_halls', 'sect_v022', 'bonds_v022', 'golden_sandbox_v022')}
        for module in mocks.values():
            module.setup = AsyncMock()
        tree = SimpleNamespace(copy_global_to=Mock(), sync=AsyncMock(return_value=[]))
        namespace = dict(PRIVATE_PLAYTEST=True, SETTINGS=config(), DSN=config(), GUILD=object(),
            Repository=SimpleNamespace(connect=AsyncMock(return_value=object())), logging=Mock(),
            qi_event_announcer=SimpleNamespace(is_running=Mock(return_value=True)),
            os=SimpleNamespace(environ={'DAO_DEV_CONSOLE': 'true'}))
        exec(extract_function('bot.py', 'setup_hook', 'DaoBot'), namespace)
        with (patch.dict(sys.modules, mocks), patch.object(launch, 'verify_discord', new_callable=AsyncMock),
                self.assertRaises(launch.StartupFailure) as caught):
            await namespace['setup_hook'](SimpleNamespace(repo=None, tree=tree))
        self.assertEqual(caught.exception.details['stage'], 'SETUP_DEV_CONSOLE')
        self.assertEqual(caught.exception.details['reason'], 'DEV_CONSOLE_CONFIGURATION_REJECTED')
        tree.sync.assert_not_awaited()

    async def test_repository_failure_does_not_skip_discord_cleanup(self):
        closed = AsyncMock()
        class Base:
            async def close(self):
                await closed()
        tree = ast.parse((PROJECT / 'bot.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DaoBot')
        cls.bases = [ast.Name(id='Base', ctx=ast.Load())]
        cls.body = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'close']
        namespace = dict(Base=Base, PRIVATE_PLAYTEST=True,
            qi_event_announcer=SimpleNamespace(is_running=lambda: False))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), 'close', 'exec'), namespace)
        client = namespace['DaoBot']()
        client.repo = SimpleNamespace(close=AsyncMock(side_effect=ConnectionError(PRIVATE)))
        with self.assertRaises(launch.StartupFailure) as caught:
            await client.close()
        self.assertEqual(caught.exception.details['stage'], 'CLEANUP')
        closed.assert_awaited_once()

    async def test_all_real_slash_commands_register_and_serialize_without_startup(self):
        # Execute definitions with synthetic settings, not the configuration loader,
        # __main__, login, run, setup_hook, database setup or command sync.
        module = ModuleType('_offline_private_registration')
        module.__dict__.update(SETTINGS=config(), PRIVATE_PLAYTEST=True, TOKEN='synthetic-token',
            GUILD_ID=config().guild_id, DSN=config(), GUILD=discord.Object(id=config().guild_id),
            DEV_MODE=False, DEV_USER_ID=0)
        tree = ast.parse((PROJECT / 'bot.py').read_text(encoding='utf-8-sig'))
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {module.__name__: module}))
            for target in ((discord.Client, 'login'), (discord.Client, 'connect'),
                    (discord.app_commands.CommandTree, 'sync'), (asyncpg, 'connect'),
                    (asyncpg, 'create_pool'), (Repository, 'connect')):
                stack.enter_context(patch.object(*target, side_effect=AssertionError('Startup forbidden')))
            skip = False
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module == 'config':
                    continue
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SETTINGS' for t in node.targets):
                    skip = True
                if isinstance(node, ast.ClassDef) and node.name == 'DaoBot':
                    skip = False
                if skip or (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                        and isinstance(node.test.left, ast.Name) and node.test.left.id == '__name__'):
                    continue
                exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), 'bot.py', 'exec'), module.__dict__)
            commands = module.bot.tree.get_commands()
            self.assertEqual(len(commands), 100)  # Discord's limit; new features go into panels now.
            self.assertEqual(len({c.name for c in commands}), 100)
            for command in commands:
                self.assertEqual(command.to_dict(module.bot.tree)['name'], command.name)
            await module.bot.close()


if __name__ == '__main__':
    unittest.main()
