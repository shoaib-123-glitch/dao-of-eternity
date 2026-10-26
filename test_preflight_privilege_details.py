"""Offline privilege diagnostics: synthetic metadata, mocked driver, no SQL engine."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg

import integration_support as support
from integration_catalog import (
    CATALOG_WRITES_SQL, IDENTITY_SQL, PRIVILEGES_SQL, PRIVILEGE_FLAGS, SERVER_IDENTITY_SQL,
)
from test_integration_followup import pg_settings_access
from test_integration_safety import environment
from test_preflight_port_forwarding import ForwardedConnectionDouble


PRIVATE = 'synthetic-password postgresql://private/private SELECT secret_catalog_row'


class PrivilegeDiagnosticRedactionTests(unittest.TestCase):
    def test_only_allowlisted_names_and_exact_booleans_can_be_returned(self):
        flags = {'other_database_access': True, 'elevated': False,
                 'owned': PRIVATE, 'table_update': 1, 'update_acl_safe': None,
                 'password': PRIVATE, 'schema': PRIVATE, PRIVATE: True}
        error = support.IntegrationSafetyError(
            PRIVATE, category=support.FailureCategory.PRIVILEGES,
            privilege_stage=support.PrivilegeStage.EFFECTIVE_PRIVILEGES_POLICY,
            privilege_flags=flags)
        error.detail = error.hint = error.query = PRIVATE
        error.add_note(PRIVATE)
        flags['other_database_access'] = False
        self.assertEqual(support.privilege_diagnostic(error), {
            'stage': 'EFFECTIVE_PRIVILEGES_POLICY',
            'flags': {'elevated': False, 'other_database_access': True},
        })
        self.assertNotIn(PRIVATE, json.dumps(support.privilege_diagnostic(error)))

    def test_mutated_exception_attributes_are_revalidated_at_output(self):
        error = support.IntegrationSafetyError(
            PRIVATE, category=support.FailureCategory.PRIVILEGES,
            privilege_stage=support.PrivilegeStage.ROLE_POLICY)
        error.privilege_flags = {'elevated': True, 'can_login': PRIVATE, PRIVATE: True}
        self.assertEqual(support.privilege_diagnostic(error),
                         {'stage': 'ROLE_POLICY', 'flags': {'elevated': True}})
        for stage in (PRIVATE, 'ROLE_POLICY', None, object()):
            with self.subTest(stage_type=type(stage).__name__):
                error.privilege_stage = stage
                self.assertEqual(support.privilege_diagnostic(error),
                                 {'stage': 'UNAVAILABLE', 'flags': {}})

    def test_wrong_flag_container_types_never_become_output(self):
        error = support.IntegrationSafetyError(
            PRIVATE, category=support.FailureCategory.PRIVILEGES,
            privilege_stage=support.PrivilegeStage.SERVER_IDENTITY)
        for flags in (PRIVATE, [PRIVATE], (PRIVATE,), None, True):
            with self.subTest(flags_type=type(flags).__name__):
                error.privilege_flags = flags
                self.assertEqual(support.privilege_diagnostic(error),
                                 {'stage': 'SERVER_IDENTITY', 'flags': {}})

    def test_other_failure_categories_never_expose_privilege_details(self):
        for category in support.FailureCategory:
            if category is support.FailureCategory.PRIVILEGES:
                continue
            with self.subTest(category=category.value):
                error = support.IntegrationSafetyError(
                    PRIVATE, category=category, privilege_stage=support.PrivilegeStage.ROLE_POLICY,
                    privilege_flags={'elevated': True})
                self.assertIsNone(support.privilege_diagnostic(error))


class PrivilegeDetailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = support.TestDatabaseConfig.from_environment({
            **environment(), 'DAO_TEST_HOST': '127.0.0.1', 'DAO_TEST_PORT': '5433'})
        self.expected = support.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747)
        self.driver_patch = patch('integration_support.asyncpg.connect', new_callable=AsyncMock)
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)
        self.fresh()

    def fresh(self):
        self.con = ForwardedConnectionDouble('172.17.0.2')
        self.con.identity['version'] = 180006
        self.con.server_identity['system_identifier'] = self.expected.system_identifier
        self.driver.reset_mock(return_value=True, side_effect=True)
        self.driver.return_value = self.con
        return self.con

    async def preflight(self):
        return await support.preflight_catalog(
            self.config, allow_connection=True, expected_server=self.expected)

    def assert_readonly_and_closed(self):
        self.con.execute.assert_not_awaited()
        self.con.fetchval.assert_not_awaited()
        self.con.transaction.assert_not_called()
        self.con.close.assert_awaited_once()
        self.assertEqual(self.con.identity['read_only'], 'on')
        self.assertEqual(self.con.identity['default_read_only'], 'on')
        queries = [call.args[0] for call in self.con.fetchrow.await_args_list]
        self.assertTrue(set(queries) <= {IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL})
        self.assertTrue(all(call.args[0] == CATALOG_WRITES_SQL
                            for call in self.con.fetch.await_args_list))

    async def rejected(self, stage):
        with self.assertRaises(support.IntegrationSafetyError) as raised:
            await self.preflight()
        error = raised.exception
        self.assertIs(support.failure_category(error), support.FailureCategory.PRIVILEGES)
        diagnostic = support.privilege_diagnostic(error)
        self.assertEqual(diagnostic['stage'], stage)
        self.assertTrue(all(type(value) is bool for value in diagnostic['flags'].values()))
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))
        self.assertNotIn(str(self.expected.system_identifier), json.dumps(diagnostic))
        self.assertNotIn(self.config.password, json.dumps(diagnostic))
        self.assert_readonly_and_closed()
        return diagnostic

    async def test_every_role_policy_rejection_identifies_its_boolean(self):
        for key, value in (('can_login', False), ('elevated', True),
                           ('other_memberships', True), ('enabled_event_triggers', True)):
            with self.subTest(key=key):
                self.fresh().identity[key] = value
                result = await self.rejected('ROLE_POLICY')
                self.assertIs(result['flags'][key], value)
                self.assertEqual(self.con.fetchrow.await_count, 1)
                self.con.fetch.assert_not_awaited()

    async def test_every_effective_gate_reports_flags_without_further_queries(self):
        for key in PRIVILEGE_FLAGS:
            if key == 'system_catalog_writes':
                continue
            with self.subTest(key=key):
                self.fresh().privileges[key] = True
                result = await self.rejected('EFFECTIVE_PRIVILEGES_POLICY')
                self.assertEqual(set(result['flags']), set(PRIVILEGE_FLAGS))
                self.assertEqual([key for key, value in result['flags'].items() if value], [key])
                self.assertEqual(self.con.fetchrow.await_count, 3)
                self.con.fetch.assert_not_awaited()

    async def test_other_database_flag_is_not_assumed_false_from_successful_authentication(self):
        # Synthetic fixture represents denied CONNECT with residual TEMPORARY.
        # PostgreSQL's ANY-privilege semantics are documented, not SQL-emulated.
        self.con.privileges['other_database_access'] = True
        result = await self.rejected('EFFECTIVE_PRIVILEGES_POLICY')
        self.assertTrue(result['flags']['other_database_access'])

    async def test_nonstandard_catalog_access_reports_only_predicate_booleans(self):
        cases = (
            ({'oid': 20000}, 'builtin_catalog_oid', False),
            ({'schema': PRIVATE}, 'pg_catalog_schema', False),
            ({'name': PRIVATE}, 'pg_settings_name', False),
            ({'kind': 'r'}, 'view_kind', False),
            ({'is_pg_settings': False}, 'is_pg_settings', False),
            ({'owned': True}, 'owned', True),
            ({'other_table_access': True}, 'other_table_access', True),
            ({'other_column_access': True}, 'other_column_access', True),
            ({'table_update': False}, 'table_update', False),
            ({'update_acl_safe': False}, 'update_acl_safe', False),
            ({'explicit_column_update': True}, 'explicit_column_update', True),
        )
        for overrides, flag, expected in cases:
            with self.subTest(flag=flag):
                self.fresh().privileges['system_catalog_writes'] = True
                row = {**pg_settings_access(), **overrides}
                self.con.fetch = AsyncMock(return_value=[row])
                result = await self.rejected('CATALOG_WRITES_POLICY')
                self.assertTrue(result['flags']['single_catalog_write_candidate'])
                self.assertFalse(result['flags']['standard_pg_settings_update'])
                self.assertIs(result['flags'][flag], expected)
                self.con.fetch.assert_awaited_once_with(CATALOG_WRITES_SQL, None)

    async def test_unexpected_candidate_count_exposes_no_names_counts_or_rows(self):
        for rows in ([], [pg_settings_access(), pg_settings_access()]):
            with self.subTest(multiple=bool(rows)):
                self.fresh().privileges['system_catalog_writes'] = True
                self.con.fetch = AsyncMock(return_value=rows)
                result = await self.rejected('CATALOG_WRITES_POLICY')
                self.assertEqual(result['flags'], {
                    'single_catalog_write_candidate': False, 'standard_pg_settings_update': False})

    async def test_unknown_catalog_values_are_omitted_without_coercion(self):
        self.con.privileges['system_catalog_writes'] = True
        row = {**pg_settings_access(), 'update_acl_safe': PRIVATE, 'schema': PRIVATE}
        self.con.fetch = AsyncMock(return_value=[row])
        result = await self.rejected('CATALOG_WRITES_POLICY')
        self.assertNotIn('update_acl_safe', result['flags'])
        self.assertFalse(result['flags']['pg_catalog_schema'])

    async def test_catalog_rows_cannot_override_diagnostic_predicates(self):
        self.con.privileges['system_catalog_writes'] = True
        row = {**pg_settings_access(), 'owned': True,
               'standard_pg_settings_update': True, 'single_catalog_write_candidate': False}
        self.con.fetch = AsyncMock(return_value=[row])
        result = await self.rejected('CATALOG_WRITES_POLICY')
        self.assertFalse(result['flags']['standard_pg_settings_update'])
        self.assertTrue(result['flags']['single_catalog_write_candidate'])

    async def test_permission_denial_identifies_each_existing_query_boundary(self):
        for query, stage in (
                (IDENTITY_SQL, 'SESSION_METADATA'), (SERVER_IDENTITY_SQL, 'SERVER_IDENTITY'),
                (PRIVILEGES_SQL, 'EFFECTIVE_PRIVILEGES_QUERY'),
                (CATALOG_WRITES_SQL, 'CATALOG_WRITES_QUERY')):
            with self.subTest(stage=stage):
                self.fresh()
                if query == CATALOG_WRITES_SQL:
                    self.con.privileges['system_catalog_writes'] = True
                    self.con.fetch.side_effect = asyncpg.InsufficientPrivilegeError(PRIVATE)
                else:
                    original = self.con._fetchrow

                    async def denied(sql, *args):
                        if sql == query:
                            raise asyncpg.InsufficientPrivilegeError(PRIVATE)
                        return await original(sql, *args)

                    self.con.fetchrow.side_effect = denied
                result = await self.rejected(stage)
                self.assertEqual(result['flags'], {})
                self.driver.assert_awaited_once()

    async def test_startup_permission_denial_has_no_catalog_flags_or_driver_text(self):
        self.driver.side_effect = asyncpg.InsufficientPrivilegeError(PRIVATE)
        with self.assertRaises(support.IntegrationSafetyError) as raised:
            await self.preflight()
        self.assertEqual(support.privilege_diagnostic(raised.exception),
                         {'stage': 'CONNECTION_STARTUP', 'flags': {}})
        self.con.fetchrow.assert_not_awaited()
        self.con.close.assert_not_awaited()

    async def test_pg18_6_normal_pg_settings_exception_still_passes(self):
        self.con.privileges['system_catalog_writes'] = True
        self.con.fetch = AsyncMock(return_value=[pg_settings_access()])
        result = await self.preflight()
        self.assertEqual(result.server_major, 18)
        self.assertTrue(result.effective_privileges_verified)
        self.assertTrue(result.cluster_identity_verified)
        self.assertEqual(self.con.fetchrow.await_count, 3)
        self.con.fetch.assert_awaited_once_with(CATALOG_WRITES_SQL, None)
        self.assert_readonly_and_closed()

    async def test_missing_metadata_remains_catalog_compatibility_not_false_flags(self):
        self.con.privileges['unsafe_defaults'] = None
        with self.assertRaises(support.IntegrationSafetyError) as raised:
            await self.preflight()
        self.assertIs(support.failure_category(raised.exception),
                      support.FailureCategory.CATALOG_COMPATIBILITY)
        self.assertIsNone(support.privilege_diagnostic(raised.exception))
        self.assert_readonly_and_closed()

    async def test_unconfirmed_close_does_not_publish_stale_privilege_diagnostic(self):
        self.con.privileges['other_database_access'] = True
        self.con.close.side_effect = RuntimeError(PRIVATE)
        self.con.terminate.side_effect = RuntimeError(PRIVATE)
        with self.assertRaises(support.IntegrationSafetyError) as raised:
            await self.preflight()
        self.assertIs(support.failure_category(raised.exception),
                      support.FailureCategory.CONNECTION_CLEANUP)
        self.assertIsNone(support.privilege_diagnostic(raised.exception))
        self.assert_readonly_and_closed()


if __name__ == '__main__':
    unittest.main()
