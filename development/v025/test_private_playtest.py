"""Private launch isolation, simulated identities and startup ordering; no I/O."""
import ast
from contextlib import asynccontextmanager, redirect_stdout
from dataclasses import replace
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import private_playtest as launch
from storage import Repository, SCHEMA


PROJECT = Path(__file__).resolve().parent


def environment():
    return dict(APP_ENV='private-playtest', DAO_PLAYTEST_AUTHORIZED='writes-and-discord',
        DAO_PLAYTEST_HOST='127.0.0.1', DAO_PLAYTEST_PORT='55432',
        DAO_PLAYTEST_DATABASE='dao_playtest_v023_v025_private', DAO_PLAYTEST_USER='dao_playtest_v025_private',
        DAO_V025_CANDIDATE='explicitly-authorized',
        DAO_PLAYTEST_SYSTEM_IDENTIFIER='7390000000000000001',
        DAO_PLAYTEST_APPLICATION_ID='123456789012345678', DAO_PLAYTEST_GUILD_ID='223456789012345678',
        DAO_PLAYTEST_TOKEN='synthetic-test-token', DAO_PLAYTEST_PASSWORD='synthetic-test-password')


def config():
    return launch.PrivatePlaytestConfig.from_environment(environment())


def identity():
    cfg = config()
    return dict(database_name=cfg.database, role_name=cfg.user, session_role=cfg.user,
        database_owner=cfg.user, system_identifier=str(cfg.system_identifier), version=180006,
        read_only='on', search_path='public, pg_catalog', schema_name='public', can_create=True,
        elevated=False, memberships=False, event_triggers=False)


class Connection:
    def __init__(self, row=None):
        self.row = identity() if row is None else row
        self.calls = []

    @asynccontextmanager
    async def transaction(self, **kwargs):
        self.calls.append(('begin', kwargs))
        try:
            yield
        finally:
            self.calls.append(('end',))

    async def fetchrow(self, query, *args):
        self.calls.append(('fetch', query, args))
        return self.row


def extract_function(filename, name, class_name=None):
    tree = ast.parse((PROJECT / filename).read_text(encoding='utf-8-sig'))
    nodes = tree.body
    if class_name:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == class_name).body
    node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    return compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), filename, 'exec')


