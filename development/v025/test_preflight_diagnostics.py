"""Diagnostic regressions using synthetic metadata and mocked driver calls only."""
import asyncio
import ipaddress
import unittest
from unittest.mock import AsyncMock, Mock, patch

import asyncpg

from integration_catalog import CATALOG_WRITES_SQL, IDENTITY_SQL, PRIVILEGES_SQL, PRIVILEGE_FLAGS
from integration_support import (
    FailureCategory as Category, IntegrationSafetyError, TestDatabaseConfig,
    failure_category, preflight_catalog,
)
from test_integration_safety import CONFIG_KEYS, ConnectionDouble, environment


PRIVATE_TEXT = 'synthetic-password postgresql://private/private SELECT private_catalog_row'


class PostgreSQL18IdentityFixture(ConnectionDouble):
    """Documented inet display fixtures for two reviewed projections, not SQL execution.

    PostgreSQL 18's explicit inet::text includes the netmask, even /32 or /128;
    host(inet) returns only the address. Recognize only those two fixed query
    forms so an unrealistic bare-address mock cannot hide the original bug.
    Other catalog values still come from the ordinary synthetic recorder.
    """
    def __init__(self, address='127.0.0.1'):
        super().__init__()
        self.address = address
        self.identity['version'] = 180000

    async def _fetchrow(self, sql, *args):
        row = await super()._fetchrow(sql, *args)
        if sql == IDENTITY_SQL:
            value = None if self.address is None else ipaddress.ip_interface(self.address)
            compact = ' '.join(sql.split())
            if 'pg_catalog.host(pg_catalog.inet_server_addr()) AS server_address,' in compact:
                row['server_address'] = None if value is None else value.ip.compressed
            elif 'pg_catalog.inet_server_addr()::text AS server_address,' in compact:
                row['server_address'] = None if value is None else value.with_prefixlen
            else:
                raise AssertionError('Unreviewed identity address projection in offline fixture.')
        return row


class DiagnosticCodeTests(unittest.TestCase):
    def test_all_requested_categories_are_available(self):
        self.assertTrue({
            'CONFIGURATION', 'AUTHENTICATION', 'CONNECTION', 'IDENTITY',
            'PRIVILEGES', 'CATALOG_COMPATIBILITY', 'VERSION', 'CONNECTION_CLEANUP',
        } <= {category.value for category in Category})

    def test_unreviewed_category_values_cannot_be_printed(self):
        for value in (PRIVATE_TEXT, 'CONNECTION', None, True, object()):
            with self.subTest(value_type=type(value).__name__):
                error = IntegrationSafetyError(PRIVATE_TEXT, category=value)
                self.assertIs(failure_category(error), Category.SAFETY)
                error.category = value
                self.assertIs(failure_category(error), Category.SAFETY)

    def test_uncategorized_errors_use_only_fixed_fallback(self):
        self.assertIs(failure_category(RuntimeError(PRIVATE_TEXT)), Category.SAFETY)
        self.assertIs(failure_category(IntegrationSafetyError(PRIVATE_TEXT)), Category.SAFETY)

    def test_every_invalid_configuration_path_has_configuration_category(self):
        cases = []
        for key in CONFIG_KEYS:
            values = environment()
            del values[key]
            cases.append(values)
        for key, value in (
                ('DAO_TEST_HOST', 'remote'), ('DAO_TEST_PORT', 'not-a-port'),
                ('DAO_TEST_PORT', '0'), ('DAO_TEST_DATABASE', 'production'),
                ('DAO_TEST_USER', 'postgres'), ('DAO_TEST_PASSWORD', '')):
            cases.append({**environment(), key: value})
        for index, values in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(IntegrationSafetyError) as raised:
                    TestDatabaseConfig.from_environment(values)
                self.assertIs(failure_category(raised.exception), Category.CONFIGURATION)


class PreflightDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = TestDatabaseConfig.from_environment(environment())
        self.driver_patch = patch('integration_support.asyncpg.connect', new_callable=AsyncMock)
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)
        self.fresh_connection()

    def fresh_connection(self):
        self.con = ConnectionDouble()
        self.con.identity['version'] = 180000
        self.driver.reset_mock(return_value=True, side_effect=True)
        self.driver.return_value = self.con
        return self.con

    async def rejected(self, category, *, connected=True):
        with self.assertRaises(IntegrationSafetyError) as raised:
            await preflight_catalog(self.config, allow_connection=True)
        error = raised.exception
        self.assertIs(failure_category(error), category)
        self.assertNotIn(PRIVATE_TEXT, str(error))
        self.assertNotIn(self.config.password, str(error))
        self.con.execute.assert_not_awaited()
        self.con.fetchval.assert_not_awaited()
        if connected:
            self.con.close.assert_awaited_once()
        else:
            self.con.close.assert_not_awaited()
            self.con.fetchrow.assert_not_awaited()
        return error

    def fail_catalog_query(self, query, error):
        original_fetchrow = self.con._fetchrow
        if query == CATALOG_WRITES_SQL:
            self.con.privileges['system_catalog_writes'] = True
            self.con.fetch.side_effect = error
        else:
            async def fetchrow(sql, *args):
                if sql == query:
                    raise error
                return await original_fetchrow(sql, *args)
            self.con.fetchrow.side_effect = fetchrow

    async def test_disabled_or_unvalidated_configuration_never_connects(self):
        for config, kwargs in ((self.config, {}), ({}, {'allow_connection': True})):
            with self.subTest(valid_config=type(config) is TestDatabaseConfig):
                with self.assertRaises(IntegrationSafetyError) as raised:
                    await preflight_catalog(config, **kwargs)
                self.assertIs(failure_category(raised.exception), Category.CONFIGURATION)
        self.driver.assert_not_called()

    async def test_authentication_errors_are_classified_without_driver_text(self):
        for cls in (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError):
            with self.subTest(error_type=cls.__name__):
                self.fresh_connection()
                self.driver.side_effect = cls(PRIVATE_TEXT)
                error = await self.rejected(Category.AUTHENTICATION, connected=False)
                self.assertTrue(error.__suppress_context__)

    async def test_transport_and_session_startup_errors_have_connection_category(self):
        for cls in (ConnectionRefusedError, TimeoutError, OSError, asyncpg.PostgresConnectionError,
                    asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError,
                    asyncpg.InterfaceError):
            with self.subTest(error_type=cls.__name__):
                self.fresh_connection()
                self.driver.side_effect = cls(PRIVATE_TEXT)
                await self.rejected(Category.CONNECTION, connected=False)

    async def test_error_message_is_never_used_to_infer_authentication(self):
        self.driver.side_effect = RuntimeError('authentication failed ' + PRIVATE_TEXT)
        await self.rejected(Category.CONNECTION, connected=False)

    async def test_synchronous_driver_initialization_failure_is_sanitized(self):
        for cls, category in ((OSError, Category.CONNECTION),
                              (asyncpg.InvalidPasswordError, Category.AUTHENTICATION)):
            with self.subTest(error_type=cls.__name__):
                self.fresh_connection()
                driver = Mock(side_effect=cls(PRIVATE_TEXT))
                with patch('integration_support.asyncpg.connect', driver):
                    await self.rejected(category, connected=False)
                driver.assert_called_once()

    async def test_missing_database_startup_error_is_configuration(self):
        self.driver.side_effect = asyncpg.InvalidCatalogNameError(PRIVATE_TEXT)
        await self.rejected(Category.CONFIGURATION, connected=False)

    async def test_startup_permission_denial_is_privileges(self):
        self.driver.side_effect = asyncpg.InsufficientPrivilegeError(PRIVATE_TEXT)
        await self.rejected(Category.PRIVILEGES, connected=False)

    async def test_identity_rejections_are_separate_from_permissions(self):
        for key, value in (
                ('database_name', 'production'), ('role_name', 'postgres'),
                ('session_role', 'postgres'), ('server_address', 'remote'),
                ('server_port', 1234), ('in_recovery', True), ('temp_schema', 100)):
            with self.subTest(field=key):
                self.fresh_connection().identity[key] = value
                await self.rejected(Category.IDENTITY)
                self.assertEqual(self.con.fetchrow.await_count, 1)

    async def test_missing_identity_row_is_identity_rejection(self):
        self.con.fetchrow.side_effect = None
        self.con.fetchrow.return_value = None
        await self.rejected(Category.IDENTITY)

    async def test_postgresql18_address_projection_accepts_pinned_ipv4_session(self):
        self.con = PostgreSQL18IdentityFixture()
        self.driver.return_value = self.con
        report = await preflight_catalog(self.config, allow_connection=True)
        self.assertEqual(report.host, '127.0.0.1')
        self.assertEqual(report.server_major, 18)
        self.assertTrue(report.effective_privileges_verified)
        self.assertEqual([call.args[0] for call in self.con.fetchrow.await_args_list],
                         [IDENTITY_SQL, PRIVILEGES_SQL])
        self.con.execute.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_postgresql18_projection_does_not_admit_other_endpoints(self):
        for address in (None, '127.0.0.2', '192.0.2.10', '::1', '::ffff:127.0.0.1'):
            with self.subTest(address=address):
                self.con = PostgreSQL18IdentityFixture(address)
                self.driver.return_value = self.con
                await self.rejected(Category.IDENTITY)
                self.assertEqual(self.con.fetchrow.await_count, 1)

    async def test_legacy_masked_text_and_uncast_inet_values_still_fail_closed(self):
        for value in ('127.0.0.1/32', '127.0.0.1/24', '127.0.0.1\n',
                      ipaddress.ip_address('127.0.0.1'),
                      ipaddress.ip_interface('127.0.0.1/32')):
            with self.subTest(value_type=type(value).__name__):
                self.fresh_connection().identity['server_address'] = value
                await self.rejected(Category.IDENTITY)

    async def test_postgresql18_listener_port_must_match_explicit_configuration(self):
        values = {**environment(), 'DAO_TEST_PORT': '55432'}
        self.config = TestDatabaseConfig.from_environment(values)
        self.con = PostgreSQL18IdentityFixture()
        self.con.identity['server_port'] = 55432
        self.driver.return_value = self.con
        report = await preflight_catalog(self.config, allow_connection=True)
        self.assertEqual(report.port, 55432)
        self.assertEqual(self.driver.await_args.kwargs['port'], 55432)
        self.con.close.assert_awaited_once()
        self.con.execute.assert_not_awaited()
        for value in (None, '55432', 5432):
            with self.subTest(value_type=type(value).__name__):
                self.con = PostgreSQL18IdentityFixture()
                self.con.identity['server_port'] = value
                self.driver.return_value = self.con
                await self.rejected(Category.IDENTITY)

    async def test_postgresql18_session_and_effective_roles_are_both_required(self):
        for key in ('session_role', 'role_name'):
            with self.subTest(field=key):
                self.con = PostgreSQL18IdentityFixture()
                self.con.identity[key] = 'unexpected_role'
                self.driver.return_value = self.con
                await self.rejected(Category.IDENTITY)
                self.assertEqual(self.con.fetchrow.await_count, 1)

    async def test_postgresql18_membership_failure_is_privileges_after_valid_identity(self):
        self.con = PostgreSQL18IdentityFixture()
        self.con.identity['other_memberships'] = True
        self.driver.return_value = self.con
        await self.rejected(Category.PRIVILEGES)
        self.assertEqual(self.con.fetchrow.await_count, 1)

    async def test_postgresql18_recovery_or_temp_namespace_still_rejects_identity(self):
        for key, value in (('in_recovery', True), ('in_recovery', None),
                           ('temp_schema', 12000), ('temp_schema', None), ('temp_schema', '0')):
            with self.subTest(field=key, value_type=type(value).__name__):
                self.con = PostgreSQL18IdentityFixture()
                self.con.identity[key] = value
                self.driver.return_value = self.con
                await self.rejected(Category.IDENTITY)
                self.assertEqual(self.con.fetchrow.await_count, 1)

    async def test_both_readonly_gates_fail_closed(self):
        for key in ('read_only', 'default_read_only'):
            with self.subTest(field=key):
                self.fresh_connection().identity[key] = 'off'
                await self.rejected(Category.READ_ONLY)
                self.assertEqual(self.con.fetchrow.await_count, 1)

    async def test_unapproved_or_malformed_server_version_is_version_rejection(self):
        for version in (140000, 190000, None, True, '180000', 180000.0):
            with self.subTest(version_type=type(version).__name__):
                self.fresh_connection().identity['version'] = version
                await self.rejected(Category.VERSION)

    async def test_missing_identity_column_is_catalog_compatibility(self):
        for key in ('version', 'role_oid', 'can_create_schema', 'elevated'):
            with self.subTest(field=key):
                del self.fresh_connection().identity[key]
                await self.rejected(Category.CATALOG_COMPATIBILITY)

    async def test_unknown_role_metadata_is_catalog_compatibility(self):
        for key in ('can_login', 'elevated', 'other_memberships', 'enabled_event_triggers',
                    'can_create_schema', 'role_oid'):
            with self.subTest(field=key):
                self.fresh_connection().identity[key] = None
                await self.rejected(Category.CATALOG_COMPATIBILITY)

    async def test_elevated_roles_memberships_and_triggers_are_privileges(self):
        for key, value in (('can_login', False), ('elevated', True),
                           ('other_memberships', True), ('enabled_event_triggers', True)):
            with self.subTest(field=key):
                self.fresh_connection().identity[key] = value
                await self.rejected(Category.PRIVILEGES)

    async def test_every_effective_privilege_gate_keeps_privileges_category(self):
        for key in PRIVILEGE_FLAGS:
            if key == 'system_catalog_writes':
                continue
            with self.subTest(field=key):
                self.fresh_connection().privileges[key] = True
                await self.rejected(Category.PRIVILEGES)

    async def test_missing_or_unknown_privilege_results_are_catalog_compatibility(self):
        for key in PRIVILEGE_FLAGS:
            for value in (None, 0, 'false'):
                with self.subTest(field=key, value_type=type(value).__name__):
                    self.fresh_connection().privileges[key] = value
                    await self.rejected(Category.CATALOG_COMPATIBILITY)
            del self.fresh_connection().privileges[key]
            await self.rejected(Category.CATALOG_COMPATIBILITY)

    async def test_empty_privilege_result_is_catalog_compatibility(self):
        self.con.privileges = None
        await self.rejected(Category.CATALOG_COMPATIBILITY)

    async def test_permission_denial_in_each_catalog_query_is_privileges(self):
        for query in (IDENTITY_SQL, PRIVILEGES_SQL, CATALOG_WRITES_SQL):
            with self.subTest(query_stage=(IDENTITY_SQL, PRIVILEGES_SQL, CATALOG_WRITES_SQL).index(query)):
                self.fresh_connection()
                self.fail_catalog_query(query, asyncpg.InsufficientPrivilegeError(PRIVATE_TEXT))
                error = await self.rejected(Category.PRIVILEGES)
                self.assertTrue(error.__suppress_context__)

    async def test_unsupported_catalog_queries_are_catalog_compatibility(self):
        for cls in (asyncpg.UndefinedColumnError, asyncpg.UndefinedTableError,
                    asyncpg.UndefinedFunctionError, asyncpg.InvalidSchemaNameError,
                    asyncpg.PostgresSyntaxError, asyncpg.FeatureNotSupportedError, RuntimeError):
            for index, query in enumerate((IDENTITY_SQL, PRIVILEGES_SQL, CATALOG_WRITES_SQL)):
                with self.subTest(error_type=cls.__name__, query_stage=index):
                    self.fresh_connection()
                    self.fail_catalog_query(query, cls(PRIVATE_TEXT))
                    await self.rejected(Category.CATALOG_COMPATIBILITY)

    async def test_transport_loss_or_timeout_during_catalog_reads_is_connection(self):
        for cls in (ConnectionResetError, TimeoutError, asyncpg.PostgresConnectionError,
                    asyncpg.InterfaceError, asyncpg.QueryCanceledError):
            for index, query in enumerate((IDENTITY_SQL, PRIVILEGES_SQL, CATALOG_WRITES_SQL)):
                with self.subTest(error_type=cls.__name__, query_stage=index):
                    self.fresh_connection()
                    self.fail_catalog_query(query, cls(PRIVATE_TEXT))
                    await self.rejected(Category.CONNECTION)

    async def test_unapproved_catalog_write_exception_is_still_privileges(self):
        self.con.privileges['system_catalog_writes'] = True
        self.con.fetch.side_effect = None
        self.con.fetch.return_value = []
        await self.rejected(Category.PRIVILEGES)

    async def test_unconfirmed_close_and_termination_have_cleanup_category(self):
        self.con.close.side_effect = RuntimeError(PRIVATE_TEXT)
        self.con.terminate.side_effect = RuntimeError(PRIVATE_TEXT)
        error = await self.rejected(Category.CONNECTION_CLEANUP)
        self.assertIs(error.termination_unconfirmed, True)
        self.con.terminate.assert_called_once()

    async def test_cleanup_failure_overrides_prior_identity_rejection(self):
        self.con.identity['database_name'] = 'production'
        self.con.close.side_effect = RuntimeError(PRIVATE_TEXT)
        self.con.terminate.side_effect = RuntimeError(PRIVATE_TEXT)
        await self.rejected(Category.CONNECTION_CLEANUP)

    async def test_confirmed_termination_fallback_does_not_mask_prior_rejection(self):
        self.con.identity['database_name'] = 'production'
        self.con.close.side_effect = RuntimeError(PRIVATE_TEXT)
        await self.rejected(Category.IDENTITY)
        self.con.terminate.assert_called_once()

    async def test_cleanup_failure_preserves_original_cancellation_and_safe_note(self):
        cancellation = asyncio.CancelledError(PRIVATE_TEXT)
        self.con.fetchrow.side_effect = cancellation
        self.con.close.side_effect = RuntimeError(PRIVATE_TEXT)
        self.con.terminate.side_effect = RuntimeError(PRIVATE_TEXT)
        with self.assertRaises(asyncio.CancelledError) as raised:
            await preflight_catalog(self.config, allow_connection=True)
        self.assertIs(raised.exception, cancellation)
        self.assertIs(raised.exception.termination_unconfirmed, True)
        self.assertTrue(raised.exception.__notes__)
        self.assertNotIn(PRIVATE_TEXT, ' '.join(raised.exception.__notes__))
        self.con.close.assert_awaited_once()
        self.con.execute.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
