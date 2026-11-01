"""Actual asyncpg row semantics; synthetic evidence and no database connections."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg
from asyncpg.protocol.protocol import _create_record

import integration_inspector as inspector
from integration_catalog import IDENTITY_SQL, NAMESPACE_SQL, RECOVERY_OBSERVATION_SQL
from integration_inspection_diagnostics import InspectionFailure, inspection_diagnostic
from test_integration_inspector import InspectorConnectionDouble, synthetic_evidence, SCHEMA, CONTAINER_ID, PRIVATE
from test_postgres_suite_safety import config


def record(fields):
    # asyncpg's own test-only factory: construct real Records without a server.
    return _create_record({key: index for index, key in enumerate(fields)}, tuple(fields.values()))


class InspectorRecordShapeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dao_record_shapes_offline_')
        self.addCleanup(self.directory.cleanup)
        self.path = synthetic_evidence(self.directory.name)
        self.con = InspectorConnectionDouble()
        self.overrides = {}
        self.record_queries = None  # None means every catalog row is a Record.
        original = self.con._fetchrow

        async def fetchrow(sql, *args):
            row = self.overrides[sql] if sql in self.overrides else await original(sql, *args)
            if isinstance(row, dict) and (self.record_queries is None or sql in self.record_queries):
                return record(row)
            return row

        self.con.fetchrow.side_effect = fetchrow
        self.driver_patch = patch('integration_support.asyncpg.connect', AsyncMock(return_value=self.con))
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)

    async def inspect(self):
        return await inspector.inspect_recovery(config(), self.path, 16390, 16389,
            CONTAINER_ID, allow_read_only_connection=True)

    async def rejection(self, category, stage, checks):
        with self.assertRaises(InspectionFailure) as raised:
            await self.inspect()
        diagnostic = inspection_diagnostic(raised.exception)
        self.assertEqual((diagnostic['category'], diagnostic['stage']), (category, stage))
        self.assertEqual(diagnostic['failed_checks'], checks)
        self.assertEqual(diagnostic['transaction_exit'], 'ROLLBACK_ACKNOWLEDGED')
        self.assertEqual(diagnostic['connection_cleanup'], 'ACKNOWLEDGED')
        self.assertEqual(diagnostic['secondary_failures'], [])
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))
        self.assertNotIn(PRIVATE, str(raised.exception))
        self.con.execute.assert_not_awaited()

    def test_actual_record_iteration_is_values_while_keys_are_field_names(self):
        row = record(dict(oid=16390, owner=16389, acl_safe=True))
        self.assertIsInstance(row, asyncpg.Record)
        self.assertEqual(tuple(row), (16390, 16389, True))
        self.assertNotEqual(set(row), {'oid', 'owner', 'acl_safe'})
        self.assertEqual(set(row.keys()), {'oid', 'owner', 'acl_safe'})
        self.assertEqual(tuple(type(row[key]) for key in row.keys()), (int, int, bool))

    async def test_all_catalog_records_accept_legacy_journal_without_writes(self):
        before = {p: p.read_bytes() for p in Path(self.directory.name).iterdir()}
        result = await self.inspect()
        self.assertEqual(result['status'], 'EMPTY_SCHEMA_OBSERVED')
        self.assertEqual((result['journal_phase'], result['journal_outcome']),
                         ('creating_tables', 'rollback_unverified'))
        self.assertEqual(result['rollback_verification'], 'UNKNOWN')
        self.assertEqual(result['connection_cleanup'], 'ACKNOWLEDGED')
        self.assertTrue(result['read_only'] and result['cluster_identity_verified'])
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.directory.name).iterdir()})
        self.con.execute.assert_not_awaited()
        self.driver.assert_awaited_once()
        self.assertEqual((self.driver.await_args.kwargs['host'], self.driver.await_args.kwargs['port']),
                         ('127.0.0.1', 5433))
        self.assertEqual(self.con.transaction.call_args.kwargs,
                         dict(readonly=True, isolation='repeatable_read'))
        self.con.transaction.return_value.__aexit__.assert_awaited_once_with(None, None, None)
        self.con.close.assert_awaited_once()

    async def test_namespace_record_with_dictionary_observations_is_accepted(self):
        self.record_queries = {NAMESPACE_SQL}
        self.assertEqual((await self.inspect())['status'], 'EMPTY_SCHEMA_OBSERVED')

    async def test_recovery_record_with_dictionary_namespace_is_accepted(self):
        self.record_queries = {RECOVERY_OBSERVATION_SQL}
        self.assertEqual((await self.inspect())['status'], 'EMPTY_SCHEMA_OBSERVED')

    async def test_field_order_does_not_change_named_identity_checks(self):
        self.con.namespace = dict(reversed(tuple(self.con.namespace.items())))
        self.con.observation = dict(reversed(tuple(self.con.observation.items())))
        self.assertEqual((await self.inspect())['status'], 'EMPTY_SCHEMA_OBSERVED')

    async def test_schema_missing_or_extra_field_remains_catalog_rejection(self):
        for field in ('oid', 'owner', 'acl_safe', PRIVATE):
            fields = dict(oid=16390, owner=16389, acl_safe=True)
            if field == PRIVATE:
                fields[field] = PRIVATE
            else:
                fields.pop(field)
            self.overrides[NAMESPACE_SQL] = fields
            with self.subTest(field=field):
                await self.rejection('CATALOG_COMPATIBILITY', 'SCHEMA_VERIFICATION', ['metadata_shape_valid'])

    async def test_schema_null_or_wrong_scalar_types_remain_rejected(self):
        for field, values in (('oid', (None, '16390', True, 16390.0)),
                ('owner', (None, '16389', True, 16389.0)), ('acl_safe', (None, 'true', 1))):
            for value in values:
                self.overrides[NAMESPACE_SQL] = {**self.con.namespace, field: value}
                with self.subTest(field=field, value=value):
                    await self.rejection('CATALOG_COMPATIBILITY', 'SCHEMA_VERIFICATION', ['metadata_shape_valid'])

    async def test_wrong_oid_owner_or_acl_is_policy_failure_after_shape_validation(self):
        for field, value, check in (('oid', 16391, 'namespace_oid_verified'),
                ('owner', 16390, 'namespace_owner_verified'), ('acl_safe', False, 'namespace_acl_private')):
            self.overrides[NAMESPACE_SQL] = {**self.con.namespace, field: value}
            with self.subTest(field=field):
                await self.rejection('SCHEMA_VERIFICATION', 'SCHEMA_VERIFICATION', [check])

    async def test_absent_namespace_is_not_a_shape_failure(self):
        self.overrides[NAMESPACE_SQL] = None
        await self.rejection('SCHEMA_VERIFICATION', 'SCHEMA_VERIFICATION', ['namespace_present'])

    async def test_recovery_missing_or_extra_fields_remain_rejected(self):
        for field in (*sorted(inspector._OBSERVATION_FLAGS), PRIVATE):
            fields = dict(self.con.observation)
            if field == PRIVATE:
                fields[field] = PRIVATE
            else:
                fields.pop(field)
            self.overrides[RECOVERY_OBSERVATION_SQL] = fields
            with self.subTest(field=field):
                await self.rejection('CATALOG_COMPATIBILITY', 'RECOVERY_METADATA', ['metadata_shape_valid'])

    async def test_recovery_unknown_boolean_types_remain_rejected(self):
        for field in sorted(inspector._OBSERVATION_FLAGS):
            for value in (None, 1, 'true'):
                self.overrides[RECOVERY_OBSERVATION_SQL] = {**self.con.observation, field: value}
                stage = 'SESSION_INSPECTION' if field == 'no_other_guard_sessions' else 'RECOVERY_METADATA'
                with self.subTest(field=field, value=value):
                    await self.rejection('CATALOG_COMPATIBILITY', stage, [field])

    async def test_recovery_identity_acl_and_access_policy_remain_effective(self):
        for field, category, stage in (
                ('database_oid_verified', 'IDENTITY', 'DATABASE_IDENTITY'),
                ('namespace_exists', 'SCHEMA_VERIFICATION', 'RECOVERY_SCHEMA_POLICY'),
                ('namespace_identity_verified', 'SCHEMA_VERIFICATION', 'RECOVERY_SCHEMA_POLICY'),
                ('acl_private', 'SCHEMA_VERIFICATION', 'RECOVERY_SCHEMA_POLICY'),
                ('schema_usage', 'PRIVILEGES', 'SCHEMA_ACCESS_POLICY'),
                ('schema_create', 'PRIVILEGES', 'SCHEMA_ACCESS_POLICY')):
            self.overrides[RECOVERY_OBSERVATION_SQL] = {**self.con.observation, field: False}
            with self.subTest(field=field):
                await self.rejection(category, stage, [field])

    async def test_objects_dependencies_extensions_and_sessions_prevent_empty_status(self):
        for field in ('no_relations', 'no_namespace_dependencies', 'no_extension_placement', 'no_other_guard_sessions'):
            self.overrides[RECOVERY_OBSERVATION_SQL] = {**self.con.observation, field: False}
            with self.subTest(field=field):
                result = await self.inspect()
                self.assertEqual(result['status'], 'OBJECTS_OR_SESSIONS_PRESENT')
                self.assertFalse(result['flags'][field])
        self.con.execute.assert_not_awaited()

    async def test_record_field_count_must_match_exact_field_names(self):
        for query, fields, stage in ((NAMESPACE_SQL, self.con.namespace, 'SCHEMA_VERIFICATION'),
                (RECOVERY_OBSERVATION_SQL, self.con.observation, 'RECOVERY_METADATA')):
            self.overrides.clear()
            self.overrides[query] = _create_record({key: i for i, key in enumerate(fields)},
                                                  (*fields.values(), True))
            with self.subTest(stage=stage):
                await self.rejection('CATALOG_COMPATIBILITY', stage, ['metadata_shape_valid'])

    async def test_unpinned_cluster_is_rejected_before_schema_record_is_read(self):
        self.con.server_identity['system_identifier'] += 1
        await self.rejection('IDENTITY', 'CLUSTER_IDENTITY', [])
        self.assertFalse(any(call.args[0] == NAMESPACE_SQL for call in self.con.fetchrow.await_args_list))

    async def test_final_read_only_check_uses_record_key_membership(self):
        original = self.con.fetchrow.side_effect

        async def drift(sql, *args):
            row = await original(sql, *args)
            if sql == RECOVERY_OBSERVATION_SQL:
                self.con.identity['read_only'] = 'off'
            return row

        self.con.fetchrow.side_effect = drift
        await self.rejection('READ_ONLY', 'FINAL_READ_ONLY', ['read_only'])