class ConfigurationTests(unittest.TestCase):
    def test_dedicated_target_is_explicit_and_secrets_are_not_in_repr(self):
        cfg = config()
        self.assertEqual(cfg.port, 55432)
        self.assertNotIn(cfg.token, repr(cfg))
        self.assertNotIn(cfg.password, repr(cfg))

    def test_missing_or_wrong_authorization_is_rejected(self):
        for name, values in {'APP_ENV': (None, '', 'production', 'test'),
                'DAO_PLAYTEST_AUTHORIZED': (None, '', 'true', 'writes')}.items():
            for value in values:
                env = environment()
                if value is None:
                    env.pop(name)
                else:
                    env[name] = value
                with self.subTest(name=name, value=value), self.assertRaises(launch.PlaytestRejected):
                    launch.PrivatePlaytestConfig.from_environment(env)

    def test_each_missing_target_or_credential_is_rejected_without_fallback(self):
        for name in environment():
            if name == 'DAO_V025_CANDIDATE':
                continue  # Enforced by candidate_policy_v025.authorize; tested below.
            env = environment()
            env.pop(name)
            with self.subTest(name=name), self.assertRaises(launch.PlaytestRejected):
                launch.PrivatePlaytestConfig.from_environment(env)

    def test_v025_candidate_requires_explicit_authorization_and_sandbox_target(self):
        from candidate_policy_v025 import authorize
        authorize(config(), environment())
        for value in (None, '', 'yes', 'explicitly-authorized '):
            env = environment()
            env.pop('DAO_V025_CANDIDATE')
            if value is not None:
                env['DAO_V025_CANDIDATE'] = value
            with self.subTest(value=value), self.assertRaises(launch.PlaytestRejected):
                authorize(config(), env)
        for field, value in (('database', 'dao_playtest_v023_rc1'), ('user', 'dao_playtest_rc1')):
            with self.subTest(field=field), self.assertRaises(launch.PlaytestRejected):
                authorize(replace(config(), **{field: value}), environment())

    def test_production_default_ports_and_ambiguous_hosts_are_rejected(self):
        for name, values in {'host': ('localhost', '::1', '127.0.0.2', '/tmp', ''),
                'port': (5432, 0, 1023, 65536, True, '55432')}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(launch.PlaytestRejected):
                    replace(config(), **{name: value}).validate()

    def test_old_integration_and_player_database_names_are_rejected(self):
        for database in ('dao_eternity', 'dao_eternity_test', 'postgres',
                'dao_playtest_v023_', 'dao_playtest_v023_' + 'a' * 25,
                'dao_playtest_v023_UPPER', 'dao_playtest_v023_a;select'):
            with self.subTest(database=database), self.assertRaises(launch.PlaytestRejected):
                replace(config(), database=database).validate()

    def test_old_integration_and_admin_roles_are_rejected(self):
        for user in ('postgres', 'dao_test_user', 'dao_playtest_', 'dao_playtest_' + 'a' * 33,
                'dao_playtest_bad-role', 'dao_playtest_UPPER'):
            with self.subTest(user=user), self.assertRaises(launch.PlaytestRejected):
                replace(config(), user=user).validate()

    def test_identifiers_have_type_and_range_checks(self):
        for name, values in {'system_identifier': (0, -1, 2**63, True, '123'),
                'application_id': (0, -1, 2**64, True), 'guild_id': (0, -1, 2**64, True)}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(launch.PlaytestRejected):
                    replace(config(), **{name: value}).validate()

    def test_numeric_configuration_rejects_signs_whitespace_and_non_strings(self):
        for value in ('+55432', ' 55432', '55432 ', '5.5', 55432, None):
            env = environment()
            env['DAO_PLAYTEST_PORT'] = value
            with self.subTest(value=value), self.assertRaises(launch.PlaytestRejected):
                launch.PrivatePlaytestConfig.from_environment(env)

    def test_empty_credentials_are_rejected(self):
        for name, values in {'token': ('', '   ', None), 'password': ('', None)}.items():
            for value in values:
                with self.subTest(name=name), self.assertRaises(launch.PlaytestRejected):
                    replace(config(), **{name: value}).validate()

    def test_connection_parameters_do_not_have_dsn_service_or_passfile_fallback(self):
        cfg = config()
        parameters = cfg.connection_kwargs()
        for name in ('host', 'port', 'database', 'user', 'password'):
            self.assertEqual(parameters[name], getattr(cfg, name))
        self.assertIs(parameters['ssl'], False)
        self.assertNotIn('dsn', parameters)
        self.assertEqual(parameters['server_settings']['search_path'], 'public, pg_catalog')
        self.assertEqual(parameters['server_settings']['statement_timeout'], '30000')

    def test_ambient_production_configuration_is_removed_only_in_private_process(self):
        env = environment()
        poison = dict(PGHOST='player-host', PGPORT='5432', PGSERVICE='players',
            PGPASSFILE='secret', DATABASE_URL='postgres://production', DISCORD_TOKEN='production-token',
            DISCORD_GUILD_ID='1', DEV_MODE='true', DEV_USER_ID='1')
        with patch.dict(os.environ, {**env, **poison}, clear=True):
            launch.sanitize_environment()
            for key in poison:
                self.assertNotIn(key, os.environ)
            self.assertEqual(dict(os.environ), env)

    def test_private_config_path_never_loads_dotenv(self):
        namespace = dict(os=os, load_dotenv=Mock(side_effect=AssertionError('dotenv forbidden')))
        exec(extract_function('config.py', 'load_config'), namespace)
        with patch.dict(os.environ, environment(), clear=True):
            self.assertEqual(namespace['load_config'](), config())
        namespace['load_dotenv'].assert_not_called()

    def test_private_configuration_failure_never_falls_back_to_dotenv(self):
        namespace = dict(os=os, load_dotenv=Mock(side_effect=AssertionError('dotenv forbidden')))
        exec(extract_function('config.py', 'load_config'), namespace)
        with patch.dict(os.environ, {'APP_ENV': 'private-playtest', 'DATABASE_URL': 'production'}, clear=True):
            with self.assertRaises(launch.PlaytestRejected):
                namespace['load_config']()
        namespace['load_dotenv'].assert_not_called()

    def test_cli_requires_both_authorizations_before_config_or_startup(self):
        for argv in ([], ['--authorize-database-writes'], ['--authorize-discord-login']):
            with (self.subTest(argv=argv), patch.object(launch.PrivatePlaytestConfig,
                    'from_environment', side_effect=AssertionError('config read too early')),
                    redirect_stdout(StringIO())):
                self.assertEqual(launch.main(argv), 2)

    def test_cli_invalid_configuration_stops_before_bot_import(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(StringIO()) as output:
            self.assertEqual(launch.main(['--authorize-database-writes', '--authorize-discord-login']), 1)
            self.assertNotIn('DAO_PLAYTEST_AUTHORIZED', os.environ)
        self.assertIn('PRIVATE_PLAYTEST_STOPPED', output.getvalue())


class DatabaseIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_identity_checked_in_read_only_transaction(self):
        con = Connection()
        await launch.verify_connection(con, config())
        self.assertEqual(con.calls[0], ('begin', {'readonly': True}))
        self.assertEqual(con.calls[1], ('fetch', launch.IDENTITY_SQL, ()))
        self.assertEqual(con.calls[-1], ('end',))

    async def test_each_database_cluster_or_role_mismatch_rejects(self):
        for key in ('database_name', 'role_name', 'session_role', 'database_owner', 'system_identifier'):
            row = identity()
            row[key] = 'wrong-target'
            with self.subTest(key=key), self.assertRaises(launch.StartupFailure):
                await launch.verify_connection(Connection(row), config())

    async def test_search_path_and_writable_transaction_mismatches_reject(self):
        for key, value in (('read_only', 'off'), ('search_path', '"$user", public'),
                ('schema_name', 'pg_catalog'), ('can_create', False)):
            row = identity()
            row[key] = value
            with self.subTest(key=key), self.assertRaises(launch.StartupFailure):
                await launch.verify_connection(Connection(row), config())

    async def test_elevated_membership_and_enabled_event_triggers_reject(self):
        for key in ('elevated', 'memberships', 'event_triggers'):
            row = identity()
            row[key] = True
            with self.subTest(key=key), self.assertRaises(launch.StartupFailure):
                await launch.verify_connection(Connection(row), config())

    async def test_postgres_version_must_be_18_and_native_integer(self):
        for version in (170999, 190000, '180006', True):
            row = identity()
            row['version'] = version
            with self.subTest(version=version), self.assertRaises(launch.StartupFailure):
                await launch.verify_connection(Connection(row), config())

    async def test_boolean_catalog_values_are_strict(self):
        for key, value in (('can_create', 1), ('elevated', 0), ('memberships', None), ('event_triggers', 0)):
            row = identity()
            row[key] = value
            with self.subTest(key=key), self.assertRaises(launch.StartupFailure):
                await launch.verify_connection(Connection(row), config())

    async def test_unavailable_identity_is_not_guessed_or_bypassed(self):
        con = Connection()
        con.row = None
        with self.assertRaises(launch.StartupFailure):
            await launch.verify_connection(con, config())
        self.assertEqual(con.calls[-1], ('end',))

    async def test_catalog_permission_error_propagates_without_retry_or_ddl(self):
        con = Connection()
        con.fetchrow = AsyncMock(side_effect=PermissionError('synthetic forbidden control metadata'))
        with self.assertRaises(launch.StartupFailure) as caught:
            await launch.verify_connection(con, config())
        self.assertEqual(caught.exception.details['stage'], 'DATABASE_IDENTITY_QUERY')
        self.assertEqual(caught.exception.details['family'], 'OS_ERROR')
        con.fetchrow.assert_awaited_once_with(launch.IDENTITY_SQL)
        self.assertEqual(con.calls[-1], ('end',))

    async def test_every_new_pool_connection_has_identity_guard(self):
        marker = object()
        with (patch('asyncpg.create_pool', new_callable=AsyncMock, return_value=marker) as create,
                patch.object(launch, 'sanitize_environment')):
            self.assertIs(await launch.create_private_pool(config()), marker)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['min_size'], 1)
        self.assertEqual(kwargs['max_size'], 5)
        self.assertNotIn('dsn', kwargs)
        await kwargs['init'](Connection())
        row = identity()
        row['system_identifier'] = 'wrong-cluster'
        with self.assertRaises(launch.StartupFailure):
            await kwargs['init'](Connection(row))


class DiscordAndStartupTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        cfg = config()
        return SimpleNamespace(application=SimpleNamespace(id=cfg.application_id, bot_public=False),
            user=SimpleNamespace(bot=True), fetch_guild=AsyncMock(return_value=SimpleNamespace(id=cfg.guild_id)))

    async def test_matching_private_application_and_guild_are_verified(self):
        client = self.client()
        await launch.verify_discord(client, config())
        client.fetch_guild.assert_awaited_once_with(config().guild_id)

    async def test_wrong_public_or_non_bot_application_rejects_before_guild_fetch(self):
        for variant in ('missing-app', 'wrong-app', 'public', 'missing-user', 'non-bot'):
            client = self.client()
            if variant == 'missing-app': client.application = None
            elif variant == 'wrong-app': client.application.id += 1
            elif variant == 'public': client.application.bot_public = True
            elif variant == 'missing-user': client.user = None
            else: client.user.bot = False
            with self.subTest(variant=variant), self.assertRaises(launch.StartupFailure):
                await launch.verify_discord(client, config())
            client.fetch_guild.assert_not_awaited()

    async def test_wrong_guild_rejects(self):
        client = self.client()
        client.fetch_guild.return_value.id += 1
        with self.assertRaises(launch.StartupFailure):
            await launch.verify_discord(client, config())

    async def test_discord_identity_rejection_prevents_database_startup(self):
        repo = SimpleNamespace(connect=AsyncMock())
        namespace = dict(PRIVATE_PLAYTEST=True, SETTINGS=config(), DSN=config(), Repository=repo)
        exec(extract_function('bot.py', 'setup_hook', 'DaoBot'), namespace)
        with patch.object(launch, 'verify_discord', side_effect=launch.PlaytestRejected('TEST')):
            with self.assertRaises(launch.PlaytestRejected):
                await namespace['setup_hook'](SimpleNamespace(repo=None))
        repo.connect.assert_not_awaited()

    async def test_repository_uses_verified_pool_before_schema_setup(self):
        con = SimpleNamespace(execute=AsyncMock())
        pool = Mock()
        @asynccontextmanager
        async def acquire():
            yield con
        pool.acquire = acquire
        with (patch.object(launch, 'create_private_pool', new_callable=AsyncMock, return_value=pool) as create,
                patch('asyncpg.create_pool', new_callable=AsyncMock) as ordinary):
            repo = await Repository.connect(config())
        create.assert_awaited_once_with(config())
        ordinary.assert_not_awaited()
        con.execute.assert_awaited_once_with(SCHEMA)
        pool.terminate.assert_not_called()
        self.assertIs(repo.pool, pool)

    async def test_repository_terminates_new_pool_when_schema_setup_fails(self):
        for error in (RuntimeError('synthetic migration failure'), KeyboardInterrupt()):
            con = SimpleNamespace(execute=AsyncMock(side_effect=error))
            pool = Mock()
            @asynccontextmanager
            async def acquire():
                yield con
            pool.acquire = acquire
            with self.subTest(error=type(error).__name__), patch.object(launch,
                    'create_private_pool', new_callable=AsyncMock, return_value=pool):
                with self.assertRaises(type(error)):
                    await Repository.connect(config())
            pool.terminate.assert_called_once_with()


