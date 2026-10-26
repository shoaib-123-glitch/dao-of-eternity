"""Post-prompt regressions. Synthetic evidence and mocked connections only."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import asyncpg

import integration_inspector as inspector
from integration_catalog import IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL, NAMESPACE_SQL, RECOVERY_OBSERVATION_SQL
from integration_inspection_diagnostics import (
    InspectionCategory as Category, InspectionStage as Stage, InspectionDiagnostic,
    InspectionFailure, InspectionTrace, inspection_diagnostic, safe_inspection_diagnostic,
)
from integration_recovery import RecoveryJournal
from test_integration_followup import pg_settings_access
from test_integration_inspector import InspectorConnectionDouble, synthetic_evidence, SCHEMA, CONTAINER_ID, PRIVATE
from test_postgres_suite_safety import config


class PostPromptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dao_post_prompt_offline_')
        self.addCleanup(self.directory.cleanup)
        self.path = synthetic_evidence(self.directory.name)
        self.patch_driver = patch('integration_support.asyncpg.connect', AsyncMock())
        self.driver = self.patch_driver.start()
        self.addCleanup(self.patch_driver.stop)
        self.fresh()

    def fresh(self):
        self.con = InspectorConnectionDouble()
        self.driver.reset_mock(return_value=True, side_effect=True)
        self.driver.return_value = self.con

    async def inspect(self):
        return await inspector.inspect_recovery(config(), self.path, 16390, 16389,
            CONTAINER_ID, allow_read_only_connection=True)

    async def rejection(self, category, stage):
        with self.assertRaises(InspectionFailure) as raised:
            await self.inspect()
        diagnostic = inspection_diagnostic(raised.exception)
        self.assertEqual(diagnostic['category'], category)
        self.assertEqual(diagnostic['stage'], stage)
        self.assertNotIn(PRIVATE, str(raised.exception))
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))
        self.con.execute.assert_not_awaited()
        self.driver.assert_awaited_once()
        return diagnostic

    def fail_query(self, query, error):
        original = self.con._fetchrow
        async def fail(sql, *args):
            if sql == query:
                raise error
            return await original(sql, *args)
        self.con.fetchrow.side_effect = fail

    async def test_authentication_has_connection_stage_and_no_catalog_queries(self):
        self.driver.side_effect = asyncpg.InvalidPasswordError(PRIVATE)
        await self.rejection('AUTHENTICATION', 'CONNECTION')
        self.con.fetchrow.assert_not_awaited()
        self.con.close.assert_not_awaited()

    async def test_connection_refusal_timeout_and_disconnect_are_redacted(self):
        for error in (ConnectionRefusedError(PRIVATE), TimeoutError(PRIVATE),
                      asyncpg.ConnectionDoesNotExistError(PRIVATE)):
            self.fresh()
            self.driver.side_effect = error
            await self.rejection('CONNECTION', 'CONNECTION')

    async def test_session_role_database_and_destination_mismatch_are_identity_failures(self):
        for key, value in (('database_name', PRIVATE), ('role_name', 'postgres'),
                ('session_role', 'postgres'), ('server_address', '127.0.0.1'), ('server_port', 5433)):
            self.fresh()
            self.con.identity[key] = value
            await self.rejection('IDENTITY', 'SESSION_IDENTITY')

    async def test_cluster_mismatch_has_its_own_stage(self):
        self.con.server_identity['system_identifier'] += 1
        result = await self.rejection('IDENTITY', 'CLUSTER_IDENTITY')
        self.assertEqual(result['transaction_exit'], 'ROLLBACK_ACKNOWLEDGED')
        self.assertFalse(any(call.args[0] == RECOVERY_OBSERVATION_SQL for call in self.con.fetchrow.await_args_list))

    async def test_identity_catalog_errors_remain_catalog_compatibility(self):
        self.fail_query(IDENTITY_SQL, asyncpg.UndefinedColumnError(PRIVATE))
        await self.rejection('CATALOG_COMPATIBILITY', 'SESSION_IDENTITY')

    async def test_role_policy_retains_only_allowlisted_boolean_flags(self):
        self.con.identity['elevated'] = True
        self.con.identity['unreviewed_private_field'] = PRIVATE
        result = await self.rejection('PRIVILEGES', 'SESSION_IDENTITY')
        self.assertEqual(result['privilege_stage'], 'ROLE_POLICY')
        self.assertIs(result['privilege_flags']['elevated'], True)
        self.assertNotIn('unreviewed_private_field', result['privilege_flags'])

    async def test_cluster_identity_metadata_permission_error_is_preserved(self):
        self.fail_query(SERVER_IDENTITY_SQL, asyncpg.InsufficientPrivilegeError(PRIVATE))
        result = await self.rejection('PRIVILEGES', 'CLUSTER_IDENTITY')
        self.assertEqual(result['privilege_stage'], 'SERVER_IDENTITY')

    async def test_effective_privilege_policy_has_specific_stage_and_flags(self):
        self.con.privileges['outside_schema_create'] = True
        self.con.privileges['unreviewed_private_field'] = PRIVATE
        result = await self.rejection('PRIVILEGES', 'EFFECTIVE_PRIVILEGES')
        self.assertEqual(result['privilege_stage'], 'EFFECTIVE_PRIVILEGES_POLICY')
        self.assertIs(result['privilege_flags']['outside_schema_create'], True)
        self.assertNotIn('unreviewed_private_field', result['privilege_flags'])

    async def test_privilege_query_incomplete_metadata_is_catalog_compatibility(self):
        self.con.privileges['outside_schema_create'] = None
        await self.rejection('CATALOG_COMPATIBILITY', 'EFFECTIVE_PRIVILEGES')

    async def test_catalog_write_policy_remains_strict_and_diagnostic(self):
        self.con.privileges['system_catalog_writes'] = True
        self.con.fetch = AsyncMock(return_value=[])
        result = await self.rejection('PRIVILEGES', 'CATALOG_WRITE_POLICY')
        self.assertEqual(result['privilege_stage'], 'CATALOG_WRITES_POLICY')

    async def test_standard_pg_settings_exception_still_allows_read_only_observation(self):
        self.con.privileges['system_catalog_writes'] = True
        self.con.fetch = AsyncMock(return_value=[pg_settings_access()])
        result = await self.inspect()
        self.assertEqual(result['status'], 'EMPTY_SCHEMA_OBSERVED')
        self.assertEqual(result['connection_cleanup'], 'ACKNOWLEDGED')
        self.con.execute.assert_not_awaited()

    async def test_prepared_role_database_create_remains_forbidden(self):
        self.con.identity['can_create_schema'] = True
        result = await self.rejection('PRIVILEGES', 'PREPARED_ROLE_POLICY')
        self.assertEqual(result['failed_checks'], ['prepared_database_create_absent'])

    async def test_prepared_role_oid_and_exact_version_fail_separately(self):
        self.con.identity['role_oid'] = 16390
        await self.rejection('IDENTITY', 'PREPARED_ROLE_POLICY')
        self.fresh()
        self.con.identity['version'] = 180005
        await self.rejection('VERSION', 'PREPARED_ROLE_POLICY')

    async def test_schema_presence_oid_owner_and_acl_have_fixed_failed_checks(self):
        for namespace, check in ((None, 'namespace_present'),
                (dict(oid=16391, owner=16389, acl_safe=True), 'namespace_oid_verified'),
                (dict(oid=16390, owner=16390, acl_safe=True), 'namespace_owner_verified'),
                (dict(oid=16390, owner=16389, acl_safe=False), 'namespace_acl_private')):
            self.fresh()
            self.con.namespace = namespace
            result = await self.rejection('SCHEMA_VERIFICATION', 'SCHEMA_VERIFICATION')
            self.assertEqual(result['failed_checks'], [check])

    async def test_schema_catalog_query_errors_use_driver_class_metadata(self):
        for error, category in ((asyncpg.InsufficientPrivilegeError(PRIVATE), 'PRIVILEGES'),
                (asyncpg.UndefinedColumnError(PRIVATE), 'CATALOG_COMPATIBILITY'),
                (TimeoutError(PRIVATE), 'TIMEOUT'), (OSError(PRIVATE), 'CONNECTION')):
            self.fresh()
            self.fail_query(NAMESPACE_SQL, error)
            await self.rejection(category, 'SCHEMA_CATALOG')

    async def test_unknown_schema_catalog_shape_is_not_misreported_as_acl_policy(self):
        self.con.namespace['unreviewed_private_field'] = PRIVATE
        result = await self.rejection('CATALOG_COMPATIBILITY', 'SCHEMA_VERIFICATION')
        self.assertEqual(result['failed_checks'], ['metadata_shape_valid'])

    async def test_recovery_catalog_query_errors_have_their_own_stage(self):
        for error, category in ((asyncpg.UndefinedColumnError(PRIVATE), 'CATALOG_COMPATIBILITY'),
                (asyncpg.InsufficientPrivilegeError(PRIVATE), 'PRIVILEGES'),
                (RuntimeError(PRIVATE), 'CATALOG_COMPATIBILITY')):
            self.fresh()
            self.fail_query(RECOVERY_OBSERVATION_SQL, error)
            await self.rejection(category, 'RECOVERY_CATALOG')

    async def test_recovery_metadata_shapes_types_and_session_flag_are_bounded(self):
        self.con.observation['unreviewed_private_field'] = PRIVATE
        await self.rejection('CATALOG_COMPATIBILITY', 'RECOVERY_METADATA')
        for key, stage in (('no_relations', 'RECOVERY_METADATA'),
                           ('no_other_guard_sessions', 'SESSION_INSPECTION')):
            self.fresh()
            self.con.observation[key] = None
            result = await self.rejection('CATALOG_COMPATIBILITY', stage)
            self.assertEqual(result['failed_checks'], [key])

    async def test_receipt_database_oid_mismatch_is_identity_rejection(self):
        self.con.observation['database_oid_verified'] = False
        result = await self.rejection('IDENTITY', 'DATABASE_IDENTITY')
        self.assertEqual(result['failed_checks'], ['database_oid_verified'])

    async def test_recovery_schema_flags_have_schema_policy_stage(self):
        for flag in ('namespace_exists', 'namespace_identity_verified', 'acl_private'):
            self.fresh()
            self.con.observation[flag] = False
            result = await self.rejection('SCHEMA_VERIFICATION', 'RECOVERY_SCHEMA_POLICY')
            self.assertEqual(result['failed_checks'], [flag])

    async def test_effective_schema_access_false_has_privilege_policy_stage(self):
        for flag in ('schema_usage', 'schema_create'):
            self.fresh()
            self.con.observation[flag] = False
            result = await self.rejection('PRIVILEGES', 'SCHEMA_ACCESS_POLICY')
            self.assertEqual(result['failed_checks'], [flag])

    async def test_other_guard_sessions_and_objects_are_observations_not_rejections(self):
        for flag in ('no_relations', 'no_namespace_dependencies', 'no_extension_placement', 'no_other_guard_sessions'):
            self.fresh()
            self.con.observation[flag] = False
            result = await self.inspect()
            self.assertEqual(result['status'], 'OBJECTS_OR_SESSIONS_PRESENT')
            self.assertEqual(result['journal_phase'], 'creating_tables')
            self.assertEqual(result['journal_outcome'], 'rollback_unverified')
            self.assertEqual(result['rollback_verification'], 'UNKNOWN')

    async def test_final_read_only_drift_is_not_generic_safety(self):
        original = self.con._fetchrow
        async def drift(sql, *args):
            result = await original(sql, *args)
            if sql == RECOVERY_OBSERVATION_SQL:
                self.con.identity['default_read_only'] = 'off'
            return result
        self.con.fetchrow.side_effect = drift
        result = await self.rejection('READ_ONLY', 'FINAL_READ_ONLY')
        self.assertEqual(result['failed_checks'], ['default_read_only'])

    async def test_transaction_begin_failure_precedes_metadata_and_closes(self):
        self.con.transaction.return_value.__aenter__.side_effect = RuntimeError(PRIVATE)
        result = await self.rejection('TRANSACTION', 'TRANSACTION_BEGIN')
        self.assertEqual(result['transaction_exit'], 'BEGIN_RAISED')
        self.con.fetchrow.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_read_only_transaction_commit_failure_is_distinct(self):
        self.con.transaction.return_value.__aexit__.side_effect = RuntimeError(PRIVATE)
        result = await self.rejection('TRANSACTION', 'TRANSACTION_COMMIT')
        self.assertEqual(result['transaction_exit'], 'COMMIT_RAISED')

    async def test_rollback_failure_does_not_hide_original_schema_rejection(self):
        self.con.namespace['owner'] = 16390
        self.con.transaction.return_value.__aexit__.side_effect = asyncpg.ConnectionDoesNotExistError(PRIVATE)
        result = await self.rejection('SCHEMA_VERIFICATION', 'SCHEMA_VERIFICATION')
        self.assertEqual(result['transaction_exit'], 'ROLLBACK_RAISED')
        self.assertEqual(result['secondary_failures'], [{'stage': 'TRANSACTION_ROLLBACK', 'category': 'CONNECTION'}])

    async def test_unconfirmed_connection_cleanup_rejects_an_otherwise_successful_observation(self):
        self.con.close.side_effect = RuntimeError(PRIVATE)
        self.con.terminate.side_effect = RuntimeError(PRIVATE)
        result = await self.rejection('CONNECTION_CLEANUP', 'CONNECTION_CLEANUP')
        self.assertEqual(result['connection_cleanup'], 'UNCONFIRMED')
        self.assertEqual(result['transaction_exit'], 'COMMIT_ACKNOWLEDGED')

    async def test_close_failure_preserves_primary_catalog_reason(self):
        self.fail_query(RECOVERY_OBSERVATION_SQL, asyncpg.UndefinedColumnError(PRIVATE))
        self.con.close.side_effect = RuntimeError(PRIVATE)
        self.con.terminate.side_effect = RuntimeError(PRIVATE)
        result = await self.rejection('CATALOG_COMPATIBILITY', 'RECOVERY_CATALOG')
        self.assertEqual(result['connection_cleanup'], 'UNCONFIRMED')
        self.assertEqual(result['secondary_failures'], [{'stage': 'CONNECTION_CLEANUP', 'category': 'CONNECTION_CLEANUP'}])

    async def test_termination_fallback_is_visible_and_never_reconnects(self):
        self.con.close.side_effect = RuntimeError(PRIVATE)
        result = await self.inspect()
        self.assertEqual(result['connection_cleanup'], 'TERMINATED')
        self.con.close.assert_awaited_once()
        self.con.terminate.assert_called_once()
        self.driver.assert_awaited_once()

    async def test_revalidated_evidence_failure_preserves_fixed_source_and_stage(self):
        error = inspector.EvidenceValidationError(inspector.PreconnectionCategory.FILE_ACCESS_DENIED,
                                                  inspector.EvidenceSource.JOURNAL)
        with patch.object(inspector, 'read_evidence', side_effect=error):
            with self.assertRaises(inspector.EvidenceValidationError) as raised:
                await self.inspect()
        result = inspection_diagnostic(raised.exception)
        self.assertEqual((result['category'], result['stage']), ('EVIDENCE', 'EVIDENCE_REVALIDATION'))
        self.assertEqual(inspector.preconnection_diagnostic(raised.exception)['source'], 'JOURNAL')
        self.driver.assert_not_awaited()

    async def test_evidence_recheck_failure_is_local_not_a_server_rejection(self):
        original = self.con._fetchrow
        async def changed(sql, *args):
            result = await original(sql, *args)
            if sql == RECOVERY_OBSERVATION_SQL:
                self.path.with_name(SCHEMA + '.precommit.json').write_text('{}')
            return result
        self.con.fetchrow.side_effect = changed
        with self.assertRaises(inspector.EvidenceValidationError) as raised:
            await self.inspect()
        result = inspection_diagnostic(raised.exception)
        self.assertEqual((result['category'], result['stage']), ('EVIDENCE', 'EVIDENCE_RECHECK'))
        self.assertEqual(result['transaction_exit'], 'COMMIT_ACKNOWLEDGED')

    async def test_cancellation_during_metadata_preserves_cancellation_and_evidence(self):
        before = {p: p.read_bytes() for p in Path(self.directory.name).iterdir()}
        self.con.fetchrow.side_effect = asyncio.CancelledError()
        with patch.object(RecoveryJournal, 'save', side_effect=AssertionError('journal update')):
            with self.assertRaises(asyncio.CancelledError) as raised:
                await self.inspect()
        result = inspection_diagnostic(raised.exception)
        self.assertEqual((result['category'], result['stage']), ('CANCELLED', 'SESSION_IDENTITY'))
        self.assertEqual(result['transaction_exit'], 'ROLLBACK_ACKNOWLEDGED')
        self.assertEqual(result['connection_cleanup'], 'ACKNOWLEDGED')
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.directory.name).iterdir()})

    async def test_cancellation_during_rollback_is_preserved_with_original_reason(self):
        self.con.namespace['owner'] = 16390
        self.con.transaction.return_value.__aexit__.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await self.inspect()
        result = inspection_diagnostic(raised.exception)
        self.assertEqual(result['category'], 'SCHEMA_VERIFICATION')
        self.assertEqual(result['transaction_exit'], 'ROLLBACK_RAISED')
        self.assertEqual(result['secondary_failures'], [{'stage': 'TRANSACTION_ROLLBACK', 'category': 'CANCELLED'}])
        self.con.close.assert_awaited_once()

    async def test_cancellation_during_close_drains_one_worker(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def close(**kwargs):
            entered.set()
            await release.wait()
        self.con.close.side_effect = close
        task = asyncio.create_task(self.inspect())
        await entered.wait()
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await task
        self.assertEqual(inspection_diagnostic(raised.exception)['connection_cleanup'], 'ACKNOWLEDGED')
        self.con.close.assert_awaited_once()
        self.driver.assert_awaited_once()

    async def test_query_allowlist_still_has_no_write_or_unrestricted_select_path(self):
        evidence = inspector.read_evidence(self.path, 16390, 16389, CONTAINER_ID)
        reader = inspector._CatalogReader(self.con, evidence, InspectionTrace())
        with self.assertRaises(InspectionFailure) as raised:
            await reader.fetchrow('SELECT 1')
        result = inspection_diagnostic(raised.exception)
        self.assertEqual((result['category'], result['stage']), ('SAFETY', 'QUERY_ALLOWLIST'))
        self.assertEqual(result['failed_checks'], ['query_allowed'])
        self.assertFalse(hasattr(reader, 'execute'))
        self.con.fetchrow.assert_not_awaited()


class PostPromptOutputTests(unittest.TestCase):
    def test_untrusted_diagnostic_fields_never_reach_output(self):
        value = InspectionDiagnostic(stage=PRIVATE, category=PRIVATE, failed_checks=(PRIVATE,),
            transaction_exit=PRIVATE, connection_cleanup=PRIVATE,
            secondary_failures=((PRIVATE, PRIVATE),), privilege_stage=PRIVATE,
            privilege_flags=((PRIVATE, PRIVATE), ('outside_schema_create', PRIVATE)))
        result = safe_inspection_diagnostic(value)
        self.assertNotIn(PRIVATE, json.dumps(result))
        self.assertEqual(result['stage'], 'UNAVAILABLE')
        self.assertEqual(result['category'], 'SAFETY')
        self.assertEqual(result['privilege_flags'], {})

    def test_post_prompt_main_prints_only_category_and_sanitized_diagnostic(self):
        error = InspectionFailure(InspectionDiagnostic(Stage.SCHEMA_VERIFICATION,
            Category.SCHEMA_VERIFICATION, ('namespace_acl_private',)))
        error.args = (PRIVATE,)
        with patch.object(inspector, 'read_evidence', return_value=object()), patch.object(
                inspector.getpass, 'getpass', return_value='synthetic-password'), patch.object(
                inspector, 'inspect_recovery', AsyncMock(side_effect=error)), redirect_stdout(io.StringIO()) as output:
            code = inspector.main(['inspect', '--allow-read-only-connection'])
        self.assertEqual(code, 1)
        self.assertIn('[SCHEMA_VERIFICATION]', output.getvalue())
        self.assertIn('namespace_acl_private', output.getvalue())
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertNotIn('synthetic-password', output.getvalue())

    def test_empty_password_is_configuration_failure_before_any_connection(self):
        with patch.object(inspector, 'read_evidence', return_value=object()), patch.object(
                inspector.getpass, 'getpass', return_value=''), patch.object(
                inspector, 'inspect_recovery', AsyncMock()) as inspect, redirect_stdout(io.StringIO()) as output:
            code = inspector.main(['inspect', '--allow-read-only-connection'])
        self.assertEqual(code, 1)
        inspect.assert_not_called()
        diagnostic = json.loads(output.getvalue().splitlines()[1])['inspection_diagnostic']
        self.assertEqual((diagnostic['category'], diagnostic['stage']), ('CONFIGURATION', 'CONFIGURATION'))
