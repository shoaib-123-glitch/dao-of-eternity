"""Offline security tests: all driver connections are mocked; SQL is never executed."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from integration_support import (
    CONFIG_KEYS, IDENTITY_SQL, PRIVILEGES_SQL, PRIVILEGE_FLAGS, NAMESPACE_SQL,
    PATH_SQL, INVENTORY_SQL, IntegrationSafetyError, IsolatedTestDatabase,
    TestDatabaseConfig, PreflightReport, _table_definitions, preflight_catalog,
)


def environment():
    return dict(zip(CONFIG_KEYS, ('localhost', '5432', 'dao_eternity_test',
                                  'dao_test_user', 'synthetic-password-not-a-credential')))


def identity():
    return {'database_name': 'dao_eternity_test', 'role_name': 'dao_test_user',
            'session_role': 'dao_test_user', 'server_address': '127.0.0.1',
            'server_port': 5432, 'read_only': 'on', 'default_read_only': 'on',
            'version': 170000, 'temp_schema': 0, 'in_recovery': False,
            'role_oid': 1000, 'can_login': True, 'elevated': False,
            'other_memberships': False, 'can_create_schema': True,
            'enabled_event_triggers': False}


def object_row(kind, oid, name, *, parent=0, owner=1000, definition='',
               classid=1259, subid=0):
    return dict(kind=kind, classid=classid, oid=oid, subid=subid, name=name,
                parent=parent, owner=owner, definition=definition,
                acl_safe=True, dependencies_safe=True)


class ConnectionDouble:
    """SQL recorder, not a PostgreSQL permission, commit or rollback emulator."""
    def __init__(self):
        self.identity = identity()
        self.privileges = dict.fromkeys(PRIVILEGE_FLAGS, False)
        self.schema = None
        self.namespace = None
        self.tables = []
        self.dependents = []
        self.bad_path = None
        self.events = []
        self.bad_object_acl = False
        self.bad_dependencies = False
        self.fetchrow = AsyncMock(side_effect=self._fetchrow)
        self.fetchval = AsyncMock(return_value=None)
        self.fetch = AsyncMock(side_effect=self._fetch)
        self.execute = AsyncMock(side_effect=self._execute)
        self.close = AsyncMock()
        self.terminate = MagicMock()
        self.transaction = MagicMock()
        self.transaction.return_value.__aenter__.return_value = None

    async def _fetchrow(self, sql, *args):
        self.events.append(('read', sql))
        if sql == IDENTITY_SQL:
            return deepcopy(self.identity)
        if sql == PRIVILEGES_SQL:
            return deepcopy(self.privileges)
        if sql == NAMESPACE_SQL:
            return deepcopy(self.namespace)
        if sql == PATH_SQL:
            return self.bad_path or {'current_schema': self.schema,
                                     'schemas': [self.schema], 'temp_schema': 0}
        raise AssertionError('Unexpected catalog query')

    def inventory(self):
        if not self.namespace:
            return []
        rows = [object_row('namespace', self.namespace['oid'], self.schema, classid=2615)]
        for table in self.tables:
            details = dict(kind=table['kind'], namespace=self.namespace['oid'],
                           primary_index=table['kind'] == 'i')
            rows.append(object_row('relation', table['oid'], table['name'],
                                   owner=table['owner'], definition=json.dumps(details)))
            if table['kind'] == 'r':
                rows.append(object_row('relation', table['oid'] + 10000, table['name'] + '_pkey',
                                       parent=table['oid'], definition=json.dumps(
                                           dict(kind='i', namespace=self.namespace['oid'], primary_index=True))))
                rows.append(object_row('constraint', table['oid'] + 20000, table['name'] + '_pkey',
                                       parent=table['oid'], classid=2606, definition='PRIMARY KEY'))
                if table['name'] in ('dao_player_bonds', 'dao_dual_requests'):
                    rows.append(object_row('constraint', table['oid'] + 25000, table['name'] + '_check',
                                           parent=table['oid'], classid=2606, definition='CHECK'))
                rows.append(object_row('type', table['oid'] + 30000, table['name'],
                                       parent=table['oid'], classid=1247,
                                       definition=json.dumps(dict(kind='c', element=0))))
                rows.append(object_row('type', table['oid'] + 40000, '_' + table['name'],
                                       classid=1247, definition=json.dumps(
                                           dict(kind='b', element=table['oid'] + 30000))))
        rows.extend(deepcopy(self.dependents))
        if self.bad_object_acl:
            rows[-1]['acl_safe'] = False
        if self.bad_dependencies:
            rows[-1]['dependencies_safe'] = False
        return rows

    async def _fetch(self, sql, *args):
        if sql != INVENTORY_SQL:
            raise AssertionError('Unexpected inventory query')
        return self.inventory()

    async def _execute(self, sql, *args):
        self.events.append(('execute', sql))
        if sql.startswith('SET default_transaction_read_only = '):
            self.identity['read_only'] = self.identity['default_read_only'] = sql.rsplit(' ', 1)[1]
        elif sql.startswith('CREATE SCHEMA '):
            self.schema = sql.split('"')[1]
            self.namespace = {'oid': 2000, 'owner': self.identity['role_oid'], 'acl_safe': True}
        elif sql.startswith('CREATE TABLE '):
            self.tables.append({'name': sql.split('"')[3], 'oid': 3000 + len(self.tables),
                                'owner': self.identity['role_oid'], 'kind': 'r'})
        elif not sql.startswith(('LOCK TABLE ', 'DROP TABLE ', 'DROP SCHEMA ')):
            raise AssertionError('Unexpected write statement')

    def checkout_copy(self):
        con = ConnectionDouble()
        con.schema = self.schema
        con.namespace = deepcopy(self.namespace)
        con.tables = deepcopy(self.tables)
        con.dependents = deepcopy(self.dependents)
        return con


class ConfigurationSafetyTests(unittest.TestCase):
    def test_all_five_variables_are_mandatory(self):
        for key in CONFIG_KEYS:
            with self.subTest(key=key):
                values = environment()
                del values[key]
                with self.assertRaises(IntegrationSafetyError):
                    TestDatabaseConfig.from_environment(values)

    def test_no_production_or_pg_variable_fallback(self):
        values = {'DATABASE_URL': 'postgresql://postgres:synthetic@remote/live',
                  'PGHOST': '127.0.0.1', 'PGPORT': '5432',
                  'PGDATABASE': 'dao_eternity_test', 'PGUSER': 'dao_test_user',
                  'PGPASSWORD': 'synthetic'}
        with self.assertRaises(IntegrationSafetyError):
            TestDatabaseConfig.from_environment(values)

    def test_only_exact_database_and_role_are_allowed(self):
        for key, value in (('DAO_TEST_DATABASE', 'live_game'),
                           ('DAO_TEST_DATABASE', 'dao_eternity_test?host=remote'),
                           ('DAO_TEST_USER', 'postgres'),
                           ('DAO_TEST_USER', 'dao_test_user@remote')):
            with self.subTest(key=key):
                values = environment()
                values[key] = value
                with self.assertRaises(IntegrationSafetyError):
                    TestDatabaseConfig.from_environment(values)

    def test_only_exact_ipv4_loopback_host_names_are_allowed(self):
        for value in ('remote', '192.168.1.2', '127.0.0.2', '::1', '/tmp',
                      'localhost.', 'localhost,remote', 'localhost:5432',
                      '127.0.0.1\n', 'LOCALHOST'):
            with self.subTest(host=value):
                values = environment()
                values['DAO_TEST_HOST'] = value
                with self.assertRaises(IntegrationSafetyError):
                    TestDatabaseConfig.from_environment(values)

    def test_ports_must_be_explicit_and_valid(self):
        for value in ('', '0', '65536', '-1', '+5432', '5432,5433', ' 5432', '５４３２'):
            with self.subTest(port=value):
                values = environment()
                values['DAO_TEST_PORT'] = value
                with self.assertRaises(IntegrationSafetyError):
                    TestDatabaseConfig.from_environment(values)

    def test_direct_constructor_cannot_bypass_validation(self):
        with self.assertRaises(IntegrationSafetyError):
            TestDatabaseConfig('remote', 5432, 'dao_eternity_test', 'dao_test_user', 'synthetic')
        with self.assertRaises(IntegrationSafetyError):
            TestDatabaseConfig('localhost', True, 'dao_eternity_test', 'dao_test_user', 'synthetic')

    def test_valid_configuration_is_redacted(self):
        for host in ('localhost', '127.0.0.1'):
            values = environment()
            values['DAO_TEST_HOST'] = host
            config = TestDatabaseConfig.from_environment(values)
            self.assertEqual(config.port, 5432)
            self.assertNotIn(config.password, repr(config))
            self.assertNotIn(config.password, str(config))

    def test_environment_error_does_not_echo_values(self):
        values = environment()
        values['DAO_TEST_PORT'] = 'synthetic-sensitive-invalid-value'
        with self.assertRaises(IntegrationSafetyError) as raised:
            TestDatabaseConfig.from_environment(values)
        self.assertNotIn(values['DAO_TEST_PORT'], str(raised.exception))

    def test_schema_loader_accepts_only_existing_table_declarations(self):
        tables = _table_definitions()
        self.assertTrue({'characters', 'combat_sessions', 'living_dao_players',
                         'dao_households', 'dao_player_bonds', 'dao_dual_requests'} <= tables.keys())
        with patch('integration_manifest.read_schema_literals', return_value={'storage': 'DELETE FROM characters;'}):
            with self.assertRaises(IntegrationSafetyError):
                _table_definitions()



class ConnectionSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = TestDatabaseConfig.from_environment(environment())
        self.con = ConnectionDouble()
        self.driver_patch = patch('integration_support.asyncpg.connect', AsyncMock(return_value=self.con))
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)

    async def test_connection_requires_explicit_opt_in(self):
        with self.assertRaises(IntegrationSafetyError):
            await preflight_catalog(self.config)
        self.driver.assert_not_awaited()

    async def test_unvalidated_configuration_never_connects(self):
        with self.assertRaises(IntegrationSafetyError):
            await preflight_catalog({}, allow_connection=True)
        self.driver.assert_not_awaited()

    async def test_explicit_pinned_parameters_and_readonly_startup(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'synthetic-production-value',
                                     'PGHOST': 'remote', 'PGUSER': 'postgres'}):
            result = await preflight_catalog(self.config, allow_connection=True)
        self.assertIs(type(result), PreflightReport)
        self.assertFalse(hasattr(result, 'execute'))
        self.assertNotIn(self.config.password, json.dumps(asdict(result)))
        kwargs = self.driver.await_args.kwargs
        self.assertEqual(kwargs['host'], '127.0.0.1')
        self.assertEqual(kwargs['database'], 'dao_eternity_test')
        self.assertEqual(kwargs['user'], 'dao_test_user')
        self.assertEqual(kwargs['port'], self.config.port)
        self.assertEqual(kwargs['password'], self.config.password)
        self.assertNotIn('dsn', kwargs)
        self.assertEqual(kwargs['server_settings']['search_path'], 'pg_catalog')
        self.assertEqual(kwargs['server_settings']['default_transaction_read_only'], 'on')
        self.con.execute.assert_not_awaited()
        self.con.fetchval.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_identity_and_privilege_failures_close_without_writes(self):
        cases = ({'database_name': 'production'}, {'role_name': 'postgres'},
                 {'session_role': 'postgres'}, {'server_address': '192.168.1.1'},
                 {'server_address': None}, {'server_port': 6543}, {'read_only': 'off'},
                 {'default_read_only': 'off'}, {'version': 140000}, {'version': 190000},
                 {'temp_schema': 12}, {'in_recovery': True}, {'elevated': True},
                 {'can_login': False}, {'other_memberships': True},
                 {'enabled_event_triggers': True})
        for overrides in cases:
            with self.subTest(field=next(iter(overrides))):
                con = ConnectionDouble()
                con.identity.update(overrides)
                self.driver.return_value = con
                with self.assertRaises(IntegrationSafetyError):
                    await preflight_catalog(self.config, allow_connection=True)
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()

    async def test_missing_identity_is_rejected(self):
        self.con.fetchrow.return_value = None
        self.con.fetchrow.side_effect = None
        with self.assertRaises(IntegrationSafetyError):
            await preflight_catalog(self.config, allow_connection=True)
        self.con.execute.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_driver_error_is_redacted(self):
        self.driver.side_effect = RuntimeError(self.config.password)
        with self.assertRaises(IntegrationSafetyError) as raised:
            await preflight_catalog(self.config, allow_connection=True)
        self.assertNotIn(self.config.password, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    async def test_failed_close_terminates_rejected_connection(self):
        self.con.identity['elevated'] = True
        self.con.close.side_effect = RuntimeError('synthetic-close-failure')
        with self.assertRaises(IntegrationSafetyError):
            await preflight_catalog(self.config, allow_connection=True)
        self.con.terminate.assert_called_once()

    async def test_effective_access_flags_and_unknown_results_fail_closed(self):
        for key in PRIVILEGE_FLAGS:
            for value in (True, None):
                with self.subTest(key=key, value=value):
                    con = ConnectionDouble()
                    con.privileges[key] = value
                    self.driver.return_value = con
                    with self.assertRaises(IntegrationSafetyError):
                        await preflight_catalog(self.config, allow_connection=True)
                    con.execute.assert_not_awaited()
                    con.close.assert_awaited_once()

    async def test_unreadable_privilege_catalog_fails_closed(self):
        original = self.con._fetchrow

        async def unreadable(sql, *args):
            if sql == PRIVILEGES_SQL:
                raise RuntimeError('synthetic unavailable catalog')
            return await original(sql, *args)

        self.con.fetchrow.side_effect = unreadable
        with self.assertRaises(IntegrationSafetyError):
            await preflight_catalog(self.config, allow_connection=True)
        self.con.close.assert_awaited_once()
        self.con.execute.assert_not_awaited()

    async def test_readonly_preflight_does_not_require_schema_create(self):
        self.con.identity['can_create_schema'] = False
        report = await preflight_catalog(self.config, allow_connection=True)
        self.assertFalse(report.can_create_schema)
        self.con.execute.assert_not_awaited()

    async def test_preflight_cancellation_closes_and_preserves_cancellation(self):
        self.con.fetchrow.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await preflight_catalog(self.config, allow_connection=True)
        self.con.close.assert_awaited_once()

    async def test_preflight_cleanup_errors_preserve_original_cancellation(self):
        cancellation = asyncio.CancelledError()
        self.con.fetchrow.side_effect = cancellation
        self.con.close.side_effect = RuntimeError('synthetic close failure')
        self.con.terminate.side_effect = RuntimeError('synthetic termination failure')
        with self.assertRaises(asyncio.CancelledError) as raised:
            await preflight_catalog(self.config, allow_connection=True)
        self.assertIs(raised.exception, cancellation)
        self.assertTrue(raised.exception.termination_unconfirmed)

    async def test_connect_cancellation_terminates_late_returned_connection(self):
        started = asyncio.Event()

        async def late_connection(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return self.con

        self.driver.side_effect = late_connection
        running = asyncio.create_task(preflight_catalog(self.config, allow_connection=True))
        await asyncio.wait_for(started.wait(), 2)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.con.terminate.assert_called_once()


class IsolationSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = TestDatabaseConfig.from_environment(environment())
        self.control = ConnectionDouble()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.driver_patch = patch('integration_support.asyncpg.connect', AsyncMock(return_value=self.control))
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)

    async def create(self):
        return await IsolatedTestDatabase.create(self.config, allow_database_writes=True,
                                                recovery_directory=self.directory.name)

    async def test_schema_writes_require_explicit_opt_in(self):
        with self.assertRaises(IntegrationSafetyError):
            await IsolatedTestDatabase.create(self.config)
        self.driver.assert_not_awaited()

    async def test_rejected_identity_never_reaches_schema_creation(self):
        self.control.identity['database_name'] = 'production'
        with self.assertRaises(IntegrationSafetyError):
            await self.create()
        self.control.execute.assert_not_awaited()

    async def test_existing_schema_is_never_reused_or_cleaned(self):
        self.control.namespace = {'oid': 9999, 'owner': 1000, 'acl_safe': True}
        with self.assertRaises(IntegrationSafetyError):
            await self.create()
        self.control.execute.assert_not_awaited()

    async def test_generated_schemas_are_unique_and_not_caller_supplied(self):
        first, second = IsolatedTestDatabase(self.config), IsolatedTestDatabase(self.config)
        self.assertNotEqual(first.schema, second.schema)
        self.assertRegex(first.schema, r'^dao_it_[0-9a-f]{32}$')
        self.driver.assert_not_awaited()

    async def test_setup_creates_only_owned_qualified_tables(self):
        with patch('storage.Repository.connect', AsyncMock(side_effect=AssertionError('unsafe startup'))), \
                patch('sect_v022.setup', AsyncMock(side_effect=AssertionError('setup backfill'))):
            db = await self.create()
        statements = [call.args[0] for call in self.control.execute.await_args_list]
        creates = [sql for sql in statements if sql.startswith('CREATE TABLE')]
        self.assertEqual(len(creates), len(_table_definitions()))
        self.assertTrue(all(sql.startswith(f'CREATE TABLE "{db.schema}".') for sql in creates))
        self.assertEqual(self.control.identity['read_only'], 'on')
        self.assertEqual(db._state, 'ready')
        self.assertFalse(any('public.' in sql or sql.startswith(('GRANT ', 'REVOKE ', 'UPDATE ', 'DELETE ', 'TRUNCATE '))
                             for sql in statements))

    async def test_checkout_verifies_private_schema_before_enabling_writes(self):
        db = await self.create()
        con = self.control.checkout_copy()
        self.driver.return_value = con
        async with db.acquire() as checked:
            self.assertIs(checked, con)
            self.assertEqual(con.identity['read_only'], 'off')
            write_enable = con.events.index(('execute', 'SET default_transaction_read_only = off'))
            for query in (IDENTITY_SQL, PRIVILEGES_SQL, NAMESPACE_SQL, PATH_SQL):
                self.assertLess(con.events.index(('read', query)), write_enable)
        con.close.assert_awaited_once()
        self.assertEqual(db._active, 0)

    async def test_unusable_setup_path_exits_transaction_without_table_creation(self):
        self.control.bad_path = {'current_schema': 'public', 'schemas': ['public'], 'temp_schema': 0}
        with self.assertRaises(IntegrationSafetyError):
            await self.create()
        statements = [c.args[0] for c in self.control.execute.await_args_list]
        self.assertFalse(any(sql.startswith(('CREATE TABLE ', 'DROP ')) for sql in statements))
        self.control.close.assert_awaited_once()
        self.assertIs(self.control.transaction.return_value.__aexit__.await_args.args[0], IntegrationSafetyError)

    async def test_failed_ddl_exits_transaction_without_prefix_cleanup(self):
        original = self.control._execute

        async def fail_table(sql, *args):
            if sql.startswith('CREATE TABLE '):
                raise RuntimeError('synthetic DDL failure')
            return await original(sql, *args)

        self.control.execute.side_effect = fail_table
        with self.assertRaises(IntegrationSafetyError) as raised:
            await self.create()
        self.assertEqual(raised.exception.recovery.phase, 'creating_tables')
        self.assertFalse(any(c.args[0].startswith('DROP ') for c in self.control.execute.await_args_list))
        self.control.close.assert_awaited_once()
        self.assertIs(self.control.transaction.return_value.__aexit__.await_args.args[0], RuntimeError)

    async def test_checkout_refuses_changed_namespace_or_public_fallback(self):
        db = await self.create()
        for mode in ('oid', 'owner', 'public', 'temporary', 'acl'):
            with self.subTest(mode=mode):
                con = self.control.checkout_copy()
                if mode in ('oid', 'owner'):
                    con.namespace[mode] += 1
                elif mode == 'acl':
                    con.namespace['acl_safe'] = False
                else:
                    con.bad_path = {'current_schema': db.schema,
                                    'schemas': [db.schema, 'public'] if mode == 'public' else [db.schema],
                                    'temp_schema': 123 if mode == 'temporary' else 0}
                self.driver.return_value = con
                with self.assertRaises(IntegrationSafetyError):
                    async with db.acquire():
                        self.fail('Rejected connection was yielded')
                con.execute.assert_not_awaited()
                con.close.assert_awaited_once()
        self.assertEqual(db._active, 0)

    async def test_cleanup_refuses_active_connections(self):
        db = await self.create()
        self.driver.return_value = self.control.checkout_copy()
        self.control.execute.reset_mock()
        async with db.acquire():
            with self.assertRaises(IntegrationSafetyError):
                await db.close()
        self.control.execute.assert_not_awaited()

    async def test_cleanup_rechecks_actual_identity(self):
        db = await self.create()
        self.control.identity['session_role'] = 'postgres'
        self.control.execute.reset_mock()
        with self.assertRaises(IntegrationSafetyError):
            await db.close()
        self.control.execute.assert_not_awaited()

    async def test_cleanup_refuses_changed_schema_identity(self):
        db = await self.create()
        self.control.namespace['oid'] += 1
        self.control.execute.reset_mock()
        with self.assertRaises(IntegrationSafetyError):
            await db.close()
        self.control.execute.assert_not_awaited()

    async def test_cleanup_refuses_replaced_or_unexpected_objects(self):
        for mode in ('replaced', 'other_owner', 'unexpected'):
            with self.subTest(mode=mode):
                self.control = ConnectionDouble()
                self.driver.return_value = self.control
                db = await self.create()
                if mode == 'replaced':
                    self.control.tables[0]['oid'] += 10000
                elif mode == 'other_owner':
                    self.control.tables[0]['owner'] += 1
                else:
                    self.control.tables.append({'name': 'not_created_by_run', 'oid': 9999, 'owner': 1000, 'kind': 'r'})
                self.control.execute.reset_mock()
                with self.assertRaises(IntegrationSafetyError):
                    await db.close()
                self.assertFalse(any(c.args[0].startswith('DROP ') for c in self.control.execute.await_args_list))

    async def test_cleanup_drops_only_manifest_objects_with_restrict(self):
        db = await self.create()
        self.control.execute.reset_mock()
        await db.close()
        statements = [c.args[0] for c in self.control.execute.await_args_list]
        drops = [sql for sql in statements if sql.startswith('DROP ')]
        self.assertEqual(len(drops), len(db._tables) + 1)
        self.assertTrue(all(sql.endswith(' RESTRICT') for sql in drops))
        self.assertTrue(all(re.match(rf'DROP (?:TABLE|SCHEMA) "{db.schema}"', sql) for sql in drops))
        self.assertFalse(any('CASCADE' in sql or 'public.' in sql for sql in statements))
        self.control.execute.reset_mock()
        await db.close()
        self.control.execute.assert_not_awaited()

    async def test_cleanup_error_closes_session_without_broad_retry(self):
        db = await self.create()
        original = self.control._execute

        async def fail_drop(sql, *args):
            if sql.startswith('DROP TABLE '):
                raise RuntimeError('synthetic dependency failure')
            return await original(sql, *args)

        self.control.execute.side_effect = fail_drop
        with self.assertRaises(IntegrationSafetyError) as raised:
            await db.close()
        self.assertIsNotNone(raised.exception.recovery)
        self.assertEqual(db._state, 'failed')
        self.control.close.assert_awaited_once()
        self.assertIs(self.control.transaction.return_value.__aexit__.await_args.args[0], RuntimeError)

    async def test_write_api_requires_persistent_recovery_directory_before_connect(self):
        with self.assertRaises(IntegrationSafetyError):
            await IsolatedTestDatabase.create(self.config, allow_database_writes=True)
        self.driver.assert_not_awaited()

    async def test_setup_rejects_created_schema_and_object_acl_access(self):
        for mode in ('schema', 'object', 'dependency'):
            with self.subTest(mode=mode):
                self.control = ConnectionDouble()
                self.driver.return_value = self.control
                if mode == 'schema':
                    original = self.control._fetchrow

                    async def bad_acl(sql, *args):
                        result = await original(sql, *args)
                        if sql == NAMESPACE_SQL and result is not None:
                            result['acl_safe'] = False
                        return result
                    self.control.fetchrow.side_effect = bad_acl
                else:
                    self.control.bad_object_acl = mode == 'object'
                    self.control.bad_dependencies = mode == 'dependency'
                with self.assertRaises(IntegrationSafetyError):
                    await self.create()
                self.assertFalse(any(c.args[0].startswith('DROP ') for c in self.control.execute.await_args_list))

    async def test_simultaneous_cleanup_joins_one_worker(self):
        db = await self.create()
        self.control.execute.reset_mock()
        await asyncio.wait_for(asyncio.gather(db.close(), db.close(), db.close()), 2)
        drops = [c.args[0] for c in self.control.execute.await_args_list if c.args[0].startswith('DROP SCHEMA')]
        self.assertEqual(len(drops), 1)
        self.control.close.assert_awaited_once()

    async def test_cancelling_cleanup_waiter_does_not_abort_shared_worker(self):
        db = await self.create()
        entered, resume = asyncio.Event(), asyncio.Event()
        original = self.control._fetchrow

        async def paused(sql, *args):
            if sql == IDENTITY_SQL:
                entered.set()
                await resume.wait()
            return await original(sql, *args)

        self.control.fetchrow.side_effect = paused
        first = asyncio.create_task(db.close())
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(db.close())
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        resume.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(first, 2)
        await asyncio.wait_for(second, 2)
        self.assertEqual(db._state, 'closed')

    async def test_cancellation_inside_checkout_always_closes_and_decrements(self):
        db = await self.create()
        con = self.control.checkout_copy()
        self.driver.return_value = con
        with self.assertRaises(asyncio.CancelledError) as raised:
            async with db.acquire():
                raise asyncio.CancelledError()
        self.assertIsNotNone(raised.exception.recovery)
        self.assertEqual(db._active, 0)
        con.close.assert_awaited_once()

    async def test_checkout_cleanup_errors_preserve_original_cancellation(self):
        db = await self.create()
        con = self.control.checkout_copy()
        con.close.side_effect = RuntimeError('synthetic close failure')
        con.terminate.side_effect = RuntimeError('synthetic termination failure')
        self.driver.return_value = con
        cancellation = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError) as raised:
            async with db.acquire():
                raise cancellation
        self.assertIs(raised.exception, cancellation)
        self.assertEqual(db._active, 0)
        self.assertEqual(db._state, 'failed')
        self.assertEqual(raised.exception.recovery.phase, 'connection_close_unconfirmed')

    async def test_unconfirmed_termination_disables_the_run(self):
        db = await self.create()
        con = self.control.checkout_copy()
        con.close.side_effect = asyncio.CancelledError()
        con.terminate.side_effect = RuntimeError('synthetic termination failure')
        self.driver.return_value = con
        with self.assertRaises(asyncio.CancelledError) as raised:
            async with db.acquire():
                pass
        self.assertEqual(db._active, 0)
        self.assertEqual(db._state, 'failed')
        self.assertEqual(raised.exception.recovery.phase, 'connection_close_unconfirmed')
        with self.assertRaises(IntegrationSafetyError):
            await db.close()

    async def test_cancelled_checkout_connect_drains_late_connection_and_disables_if_unclosed(self):
        db = await self.create()
        con = self.control.checkout_copy()
        con.terminate.side_effect = RuntimeError('synthetic termination failure')
        entered = asyncio.Event()

        async def late_connect(**kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return con  # A transport completing concurrently with cancellation.

        self.driver.side_effect = late_connect

        async def checkout():
            async with db.acquire():
                self.fail('Cancelled connection must not be yielded')

        worker = asyncio.create_task(checkout())
        await asyncio.wait_for(entered.wait(), 2)
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(worker, 2)
        con.terminate.assert_called_once()
        self.assertEqual(db._active, 0)
        self.assertEqual(db._state, 'failed')
        self.assertEqual(raised.exception.recovery.phase, 'connection_close_unconfirmed')
        self.assertTrue(raised.exception.termination_unconfirmed)

    async def test_worker_cancellation_preserves_recovery_without_destructive_retry(self):
        db = await self.create()
        entered = asyncio.Event()
        original = self.control._fetchrow

        async def paused(sql, *args):
            if sql == IDENTITY_SQL:
                entered.set()
                await asyncio.Event().wait()
            return await original(sql, *args)

        self.control.fetchrow.side_effect = paused
        closing = asyncio.create_task(db.close())
        await asyncio.wait_for(entered.wait(), 2)
        db._close_task.cancel()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(closing, 2)
        self.assertEqual(db._state, 'failed')
        self.assertIsNotNone(raised.exception.recovery)
        self.assertFalse(any(c.args[0].startswith('DROP ') for c in self.control.execute.await_args_list))

    async def test_cancellation_during_setup_retains_recovery_and_semantics(self):
        original = self.control._execute

        async def cancel_ddl(sql, *args):
            if sql.startswith('CREATE TABLE '):
                raise asyncio.CancelledError()
            return await original(sql, *args)

        self.control.execute.side_effect = cancel_ddl
        with self.assertRaises(asyncio.CancelledError) as raised:
            await self.create()
        self.assertEqual(raised.exception.recovery.phase, 'creating_tables')
        self.control.close.assert_awaited_once()
        self.assertTrue(list(Path(self.directory.name).glob('dao_it_*.json')))

    async def test_setup_cleanup_errors_preserve_original_cancellation(self):
        cancellation = asyncio.CancelledError()
        self.control.fetchrow.side_effect = cancellation
        self.control.close.side_effect = RuntimeError('synthetic close failure')
        self.control.terminate.side_effect = RuntimeError('synthetic termination failure')
        with self.assertRaises(asyncio.CancelledError) as raised:
            await self.create()
        self.assertIs(raised.exception, cancellation)
        self.assertEqual(raised.exception.recovery.phase, 'connection_close_unconfirmed')

    async def test_commit_error_records_unknown_outcome(self):
        self.control.transaction.return_value.__aexit__.side_effect = RuntimeError('synthetic commit uncertainty')
        with self.assertRaises(IntegrationSafetyError) as raised:
            await self.create()
        self.assertEqual(raised.exception.recovery.outcome, 'commit_unknown')
        data = json.loads(next(Path(self.directory.name).glob('*.json')).read_text())
        self.assertEqual(data['outcome'], 'commit_unknown')
        self.assertNotIn(self.config.password, json.dumps(data))

    async def test_failed_journal_update_before_connect_prevents_network_activity(self):
        with patch('integration_recovery.RecoveryJournal.save', side_effect=OSError('synthetic journal failure')):
            with self.assertRaises(IntegrationSafetyError) as raised:
                await self.create()
        self.driver.assert_not_awaited()
        self.assertEqual(raised.exception.recovery.outcome, 'no_writes')

    async def test_post_commit_journal_failure_retains_conservative_disk_evidence(self):
        from integration_recovery import RecoveryJournal
        original = RecoveryJournal.save

        def fail_after_commit(journal, record):
            if record.phase == 'setup_committed':
                raise OSError('synthetic disk failure after commit')
            original(journal, record)

        with patch('integration_recovery.RecoveryJournal.save', new=fail_after_commit):
            with self.assertRaises(IntegrationSafetyError) as raised:
                await self.create()
        self.assertEqual(raised.exception.recovery.outcome, 'committed')
        data = json.loads(next(Path(self.directory.name).glob('*.json')).read_text())
        self.assertEqual(data['outcome'], 'commit_unknown')
        self.assertEqual(data['schema'], raised.exception.recovery.schema)
        self.assertEqual(len(data['table_oids']), len(_table_definitions()))
        self.control.close.assert_awaited_once()

    async def test_partial_cleanup_failure_preserves_inventory_without_retrying(self):
        db = await self.create()
        original = self.control._execute
        drops = []

        async def fail_second_drop(sql, *args):
            if sql.startswith('DROP TABLE '):
                drops.append(sql)
                if len(drops) == 2:
                    raise RuntimeError('synthetic mid-cleanup failure')
            return await original(sql, *args)

        self.control.execute.side_effect = fail_second_drop
        with self.assertRaises(IntegrationSafetyError) as raised:
            await db.close()
        self.assertEqual(len(drops), 2)
        self.assertEqual(raised.exception.recovery.outcome, 'cleanup_rollback_unverified')
        self.assertEqual(len(raised.exception.recovery.table_oids), len(_table_definitions()))
        self.assertIs(self.control.transaction.return_value.__aexit__.await_args.args[0], RuntimeError)
        with self.assertRaises(IntegrationSafetyError):
            await db.close()
        self.assertEqual(len(drops), 2)

    async def test_inventory_detects_implicit_dependents_and_definition_drift(self):
        for kind in ('trigger', 'rule', 'policy', 'statistics', 'default', 'type', 'column'):
            with self.subTest(kind=kind):
                self.control = ConnectionDouble()
                self.driver.return_value = self.control
                db = await self.create()
                self.control.dependents.append(object_row(kind, 99999, 'synthetic-dependent', classid=999))
                with self.assertRaises(IntegrationSafetyError):
                    await db.close()
                self.assertFalse(any(c.args[0].startswith('DROP ') for c in self.control.execute.await_args_list))


if __name__ == '__main__':
    unittest.main()
