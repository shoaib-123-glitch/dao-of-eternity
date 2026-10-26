"""Offline forwarding policy regressions. Every driver call is mocked; no SQL runs."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError, replace
import json
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg

import integration_support as support
from integration_catalog import IDENTITY_SQL, PRIVILEGES_SQL, SERVER_IDENTITY_SQL
from test_integration_safety import ConnectionDouble, environment


# Synthetic cluster identities, never sampled from a connected server.
CLUSTER_ID = 7600000000000000001
OTHER_CLUSTER_ID = 7600000000000000002
PRIVATE_TEXT = 'synthetic-private-password catalog-row DSN'


class ForwardedConnectionDouble(ConnectionDouble):
    def __init__(self, address='172.20.0.2'):
        super().__init__()
        self.identity.update(server_address=address, server_port=5432, version=180000)
        self.server_identity = {'system_identifier': CLUSTER_ID}

    async def _fetchrow(self, sql, *args):
        if sql == SERVER_IDENTITY_SQL:
            self.events.append(('read', sql))
            return deepcopy(self.server_identity)
        return await super()._fetchrow(sql, *args)


class ExpectedServerIdentityTests(unittest.TestCase):
    def test_complete_explicit_pin_is_required(self):
        expected = support.ExpectedServerIdentity('172.20.0.2', 5432, CLUSTER_ID)
        for field, values in (
                ('address', (None, '', 'remote', '0.0.0.0', '127.0.0.2', '::1',
                             '::ffff:127.0.0.1', '8.8.8.8', '172.32.0.2',
                             '172.20.0.2/32', '172.20.0.2\n', 1)),
                ('port', (None, True, 0, -1, 65536, '5432', 5432.0)),
                ('system_identifier', (None, True, 0, -1, 2**63, str(CLUSTER_ID), float(CLUSTER_ID)))):
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(support.IntegrationSafetyError) as raised:
                        replace(expected, **{field: value})
                    self.assertIs(support.failure_category(raised.exception),
                                  support.FailureCategory.CONFIGURATION)

    def test_policy_is_frozen_and_identifier_is_not_in_repr(self):
        expected = support.ExpectedServerIdentity('172.20.0.2', 5432, CLUSTER_ID)
        with self.assertRaises(FrozenInstanceError):
            expected.port = 5433
        self.assertNotIn(str(CLUSTER_ID), repr(expected))


class ForwardedPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = support.TestDatabaseConfig.from_environment({
            **environment(), 'DAO_TEST_HOST': '127.0.0.1', 'DAO_TEST_PORT': '5433',
        })
        self.expected = support.ExpectedServerIdentity('172.20.0.2', 5432, CLUSTER_ID)
        self.driver_patch = patch('integration_support.asyncpg.connect', new_callable=AsyncMock)
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)
        self.fresh_connection()

    def fresh_connection(self, address='172.20.0.2'):
        self.con = ForwardedConnectionDouble(address)
        self.driver.reset_mock(return_value=True, side_effect=True)
        self.driver.return_value = self.con
        return self.con

    async def preflight(self):
        return await support.preflight_catalog(
            self.config, allow_connection=True, expected_server=self.expected)

    def queries(self):
        return [call.args[0] for call in self.con.fetchrow.await_args_list]

    def assert_catalog_only_and_closed(self):
        self.con.execute.assert_not_awaited()
        self.con.fetchval.assert_not_awaited()
        self.con.fetch.assert_not_awaited()
        self.con.transaction.assert_not_called()
        self.con.close.assert_awaited_once()
        self.assertTrue(set(self.queries()) <= {IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL})
        self.assertTrue(all(sql.lstrip().startswith(('SELECT ', 'WITH ')) for sql in self.queries()))

    async def rejected(self, category=support.FailureCategory.IDENTITY):
        with self.assertRaises(support.IntegrationSafetyError) as raised:
            await self.preflight()
        self.assertIs(support.failure_category(raised.exception), category)
        self.assertNotIn(PRIVATE_TEXT, str(raised.exception))
        self.assertNotIn(str(CLUSTER_ID), str(raised.exception))
        self.assert_catalog_only_and_closed()
        return raised.exception

    async def test_5433_to_5432_forwarding_requires_exact_internal_endpoint_and_cluster(self):
        for address in ('127.0.0.1', '172.20.0.2', '10.88.0.2', '192.168.65.2'):
            with self.subTest(address=address):
                self.fresh_connection(address)
                self.expected = replace(self.expected, address=address)
                report = await self.preflight()
                self.assertEqual((report.host, report.port, report.server_port),
                                 ('127.0.0.1', 5433, 5432))
                self.assertTrue(report.cluster_identity_verified)
                self.assertEqual(report.database, 'dao_eternity_test')
                self.assertEqual(report.role, 'dao_test_user')
                self.assertEqual(report.server_major, 18)
                kwargs = self.driver.await_args.kwargs
                self.assertEqual((kwargs['host'], kwargs['port'], kwargs['database'], kwargs['user']),
                                 ('127.0.0.1', 5433, 'dao_eternity_test', 'dao_test_user'))
                self.assertFalse(kwargs['ssl'])
                self.assertEqual(kwargs['server_settings']['default_transaction_read_only'], 'on')
                self.assertEqual(kwargs['server_settings']['search_path'], 'pg_catalog')
                self.assertEqual(self.queries(), [IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL])
                output = json.dumps(asdict(report))
                self.assertNotIn(str(CLUSTER_ID), output)
                self.assertNotIn(self.config.password, output)
                self.assert_catalog_only_and_closed()

    async def test_default_policy_still_rejects_port_forwarding_without_cluster_pin(self):
        self.fresh_connection('127.0.0.1')
        with self.assertRaises(support.IntegrationSafetyError) as raised:
            await support.preflight_catalog(self.config, allow_connection=True)
        self.assertIs(support.failure_category(raised.exception), support.FailureCategory.IDENTITY)
        self.assertEqual(self.driver.await_args.kwargs['port'], 5433)
        self.assertEqual(self.queries(), [IDENTITY_SQL])
        self.assert_catalog_only_and_closed()

    async def test_default_direct_connection_retains_policy_without_extra_query(self):
        self.config = replace(self.config, port=5432)
        self.fresh_connection('127.0.0.1')
        report = await support.preflight_catalog(self.config, allow_connection=True)
        self.assertEqual((report.port, report.server_port), (5432, 5432))
        self.assertFalse(report.cluster_identity_verified)
        self.assertEqual(self.queries(), [IDENTITY_SQL, PRIVILEGES_SQL])
        self.assert_catalog_only_and_closed()

    async def test_same_endpoint_and_names_on_unexpected_cluster_are_rejected(self):
        self.con.server_identity['system_identifier'] = OTHER_CLUSTER_ID
        await self.rejected()
        self.assertEqual(self.queries(), [IDENTITY_SQL, SERVER_IDENTITY_SQL])

    async def test_equal_client_and_server_ports_do_not_bypass_explicit_cluster_pin(self):
        self.config = replace(self.config, port=5432)
        self.expected = replace(self.expected, address='127.0.0.1')
        self.fresh_connection('127.0.0.1').server_identity['system_identifier'] = OTHER_CLUSTER_ID
        await self.rejected()
        self.assertEqual(self.queries(), [IDENTITY_SQL, SERVER_IDENTITY_SQL])

    async def test_server_address_must_match_pin_even_with_matching_cluster(self):
        for value in (None, '127.0.0.1', '172.20.0.3', '172.20.0.2/32', '::1', '8.8.8.8'):
            with self.subTest(value=value):
                self.fresh_connection().identity['server_address'] = value
                await self.rejected()
                self.assertEqual(self.queries(), [IDENTITY_SQL])

    async def test_server_port_must_match_internal_pin_not_client_port(self):
        for value in (None, True, 0, 5433, '5432', 5432.0):
            with self.subTest(value_type=type(value).__name__):
                self.fresh_connection().identity['server_port'] = value
                await self.rejected()
                self.assertEqual(self.queries(), [IDENTITY_SQL])

    async def test_database_and_both_roles_stay_strict(self):
        for field in ('database_name', 'role_name', 'session_role'):
            with self.subTest(field=field):
                self.fresh_connection().identity[field] = 'unexpected_identity'
                await self.rejected()
                self.assertEqual(self.queries(), [IDENTITY_SQL])

    async def test_missing_malformed_or_wrong_cluster_metadata_fails_closed(self):
        for value in (None, {}, {'unexpected': CLUSTER_ID},
                      *({'system_identifier': value} for value in (
                          None, True, str(CLUSTER_ID), float(CLUSTER_ID), OTHER_CLUSTER_ID))):
            with self.subTest(value_type=type(value).__name__):
                self.fresh_connection().server_identity = value
                await self.rejected()
                self.assertEqual(self.queries(), [IDENTITY_SQL, SERVER_IDENTITY_SQL])

    async def test_pin_cannot_be_used_to_disable_connection_opt_in(self):
        with self.assertRaises(support.IntegrationSafetyError):
            await support.preflight_catalog(self.config, expected_server=self.expected)
        self.driver.assert_not_called()

    async def test_invalid_policy_is_rejected_before_driver_is_called(self):
        class UnapprovedSubclass(support.ExpectedServerIdentity):
            pass
        invalid = [False, {}, object(), UnapprovedSubclass('172.20.0.2', 5432, CLUSTER_ID)]
        mutated = replace(self.expected)
        object.__setattr__(mutated, 'system_identifier', None)
        invalid.append(mutated)
        for policy in invalid:
            with self.subTest(policy_type=type(policy).__name__):
                with self.assertRaises(support.IntegrationSafetyError) as raised:
                    await support.preflight_catalog(self.config, allow_connection=True,
                                                    expected_server=policy)
                self.assertIs(support.failure_category(raised.exception),
                              support.FailureCategory.CONFIGURATION)
        self.driver.assert_not_called()

    async def test_invalid_client_destination_is_rejected_even_with_complete_server_pin(self):
        for field, value in (('host', '172.20.0.2'), ('port', True),
                             ('database', 'production'), ('user', 'postgres')):
            with self.subTest(field=field):
                mutated = replace(self.config)
                object.__setattr__(mutated, field, value)
                with self.assertRaises(support.IntegrationSafetyError) as raised:
                    await support.preflight_catalog(mutated, allow_connection=True,
                                                    expected_server=self.expected)
                self.assertIs(support.failure_category(raised.exception),
                              support.FailureCategory.CONFIGURATION)
        self.driver.assert_not_called()

    async def test_readonly_recovery_and_privilege_gates_remain_required(self):
        for field, value, category in (
                ('read_only', 'off', support.FailureCategory.READ_ONLY),
                ('default_read_only', 'off', support.FailureCategory.READ_ONLY),
                ('in_recovery', True, support.FailureCategory.IDENTITY),
                ('temp_schema', 100, support.FailureCategory.IDENTITY),
                ('elevated', True, support.FailureCategory.PRIVILEGES),
                ('other_memberships', True, support.FailureCategory.PRIVILEGES)):
            with self.subTest(field=field):
                self.fresh_connection().identity[field] = value
                await self.rejected(category)
                self.assertEqual(self.queries(), [IDENTITY_SQL])
        for flag in ('other_database_access', 'outside_function_access', 'unsafe_parameter_access'):
            with self.subTest(privilege_flag=flag):
                self.fresh_connection().privileges[flag] = True
                await self.rejected(support.FailureCategory.PRIVILEGES)

    async def test_cluster_query_permission_and_compatibility_errors_have_no_fallback(self):
        for error, category in (
                (asyncpg.InsufficientPrivilegeError, support.FailureCategory.PRIVILEGES),
                (asyncpg.UndefinedFunctionError, support.FailureCategory.CATALOG_COMPATIBILITY),
                (ConnectionResetError, support.FailureCategory.CONNECTION)):
            with self.subTest(error_type=error.__name__):
                self.fresh_connection()
                original = self.con._fetchrow

                async def fail_cluster(sql, *args):
                    if sql == SERVER_IDENTITY_SQL:
                        raise error(PRIVATE_TEXT)
                    return await original(sql, *args)

                self.con.fetchrow.side_effect = fail_cluster
                await self.rejected(category)
                self.assertEqual(self.queries(), [IDENTITY_SQL, SERVER_IDENTITY_SQL])

    async def test_cancellation_during_cluster_read_still_closes(self):
        original = self.con._fetchrow

        async def cancel_cluster(sql, *args):
            if sql == SERVER_IDENTITY_SQL:
                raise asyncio.CancelledError()
            return await original(sql, *args)

        self.con.fetchrow.side_effect = cancel_cluster
        with self.assertRaises(asyncio.CancelledError):
            await self.preflight()
        self.assert_catalog_only_and_closed()

    async def test_failed_disposal_overrides_cluster_rejection(self):
        self.con.server_identity['system_identifier'] = OTHER_CLUSTER_ID
        self.con.close.side_effect = RuntimeError(PRIVATE_TEXT)
        self.con.terminate.side_effect = RuntimeError(PRIVATE_TEXT)
        error = await self.rejected(support.FailureCategory.CONNECTION_CLEANUP)
        self.assertTrue(error.termination_unconfirmed)


if __name__ == '__main__':
    unittest.main()