class LaunchProcedureTests(unittest.TestCase):
    def test_powershell_authorization_gates_precede_prompts_and_python(self):
        text = (PROJECT / 'launch_private_playtest.ps1').read_text(encoding='utf-8-sig')
        gate = text.index('elseif (-not ($AuthorizeDatabaseWrites -and $AuthorizeDiscordLogin)')
        self.assertLess(gate, text.index('Read-Host'))
        self.assertLess(gate, text.index('& $daoPythonCommand.Source'))
        self.assertIn('--authorize-database-writes --authorize-discord-login', text)

    def test_powershell_review_precedes_secure_credentials_and_restores_environment(self):
        text = (PROJECT / 'launch_private_playtest.ps1').read_text(encoding='utf-8-sig')
        self.assertLess(text.index('LAUNCH PRIVATE PLAYTEST'), text.index('Read-Host $daoPrompt -AsSecureString'))
        self.assertIn('ZeroFreeBSTR', text)
        self.assertIn("[Environment]::SetEnvironmentVariable($daoName, $daoSaved[$daoName], 'Process')", text)
        self.assertIn('Pop-Location', text)

    def test_private_mode_disables_developer_mutation_commands(self):
        tree = ast.parse((PROJECT / 'bot.py').read_text(encoding='utf-8-sig'))
        assignments = {node.targets[0].id: node.value for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        namespace = dict(PRIVATE_PLAYTEST=True, os=SimpleNamespace(getenv=Mock(side_effect=AssertionError('ambient dev config'))))
        self.assertIs(eval(compile(ast.Expression(assignments['DEV_MODE']), 'bot.py', 'eval'), namespace), False)
        self.assertEqual(eval(compile(ast.Expression(assignments['DEV_USER_ID']), 'bot.py', 'eval'), namespace), 0)


if __name__ == '__main__':
    unittest.main()
