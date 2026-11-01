"""Second-attempt archival format and read-only recovery; synthetic files only."""
from contextlib import redirect_stdout
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import fresh_integration as fresh
import fresh_recovery_inspector as recovery
import integration_inspector as legacy
from integration_catalog import RECOVERY_OBSERVATION_SQL
from integration_recovery import RecoveryRecord
import retirement_operator as files
from test_inspector_record_shapes import record
from test_integration_inspector import InspectorConnectionDouble
from test_postgres_suite_safety import config


def synthetic_bundle(directory):
    with patch.object(fresh.runner, 'uuid4', return_value=SimpleNamespace(hex=recovery.SCHEMA[7:])):
        bundle = Path(fresh.prepare(directory)['bundle'])
    seal_hash = files._sha((bundle / 'seal.json').read_bytes())
    receipt_base = dict(version=1, schema=recovery.SCHEMA, owner_oid=recovery.OWNER_OID,
        database='dao_eternity_test', database_oid=recovery.PINS['database_oid'],
        role='dao_test_user', container_id=recovery.PINS['container_id'],
        cluster_identifier='7699864005918920747', checks_passed=True)
    for action, phase in (('verify', 'verified_absent'), ('provision', 'provisioned')):
        receipt = dict(receipt_base, phase=phase,
                       namespace_oid=None if action == 'verify' else recovery.NAMESPACE_OID)
        receipt_hash = files._write_new(bundle / (action + '.receipt.json'), receipt)
        files._write_new(bundle / (action + '.receipt.raw.json'), receipt)
        files._write_new(bundle / (action + '.result.json'), dict(version=1,
            status='VERIFIED_ABSENT' if action == 'verify' else 'PROVISIONED',
            seal_sha256=seal_hash, pins=recovery.PINS, receipt_sha256=receipt_hash))
        files._write_new(bundle / (action + '.attempt.json'), dict(version=1, outcome='UNKNOWN',
            pins=recovery.PINS, created_utc='2026-10-24T00:00:00+00:00', seal_sha256=seal_hash))
        files._write_new(bundle / (action + '.exit.json'), {'exit_code': 0})
        files._write_new(bundle / (action + '.staging.json'), dict(pins=recovery.PINS,
            directory='/tmp/' + recovery.SCHEMA + '.' + action + '-' + 'f' * 32))
    precommit = dict(receipt_base, phase='commit_unknown', namespace_oid=recovery.NAMESPACE_OID)
    files._write_new(bundle / 'provision.precommit.json', precommit)
    files._write_new(bundle / 'provision.precommit.raw.json', precommit)
    files._write_new(bundle / 'suite.attempt.json', dict(version=1, outcome='UNKNOWN',
        schema=recovery.SCHEMA, namespace_oid=recovery.NAMESPACE_OID, seal_sha256=seal_hash))
    files._write_new(bundle / 'suite.result.json', dict(version=1, status='UNKNOWN', runner_exit_code=1))
    files._write_new(bundle / 'offline.validation.json', {'version': 1, 'status': 'OFFLINE_VALIDATED'})
    journal = RecoveryRecord(recovery.SCHEMA, phase='creating_tables', outcome='rollback_unverified',
        namespace_oid=recovery.NAMESPACE_OID, owner_oid=recovery.OWNER_OID,
        diagnostic=dict(recovery.OBSERVED_DIAGNOSTIC))
    files._write_new(bundle / (recovery.SCHEMA + '.json'), asdict(journal))
    return bundle


class FreshEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dao second failure offline ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = synthetic_bundle(self.root)

    def test_fresh_receipts_are_incompatible_with_legacy_reader(self):
        with self.assertRaises(legacy.EvidenceValidationError) as raised:
            legacy.read_evidence(self.bundle / (recovery.SCHEMA + '.plan.json'),
                                 recovery.NAMESPACE_OID, recovery.OWNER_OID, recovery.PINS['container_id'])
        self.assertEqual(raised.exception.preconnection_code.value, 'FILE_MISSING')
        self.assertFalse((self.bundle / (recovery.SCHEMA + '.precommit.json')).exists())

    def test_fresh_reader_accepts_all_original_formats_without_translation(self):
        before = {p: p.read_bytes() for p in self.bundle.iterdir()}
        evidence = recovery.read_evidence(self.bundle)
        self.assertEqual(len(evidence.snapshots), 21)
        self.assertEqual(evidence.namespace_oid, 24576)
        self.assertEqual(evidence.database_oid, 16388)
        self.assertEqual(evidence.diagnostic.stage.value, 'INVENTORY_QUERY')
        self.assertEqual(before, {p: p.read_bytes() for p in self.bundle.iterdir()})

    def test_every_snapshot_including_last_suite_result_is_rechecked(self):
        evidence = recovery.read_evidence(self.bundle)
        for path, data in evidence.snapshots:
            path.write_bytes(data + b' ')
            with self.assertRaises(files.OperatorRejected) as raised:
                evidence.assert_unchanged()
            self.assertEqual(raised.exception.category, 'EVIDENCE_CHANGED')
            path.write_bytes(data)  # Synthetic fixture only.

    def test_mismatched_actual_oid_rejects_before_any_connection(self):
        path = self.bundle / (recovery.SCHEMA + '.json')
        journal = json.loads(path.read_text())
        journal['namespace_oid'] = 16390
        path.write_text(json.dumps(journal))
        with self.assertRaises(files.OperatorRejected):
            recovery.read_evidence(self.bundle)

    def test_diagnostic_is_not_reconstructed_or_rewritten(self):
        path = self.bundle / (recovery.SCHEMA + '.json')
        payload = json.loads(path.read_text())
        payload['diagnostic']['rollback_verification'] = 'ACKNOWLEDGED'
        path.write_text(json.dumps(payload))
        before = path.read_bytes()
        with self.assertRaises(files.OperatorRejected):
            recovery.read_evidence(self.bundle)
        self.assertEqual(path.read_bytes(), before)

    def test_changed_raw_receipt_or_suite_attempt_is_rejected(self):
        for name in ('provision.precommit.raw.json', 'suite.attempt.json'):
            path = self.bundle / name
            before = path.read_bytes()
            payload = json.loads(before)
            payload['namespace_oid'] = 25000
            path.write_text(json.dumps(payload))
            with self.assertRaises(files.OperatorRejected):
                recovery.read_evidence(self.bundle)
            path.write_bytes(before)  # Synthetic fixture only.

    def test_source_seals_remain_intact_and_are_never_resealed(self):
        path = self.bundle / 'seal.json'
        before = path.read_bytes()
        with patch.object(fresh, '_sources', return_value={}):
            with self.assertRaises(files.OperatorRejected) as raised:
                recovery.read_evidence(self.bundle)
        self.assertEqual(raised.exception.category, 'SEAL_DRIFT')
        self.assertEqual(path.read_bytes(), before)

    def test_no_records_can_be_written_inside_archives(self):
        evidence = recovery.read_evidence(self.bundle)
        for path in (self.bundle, self.bundle / 'records', recovery.PROJECT / 'integration_recovery',
                     recovery.PROJECT / 'fresh_integration' / 'records'):
            with self.assertRaises(files.OperatorRejected):
                recovery._record_root(path, evidence)

    def test_default_cli_blocks_before_all_evidence_reads_and_password_prompts(self):
        with patch.object(recovery, 'read_evidence', side_effect=AssertionError('must not read')), patch.object(
                recovery.getpass, 'getpass', side_effect=AssertionError('must not prompt')), redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.main([]), 2)
            self.assertEqual(recovery.main(['inspect', '--bundle', '.env']), 2)

    def test_offline_check_does_not_write_or_connect(self):
        before = {p: p.read_bytes() for p in self.bundle.iterdir()}
        with patch.object(recovery, 'inspect_fresh', side_effect=AssertionError('must not connect')), patch.object(
                recovery.getpass, 'getpass', side_effect=AssertionError('must not prompt')), redirect_stdout(io.StringIO()):
            self.assertEqual(recovery.main(['check', '--bundle', str(self.bundle)]), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.bundle.iterdir()})

    def test_evidence_reparse_and_environment_paths_reject_before_open(self):
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaises(files.OperatorRejected):
                recovery.read_evidence(self.root / '.env.test')


class FreshObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dao fresh recovery observation offline ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = synthetic_bundle(self.root)
        self.originals = {p: p.read_bytes() for p in self.bundle.iterdir()}
        self.records = self.root / 'separate observations'
        self.con = InspectorConnectionDouble()
        self.con.identity['role_oid'] = recovery.OWNER_OID
        self.con.schema = recovery.SCHEMA
        self.con.namespace = {'oid': recovery.NAMESPACE_OID, 'owner': recovery.OWNER_OID, 'acl_safe': True}
        original = self.con._fetchrow
        async def rows(sql, *args):
            value = await original(sql, *args)
            return record(value) if type(value) is dict else value
        self.con.fetchrow.side_effect = rows
        self.driver = patch('integration_support.asyncpg.connect', AsyncMock(return_value=self.con)).start()
        self.addCleanup(patch.stopall)

    async def observe(self, **kwargs):
        return await recovery.inspect_fresh(config(), bundle=self.bundle, record_directory=self.records,
                                            allow_read_only_connection=True, **kwargs)

    async def test_live_api_requires_exact_authorization_before_file_reads(self):
        with patch.object(recovery, 'read_evidence', side_effect=AssertionError('must not read')):
            for value in (False, None, 1, 'true'):
                with self.assertRaises(files.OperatorRejected):
                    await recovery.inspect_fresh(config(), allow_read_only_connection=value)
        self.driver.assert_not_called()

    async def test_shared_engine_reads_only_catalog_allowlist_and_actual_oid(self):
        result = await self.observe()
        self.assertEqual(result['status'], 'EMPTY_SCHEMA_OBSERVED')
        self.assertEqual(result['namespace_oid'], 24576)
        self.assertEqual(result['rollback_verification'], 'UNKNOWN')
        self.assertEqual(result['connection_cleanup'], 'ACKNOWLEDGED')
        self.con.execute.assert_not_awaited()
        self.con.transaction.assert_called_once_with(readonly=True, isolation='repeatable_read')
        self.assertEqual(self.originals, {p: p.read_bytes() for p in self.bundle.iterdir()})
        reads = [(c.args[0], c.args[1:]) for c in self.con.fetchrow.await_args_list]
        self.assertIn((RECOVERY_OBSERVATION_SQL, (recovery.SCHEMA, 24576, 16389, 16388)), reads)
        self.assertFalse(any(16390 in args for _, args in reads))
        self.assertTrue((Path(result['record_directory']) / 'result.json').exists())

    async def test_objects_or_sessions_never_prove_historical_rollback(self):
        self.con.observation['no_relations'] = False
        result = await self.observe()
        self.assertEqual(result['status'], 'OBJECTS_OR_SESSIONS_PRESENT')
        self.assertEqual(result['rollback_verification'], 'UNKNOWN')
        self.con.execute.assert_not_awaited()

    async def test_missing_or_wrong_namespace_is_rejected_without_writes(self):
        self.con.namespace['oid'] = 24577
        with self.assertRaises(Exception) as raised:
            await self.observe()
        self.assertIsNotNone(getattr(raised.exception, 'inspection_diagnostic', None))
        self.con.execute.assert_not_awaited()
        self.assertEqual(self.originals, {p: p.read_bytes() for p in self.bundle.iterdir()})
        result = json.loads(next(self.records.glob('operation-*/result.json')).read_text())
        self.assertEqual(result['status'], 'UNKNOWN')

    async def test_failed_observation_is_not_retried_and_preserves_partial_records(self):
        self.driver.side_effect = RuntimeError('private database message')
        with self.assertRaises(Exception):
            await self.observe()
        self.driver.assert_awaited_once()
        self.assertEqual(len(list(self.records.glob('operation-*'))), 1)
        result = next(self.records.glob('operation-*/result.json')).read_text()
        self.assertNotIn('private database message', result)
        self.assertEqual(json.loads(result)['status'], 'UNKNOWN')

    async def test_local_attempt_flush_failure_blocks_connection(self):
        with patch.object(files.os, 'fsync', side_effect=OSError('private disk error')):
            with self.assertRaises(OSError):
                await self.observe()
        self.driver.assert_not_called()
        self.assertTrue(next(self.records.glob('operation-*/attempt.json')).exists())

    async def test_post_close_evidence_drift_prevents_success_record(self):
        async def close(**kwargs):
            path = self.bundle / 'suite.result.json'
            path.write_bytes(path.read_bytes() + b' ')  # Synthetic fixture only.
        self.con.close.side_effect = close
        with self.assertRaises(files.OperatorRejected):
            await self.observe()
        result = json.loads(next(self.records.glob('operation-*/result.json')).read_text())
        self.assertEqual(result['status'], 'UNKNOWN')

    async def test_observation_never_makes_the_consumed_suite_reusable(self):
        await self.observe()
        with patch.object(fresh.runner, 'main') as main:
            with self.assertRaises(files.OperatorRejected) as raised:
                fresh.run(self.bundle, allow_database_writes=True)
            self.assertEqual(raised.exception.category, 'RUNNER_JOURNAL_EXISTS')
            main.assert_not_called()
        self.assertEqual(self.originals, {p: p.read_bytes() for p in self.bundle.iterdir()})


if __name__ == '__main__':
    unittest.main()
