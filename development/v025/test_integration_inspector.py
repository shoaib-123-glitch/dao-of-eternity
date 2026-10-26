"""Offline recovery inspection: synthetic files in temporary directories only."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import integration_inspector as inspector
import integration_runner as runner
from integration_catalog import IDENTITY_SQL, NAMESPACE_SQL, RECOVERY_OBSERVATION_SQL
from integration_diagnostics import SetupDiagnostic, safe_setup_diagnostic
from integration_recovery import RecoveryJournal, RecoveryRecord
from integration_support import IntegrationSafetyError, TestDatabaseConfig
from test_postgres_suite_safety import PreparedConnectionDouble, config


SCHEMA = 'dao_it_ea92ae2483dd4ac494729f5628b429e2'
CONTAINER_ID = 'a' * 64  # Synthetic ID, never discovered from Docker.
PRIVATE = 'postgresql://password SELECT private_exception_data'


def synthetic_evidence(directory, *, legacy=True):
    path = Path(directory) / (SCHEMA + '.plan.json')
    plan = dict(version=1, schema=SCHEMA, manifest_digest=runner.manifest_digest(), target=dict(runner.TARGET))
    journal = asdict(RecoveryRecord(SCHEMA, phase='creating_tables', outcome='rollback_unverified',
        namespace_oid=16390, owner_oid=16389))
    if legacy:
        journal.pop('diagnostic')
    else:
        journal['diagnostic'] = safe_setup_diagnostic(SetupDiagnostic())
    precommit = dict(phase='commit_unknown', container_id=CONTAINER_ID,
        # PostgreSQL jsonb_build_object receives ::oid values in the reviewed
        # writer. Its built-in oid output is a JSON string, not a JSON number.
        cluster_identifier='7699864005918920747', database='dao_eternity_test', database_oid='16388',
        role='dao_test_user', owner_oid='16389', schema=SCHEMA, namespace_oid='16390')
    receipt = {**precommit, 'phase': 'provisioned', **dict.fromkeys(inspector._RECEIPT_FLAGS, True)}
    records = dict(zip(('.plan.json', '.json', '.precommit.json', '.provisioning.json'),
                       (plan, journal, precommit, receipt)))
    for suffix, record in records.items():
        (Path(directory) / (SCHEMA + suffix)).write_text(json.dumps(record), encoding='utf-8')
    return path


class EvidenceReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dao_inspector_offline_')
        self.addCleanup(self.directory.cleanup)
        self.path = synthetic_evidence(self.directory.name)

    def read(self, **changes):
        args = dict(plan_path=self.path, namespace_oid=16390, owner_oid=16389, container_id=CONTAINER_ID)
        args.update(changes)
        return inspector.read_evidence(**args)

    def mutate(self, suffix, change):
        path = self.path.with_name(SCHEMA + suffix)
        record = json.loads(path.read_text())
        change(record)
        path.write_text(json.dumps(record), encoding='utf-8')

    def test_reported_legacy_state_is_read_without_constructing_or_saving_a_journal(self):
        before = {p: p.read_bytes() for p in Path(self.directory.name).iterdir()}
        with patch.object(RecoveryJournal, '__init__', side_effect=AssertionError('journal creation')), patch.object(
                RecoveryJournal, 'save', side_effect=AssertionError('journal update')):
            evidence = self.read()
            evidence.assert_unchanged()
        self.assertEqual((evidence.schema, evidence.namespace_oid, evidence.owner_oid), (SCHEMA, 16390, 16389))
        self.assertIsNone(evidence.diagnostic)
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.directory.name).iterdir()})

    def test_new_diagnostics_are_accepted_without_upgrading_legacy_evidence(self):
        self.mutate('.json', lambda r: r.update(diagnostic=safe_setup_diagnostic(SetupDiagnostic())))
        self.assertIsNotNone(self.read().diagnostic)

    def diagnostic(self, category, source, operation=None):
        with self.assertRaises(inspector.EvidenceValidationError) as raised:
            (operation or self.read)()
        self.assertEqual(inspector.preconnection_diagnostic(raised.exception),
                         {'category': category, 'source': source})
        self.assertNotIn(PRIVATE, str(raised.exception))

    def test_canonical_receipt_oid_strings_and_integer_receipts_are_both_exact(self):
        before = {p: p.read_bytes() for p in Path(self.directory.name).iterdir()}
        self.assertEqual(self.read().database_oid, 16388)
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.directory.name).iterdir()})
        for suffix in ('.precommit.json', '.provisioning.json'):
            self.mutate(suffix, lambda r: r.update({key: int(r[key])
                for key in ('namespace_oid', 'owner_oid', 'database_oid')}))
        self.assertEqual(self.read().database_oid, 16388)

    def test_receipt_oid_strings_do_not_relax_journal_numeric_types(self):
        self.mutate('.json', lambda r: r.update(namespace_oid='16390'))
        self.diagnostic('JOURNAL_IDENTITY', 'JOURNAL')

    def test_receipt_oid_parser_rejects_ambiguous_or_out_of_range_values(self):
        invalid = (True, False, 16390.0, ' 16390', '16390 ', '+16390', '-16390', '016390',
            '16390.0', '1.639e4', '\u0661\u0666\u0663\u0669\u0660', '', '16383',
            '4294967296', None, [], PRIVATE)
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                self.diagnostic('RECEIPT_OID_FORMAT', 'PRECOMMIT',
                    lambda: inspector._receipt_oid(value, inspector.EvidenceSource.PRECOMMIT))
        self.assertEqual(inspector._receipt_oid('4294967295', inspector.EvidenceSource.RECEIPT), 4294967295)

    def test_receipt_fixture_matches_reviewed_writer_fields_and_oid_casts(self):
        sql = Path(__file__).with_name('provision_disposable_schema.psql').read_text(encoding='utf-8')
        import re
        for phase, keys in (('commit_unknown', inspector._RECEIPT_KEYS),
                            ('provisioned', inspector._RECEIPT_KEYS | inspector._RECEIPT_FLAGS)):
            fragment = re.search(r"SELECT jsonb_build_object\('phase', '" + phase + r"',(.*?)\);", sql, re.S)
            self.assertIsNotNone(fragment)
            for key in keys - {'phase'}:
                self.assertIn("'" + key + "',", fragment.group(1))
            for key in ('namespace_oid', 'owner_oid', 'database_oid'):
                self.assertRegex(fragment.group(1), "'" + key + r"', :'[a-z_]+'::oid")

    def test_file_permissions_are_classified_without_printing_paths_or_details(self):
        original = Path.open
        for suffix, source in (('.plan.json', 'PLAN'), ('.json', 'JOURNAL'),
                ('.precommit.json', 'PRECOMMIT'), ('.provisioning.json', 'RECEIPT')):
            blocked = SCHEMA + suffix
            def denied(path, *args, **kwargs):
                if path.name == blocked:
                    raise PermissionError(PRIVATE)
                return original(path, *args, **kwargs)
            with self.subTest(source=source), patch.object(Path, 'open', new=denied):
                self.diagnostic('FILE_ACCESS_DENIED', source)

    def test_missing_and_other_io_failures_have_separate_codes(self):
        with patch.object(Path, 'lstat', side_effect=FileNotFoundError(PRIVATE)):
            self.diagnostic('FILE_MISSING', 'PLAN')
        with patch.object(Path, 'lstat', side_effect=OSError(PRIVATE)):
            self.diagnostic('FILE_IO', 'PLAN')

    def test_parent_permission_denial_is_an_access_failure(self):
        original = Path.lstat
        def denied(path, *args, **kwargs):
            if path == self.path.parent:
                raise PermissionError(PRIVATE)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', new=denied):
            self.diagnostic('FILE_ACCESS_DENIED', 'PLAN')

    def test_reparse_guard_remains_fail_closed(self):
        from types import SimpleNamespace
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_file_attributes=0x400)), \
                patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            self.diagnostic('FILE_REPARSE', 'PLAN')

    def test_utf8_bom_is_read_without_rewriting_any_evidence(self):
        for path in Path(self.directory.name).iterdir():
            path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
        before = {p: p.read_bytes() for p in Path(self.directory.name).iterdir()}
        self.read().assert_unchanged()
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.directory.name).iterdir()})

    def test_encoding_json_duplicate_keys_and_size_are_distinct(self):
        path = self.path.with_name(SCHEMA + '.json')
        for data, category in ((b'\xff\xfe{\x00}', 'FILE_ENCODING'), (b'{', 'FILE_JSON'),
                (b'{"phase":"creating_tables","phase":"ready"}', 'DUPLICATE_JSON_KEY'),
                (b'', 'FILE_TYPE_OR_SIZE'), (b' ' * 65537, 'FILE_TYPE_OR_SIZE')):
            path.write_bytes(data)
            with self.subTest(category=category):
                self.diagnostic(category, 'JOURNAL')

    def test_only_unapproved_journal_states_reject(self):
        evidence = self.read()
        self.assertEqual((evidence.phase, evidence.outcome), ('creating_tables', 'rollback_unverified'))
        path = self.path.with_name(SCHEMA + '.json')
        original = path.read_bytes()
        for changes in ({'phase': PRIVATE}, {'outcome': PRIVATE}, {'phase': []}, {'outcome': None}):
            path.write_bytes(original)
            self.mutate('.json', lambda r: r.update(changes))
            self.diagnostic('JOURNAL_STATE', 'JOURNAL')

    def test_plan_categories_distinguish_format_target_and_manifest(self):
        original = self.path.read_bytes()
        for change, category, source in ((lambda r: r.update(version=True), 'PLAN_FORMAT', 'PLAN'),
                (lambda r: r['target'].update(port=5432), 'PLAN_TARGET', 'PLAN'),
                (lambda r: r.update(manifest_digest='0' * 64), 'MANIFEST_REVIEW', 'MANIFEST')):
            self.path.write_bytes(original)
            self.mutate('.plan.json', change)
            self.diagnostic(category, source)

    def test_unreadable_manifest_source_remains_fail_closed(self):
        with patch('integration_manifest.Path.read_text', side_effect=PermissionError(PRIVATE)):
            self.diagnostic('MANIFEST_REVIEW', 'MANIFEST')

    def test_journal_shape_inventory_and_diagnostic_codes(self):
        path = self.path.with_name(SCHEMA + '.json')
        original = path.read_bytes()
        for changes, category in (({'unexpected': PRIVATE}, 'JOURNAL_FORMAT'),
                ({'namespace_oid': 16391}, 'JOURNAL_IDENTITY'),
                ({'inventory_digest': PRIVATE}, 'JOURNAL_INVENTORY'),
                ({'table_oids': None}, 'JOURNAL_INVENTORY'),
                ({'table_oids': [[PRIVATE, 20000]]}, 'JOURNAL_INVENTORY'),
                ({'diagnostic': {'stage': PRIVATE}}, 'JOURNAL_DIAGNOSTIC')):
            path.write_bytes(original)
            self.mutate('.json', lambda r: r.update(changes))
            self.diagnostic(category, 'JOURNAL')

    def test_receipt_shape_identity_oid_and_flag_codes(self):
        path = self.path.with_name(SCHEMA + '.provisioning.json')
        original = path.read_bytes()
        for changes, category in (({'unexpected': PRIVATE}, 'RECEIPT_FORMAT'),
                ({'phase': 'commit_unknown'}, 'RECEIPT_FORMAT'),
                ({'namespace_oid': '16391'}, 'RECEIPT_IDENTITY'),
                ({'cluster_identifier': PRIVATE}, 'RECEIPT_IDENTITY'),
                ({'namespace_oid': '016390'}, 'RECEIPT_OID_FORMAT'),
                ({'schema_empty': 1}, 'RECEIPT_FLAGS'),
                ({'database_oid': '16387'}, 'RECEIPT_DATABASE_MISMATCH')):
            path.write_bytes(original)
            self.mutate('.provisioning.json', lambda r: r.update(changes))
            self.diagnostic(category, 'RECEIPT')

    def test_unexpected_validation_failure_is_redacted(self):
        with patch.object(inspector, '_read_evidence', side_effect=RuntimeError(PRIVATE)):
            self.diagnostic('VALIDATION_UNAVAILABLE', 'INPUT')

    def test_argument_and_path_categories_precede_any_open(self):
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            self.diagnostic('ARGUMENTS', 'INPUT', lambda: self.read(namespace_oid=True))
            self.diagnostic('EVIDENCE_PATH', 'INPUT', lambda: self.read(plan_path='.env.test'))

    def test_environment_and_arbitrary_paths_reject_before_open(self):
        with patch.object(Path, 'open', side_effect=AssertionError('forbidden open')):
            for name in ('.env', '.env.test', 'journal.json', 'public.plan.json'):
                with self.subTest(name=name), self.assertRaises(IntegrationSafetyError):
                    self.read(plan_path=Path(self.directory.name) / name)

    def test_independent_identity_arguments_are_strict(self):
        for changes in ({'namespace_oid': True}, {'namespace_oid': 16391}, {'owner_oid': 16390},
                        {'owner_oid': '16389'}, {'container_id': 'dao-postgres18-test'},
                        {'container_id': 'b' * 64}, {'container_id': PRIVATE}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(IntegrationSafetyError):
                self.read(**changes)

    def test_modified_plan_target_or_manifest_rejects(self):
        original = self.path.read_bytes()
        for change in (lambda r: r['target'].update(port=5432),
                       lambda r: r['target'].update(system_identifier=1),
                       lambda r: r.update(manifest_digest='0' * 64)):
            self.path.write_bytes(original)
            self.mutate('.plan.json', change)
            with self.assertRaises(IntegrationSafetyError):
                self.read()

    def test_receipt_identity_and_boolean_flags_are_not_coerced(self):
        path = self.path.with_name(SCHEMA + '.provisioning.json')
        original = path.read_bytes()
        mutations = (('namespace_oid', 16391), ('owner_oid', 16390), ('database_oid', 16387),
            ('cluster_identifier', '1'), ('container_id', 'b' * 64), ('schema_empty', 1),
            ('acl_private', False), ('public_access_absent', None), ('unexpected', PRIVATE))
        for key, value in mutations:
            path.write_bytes(original)
            self.mutate('.provisioning.json', lambda r: r.update({key: value}))
            with self.subTest(key=key), self.assertRaises(IntegrationSafetyError):
                self.read()

    def test_malformed_unknown_and_duplicate_key_documents_reject(self):
        path = self.path.with_name(SCHEMA + '.json')
        original = path.read_bytes()
        for data in (b'{', b'[]', b'{"phase":"creating_tables","phase":"ready"}', b' ' * 65537):
            path.write_bytes(data)
            with self.subTest(size=len(data)), self.assertRaises(IntegrationSafetyError):
                self.read()
        path.write_bytes(original)
        self.mutate('.json', lambda r: r.update(phase=PRIVATE))
        with self.assertRaises(IntegrationSafetyError) as raised:
            self.read()
        self.assertNotIn(PRIVATE, str(raised.exception))

    def test_missing_receipt_fails_closed(self):
        self.path.with_name(SCHEMA + '.precommit.json').unlink()
        with self.assertRaises(IntegrationSafetyError):
            self.read()

    def test_symlink_refusal_precedes_file_read(self):
        with patch.object(Path, 'is_symlink', return_value=True), patch.object(
                Path, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaises(IntegrationSafetyError):
                self.read()

    def test_changed_evidence_is_detected_without_repair_or_overwrite(self):
        evidence = self.read()
        self.mutate('.json', lambda r: r.update(outcome='commit_unknown'))
        changed = self.path.with_name(SCHEMA + '.json').read_bytes()
        with self.assertRaises(IntegrationSafetyError):
            evidence.assert_unchanged()
        self.assertEqual(self.path.with_name(SCHEMA + '.json').read_bytes(), changed)

    def test_invalid_table_addresses_and_diagnostics_reject(self):
        path = self.path.with_name(SCHEMA + '.json')
        original = path.read_bytes()
        for changes in ({'table_oids': [[PRIVATE, 20000]]},
                {'table_oids': [['characters', True]]}, {'inventory_digest': PRIVATE},
                {'diagnostic': {'stage': PRIVATE}},
                {'diagnostic': {**safe_setup_diagnostic(SetupDiagnostic()), 'statement_ordinal': False}}):
            path.write_bytes(original)
            self.mutate('.json', lambda r: r.update(changes))
            with self.subTest(fields=tuple(changes)), self.assertRaises(IntegrationSafetyError):
                self.read()


class InspectorConnectionDouble(PreparedConnectionDouble):
    def __init__(self):
        super().__init__()
        self.schema = SCHEMA
        self.identity['role_oid'] = 16389
        self.namespace = dict(oid=16390, owner=16389, acl_safe=True)
        self.observation = dict.fromkeys(inspector._OBSERVATION_FLAGS, True)
        self.execute = AsyncMock(side_effect=AssertionError('No SQL execution API allowed'))

    async def _fetchrow(self, sql, *args):
        if sql == RECOVERY_OBSERVATION_SQL:
            return deepcopy(self.observation)
        return await super()._fetchrow(sql, *args)


class InspectorSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='dao_inspector_offline_')
        self.addCleanup(self.directory.cleanup)
        self.path = synthetic_evidence(self.directory.name)
        self.con = InspectorConnectionDouble()
        self.driver_patch = patch('integration_support.asyncpg.connect', AsyncMock(return_value=self.con))
        self.driver = self.driver_patch.start()
        self.addCleanup(self.driver_patch.stop)

    async def inspect(self, **changes):
        args = dict(allow_read_only_connection=True)
        args.update(changes)
        return await inspector.inspect_recovery(config(), self.path, 16390, 16389, CONTAINER_ID, **args)

    async def test_empty_legacy_namespace_is_observed_without_writes_or_false_rollback_claim(self):
        before = {p: p.read_bytes() for p in Path(self.directory.name).iterdir()}
        with patch.object(RecoveryJournal, '__init__', side_effect=AssertionError('journal creation')):
            result = await self.inspect()
        self.assertEqual(result['status'], 'EMPTY_SCHEMA_OBSERVED')
        self.assertEqual(result['rollback_verification'], 'UNKNOWN')
        self.assertEqual(result['historical_diagnostic']['rollback'], 'UNKNOWN')
        self.assertEqual(result['historical_diagnostic']['stage'], 'UNAVAILABLE')
        self.assertEqual(self.con.transaction.call_args.kwargs, dict(readonly=True, isolation='repeatable_read'))
        self.con.execute.assert_not_awaited()
        self.con.close.assert_awaited_once()
        self.assertEqual(self.driver.await_args.kwargs['port'], 5433)
        self.assertEqual(self.driver.await_args.kwargs['host'], '127.0.0.1')
        self.assertEqual(self.driver.await_args.kwargs['server_settings']['default_transaction_read_only'], 'on')
        self.assertEqual(before, {p: p.read_bytes() for p in Path(self.directory.name).iterdir()})

    async def test_default_and_nonboolean_opt_in_do_not_read_evidence_or_connect(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value), patch.object(inspector, 'read_evidence',
                    side_effect=AssertionError('evidence read')):
                with self.assertRaises(IntegrationSafetyError):
                    await self.inspect(allow_read_only_connection=value)
        self.driver.assert_not_called()

    async def test_unreviewed_client_target_rejects_before_reading_or_connecting(self):
        for port in (5432, 5434):
            other = TestDatabaseConfig('127.0.0.1', port, 'dao_eternity_test', 'dao_test_user', 'synthetic')
            with patch.object(inspector, 'read_evidence', side_effect=AssertionError('evidence read')):
                with self.assertRaises(IntegrationSafetyError):
                    await inspector.inspect_recovery(other, self.path, 16390, 16389, CONTAINER_ID,
                                                     allow_read_only_connection=True)
        self.driver.assert_not_called()

    async def test_bad_evidence_prevents_connection(self):
        self.path.with_name(SCHEMA + '.provisioning.json').write_text('{}')
        with self.assertRaises(IntegrationSafetyError):
            await self.inspect()
        self.driver.assert_not_called()

    async def test_unpinned_cluster_rejects_before_recovery_catalog_query(self):
        self.con.server_identity['system_identifier'] += 1
        with self.assertRaises(IntegrationSafetyError):
            await self.inspect()
        self.assertFalse(any(call.args[0] == RECOVERY_OBSERVATION_SQL for call in self.con.fetchrow.await_args_list))
        self.con.execute.assert_not_awaited()
        self.con.close.assert_awaited_once()

    async def test_wrong_endpoint_or_role_or_read_only_state_rejects(self):
        original = deepcopy(self.con.identity)
        for field, value in (('server_port', 5433), ('server_address', '127.0.0.1'),
                ('role_name', 'postgres'), ('session_role', 'postgres'), ('read_only', 'off'),
                ('default_read_only', 'off'), ('can_create_schema', True), ('role_oid', 16390),
                ('version', 180005)):
            self.con.identity = {**original, field: value}
            with self.subTest(field=field), self.assertRaises(IntegrationSafetyError):
                await self.inspect()
        self.con.execute.assert_not_awaited()

    async def test_changed_absent_or_public_namespace_rejects(self):
        for namespace in (None, dict(oid=16391, owner=16389, acl_safe=True),
                dict(oid=16390, owner=16390, acl_safe=True), dict(oid=16390, owner=16389, acl_safe=False)):
            self.con.namespace = namespace
            with self.subTest(namespace=namespace), self.assertRaises(IntegrationSafetyError):
                await self.inspect()

    async def test_existing_privilege_restrictions_remain_effective(self):
        for flag in self.con.privileges:
            self.con.privileges[flag] = True
            with self.subTest(flag=flag), self.assertRaises(IntegrationSafetyError):
                await self.inspect()
            self.con.privileges[flag] = False

    async def test_nonrelation_objects_and_other_sessions_prevent_empty_status(self):
        for flag in ('no_relations', 'no_namespace_dependencies', 'no_extension_placement', 'no_other_guard_sessions'):
            self.con.observation[flag] = False
            with self.subTest(flag=flag):
                self.assertEqual((await self.inspect())['status'], 'OBJECTS_OR_SESSIONS_PRESENT')
            self.con.observation[flag] = True
        self.con.execute.assert_not_awaited()

    async def test_unknown_boolean_and_identity_flags_fail_closed(self):
        original = deepcopy(self.con.observation)
        for key in inspector._OBSERVATION_FLAGS:
            self.con.observation = {**original, key: None}
            with self.subTest(key=key), self.assertRaises(IntegrationSafetyError):
                await self.inspect()
        for key in ('database_oid_verified', 'namespace_identity_verified', 'acl_private', 'schema_usage', 'schema_create'):
            self.con.observation = {**original, key: False}
            with self.subTest(key=key), self.assertRaises(IntegrationSafetyError):
                await self.inspect()

    async def test_catalog_error_is_redacted_and_connection_is_closed(self):
        self.con.fetchrow.side_effect = RuntimeError(PRIVATE)
        with self.assertRaises(IntegrationSafetyError) as raised:
            await self.inspect()
        self.assertNotIn(PRIVATE, str(raised.exception))
        self.con.close.assert_awaited_once()

    async def test_cancellation_closes_without_journal_updates_or_retry(self):
        self.con.fetchrow.side_effect = asyncio.CancelledError()
        with patch.object(RecoveryJournal, 'save', side_effect=AssertionError('journal update')):
            with self.assertRaises(asyncio.CancelledError):
                await self.inspect()
        self.driver.assert_awaited_once()
        self.con.close.assert_awaited_once()

    async def test_failed_close_terminates_without_retrying_or_writing(self):
        self.con.close.side_effect = RuntimeError(PRIVATE)
        self.assertEqual((await self.inspect())['status'], 'EMPTY_SCHEMA_OBSERVED')
        self.con.terminate.assert_called_once()
        self.con.execute.assert_not_awaited()

    async def test_unconfirmed_cleanup_rejects_the_observation(self):
        self.con.close.side_effect = RuntimeError(PRIVATE)
        self.con.terminate.side_effect = RuntimeError(PRIVATE)
        with self.assertRaises(IntegrationSafetyError) as raised:
            await self.inspect()
        self.assertNotIn(PRIVATE, str(raised.exception))

    async def test_changed_evidence_during_inspection_rejects_without_overwrite(self):
        original = self.con._fetchrow
        async def change(sql, *args):
            if sql == RECOVERY_OBSERVATION_SQL:
                self.path.with_name(SCHEMA + '.precommit.json').write_text('{}')
            return await original(sql, *args)
        self.con.fetchrow.side_effect = change
        with self.assertRaises(IntegrationSafetyError):
            await self.inspect()
        self.assertEqual(self.path.with_name(SCHEMA + '.precommit.json').read_text(), '{}')

    async def test_reader_exposes_no_execute_api_and_rejects_unreviewed_queries(self):
        evidence = inspector.read_evidence(self.path, 16390, 16389, CONTAINER_ID)
        reader = inspector._CatalogReader(self.con, evidence)
        self.assertFalse(hasattr(reader, 'execute'))
        for sql, args in (('SELECT 1', ()), ('DROP SCHEMA public', ()), (NAMESPACE_SQL, ('public',))):
            with self.subTest(sql_kind=sql.split()[0]), self.assertRaises(IntegrationSafetyError):
                await reader.fetchrow(sql, *args)
        with self.assertRaises(IntegrationSafetyError):
            await reader.fetch(IDENTITY_SQL)
        self.con.fetchrow.assert_not_awaited()

    async def test_real_driver_transaction_manager_records_read_only_begin_only(self):
        from types import SimpleNamespace
        from asyncpg.transaction import Transaction
        self.con._pool_release_ctr, self.con._top_xact = 0, None
        self.con._protocol = SimpleNamespace(is_in_transaction=lambda: False)
        self.con.is_closed = Mock(return_value=False)
        self.con.transaction = Mock(side_effect=lambda **kw: Transaction(
            self.con, kw['isolation'], kw['readonly'], False))
        statements = []
        async def record(sql, *args):
            statements.append(sql)
            if sql not in ('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;', 'COMMIT;', 'ROLLBACK;'):
                self.fail('Unexpected transaction control statement')
        self.con.execute.side_effect = record
        self.assertEqual((await self.inspect())['status'], 'EMPTY_SCHEMA_OBSERVED')
        self.assertEqual(statements, ['BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;', 'COMMIT;'])

    async def test_late_read_only_drift_rejects(self):
        original = self.con._fetchrow
        async def drift(sql, *args):
            result = await original(sql, *args)
            if sql == RECOVERY_OBSERVATION_SQL:
                self.con.identity['default_read_only'] = 'off'
            return result
        self.con.fetchrow.side_effect = drift
        with self.assertRaises(IntegrationSafetyError):
            await self.inspect()
        self.con.execute.assert_not_awaited()

    async def test_environment_cannot_change_the_target_or_enable_default_inspection(self):
        import os
        with patch.dict(os.environ, {'PGHOST': 'unapproved', 'PGPORT': '5432',
                'DAO_TEST_PORT': '5432', 'DAO_ALLOW_DATABASE_WRITES': 'true'}), redirect_stdout(io.StringIO()):
            self.assertEqual(inspector.main([]), 2)
            await self.inspect()
        self.assertEqual(self.driver.await_args.kwargs['host'], '127.0.0.1')
        self.assertEqual(self.driver.await_args.kwargs['port'], 5433)


class InspectorCommandTests(unittest.TestCase):
    def test_historical_state_with_writer_receipts_reaches_password_boundary(self):
        with tempfile.TemporaryDirectory(prefix='dao_inspector_offline_') as directory:
            path = synthetic_evidence(directory)
            with patch.object(inspector.getpass, 'getpass', side_effect=KeyboardInterrupt()) as prompt, \
                    patch.object(inspector, '_connect', AsyncMock()) as connect, redirect_stdout(io.StringIO()):
                code = inspector.main(['inspect', '--allow-read-only-connection', '--plan', str(path),
                    '--namespace-oid', '16390', '--owner-oid', '16389', '--container-id', CONTAINER_ID])
        self.assertEqual(code, 130)
        prompt.assert_called_once()
        connect.assert_not_called()

    def test_access_denial_reports_only_fixed_category_and_source_without_prompt(self):
        error = inspector.EvidenceValidationError(inspector.PreconnectionCategory.FILE_ACCESS_DENIED,
                                                  inspector.EvidenceSource.JOURNAL)
        with patch.object(inspector, 'read_evidence', side_effect=error), patch.object(
                inspector.getpass, 'getpass', side_effect=AssertionError('must not prompt')), patch.object(
                inspector, '_connect', AsyncMock()) as connect, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inspector.main(['inspect', '--allow-read-only-connection']), 1)
        self.assertEqual(output.getvalue().strip(), 'RECOVERY INSPECTION REJECTED [FILE_ACCESS_DENIED] source=JOURNAL')
        connect.assert_not_called()

    def test_preconnection_diagnostic_revalidates_mutated_fields(self):
        error = inspector.EvidenceValidationError(PRIVATE, PRIVATE)
        self.assertEqual(inspector.preconnection_diagnostic(error),
                         {'category': 'VALIDATION_UNAVAILABLE', 'source': 'UNKNOWN'})
        error.preconnection_code = inspector.PreconnectionCategory.JOURNAL_STATE
        error.evidence_source = inspector.EvidenceSource.JOURNAL
        self.assertEqual(inspector.preconnection_diagnostic(error),
                         {'category': 'JOURNAL_STATE', 'source': 'JOURNAL'})
        self.assertIsNone(inspector.preconnection_diagnostic(ValueError(PRIVATE)))

    def test_write_authorization_arguments_are_not_supported(self):
        from contextlib import redirect_stderr
        with patch.object(inspector, 'read_evidence', side_effect=AssertionError('read')), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                inspector.main(['inspect', '--allow-database-writes'])
        self.assertEqual(raised.exception.code, 2)

    def test_no_action_or_opt_in_reads_no_files_prompts_or_connections(self):
        for args in ([], ['inspect'], ['--allow-read-only-connection']):
            with patch.object(inspector, 'read_evidence', side_effect=AssertionError('read')), patch.object(
                    inspector.getpass, 'getpass', side_effect=AssertionError('prompt')), redirect_stdout(io.StringIO()):
                self.assertEqual(inspector.main(args), 2)

    def test_echo_fallback_never_connects(self):
        import warnings
        def echo(prompt):
            warnings.warn(PRIVATE, getpass_warning)
            self.fail('echo fallback allowed')
        getpass_warning = inspector.getpass.GetPassWarning
        with patch.object(inspector, 'read_evidence', return_value=object()), patch.object(
                inspector.getpass, 'getpass', side_effect=echo), patch.object(
                inspector, 'inspect_recovery', AsyncMock()) as inspect, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inspector.main(['inspect', '--allow-read-only-connection']), 1)
        inspect.assert_not_called()
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertIn('[CREDENTIAL_PROMPT] source=CREDENTIALS', output.getvalue())

    def test_read_errors_are_redacted(self):
        with patch.object(inspector, 'read_evidence', side_effect=ValueError(PRIVATE)), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inspector.main(['inspect', '--allow-read-only-connection']), 1)
        self.assertNotIn(PRIVATE, output.getvalue())
