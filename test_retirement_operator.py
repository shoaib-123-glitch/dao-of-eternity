"""Full operator scheduling with synthetic files and a mocked Docker process."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import retirement_operator as op
from test_integration_inspector import synthetic_evidence, CONTAINER_ID, PRIVATE


def docker_facts():
    return dict(id=CONTAINER_ID, name='/dao-postgres18-test', running=True,
        ports={'5432/tcp': [dict(HostIp='127.0.0.1', HostPort='5433')]},
        networks={'bridge': dict(IPAddress='172.17.0.2', GlobalIPv6Address='')})


class DockerDouble:
    def __init__(self):
        self.calls, self.files, self.directories = [], {}, set()
        self.facts = docker_facts()
        self.version = 'postgres (PostgreSQL) 18.6 (Debian 18.6)\n'
        self.control = 'Database system identifier:            7699864005918920747\n'
        self.psql_exit, self.bad_marker, self.bad_hash = 0, False, False
        self.fail_command = None

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if command[0] != 'docker':
            raise AssertionError('Only mocked Docker expected')
        args = command[1:]
        if self.fail_command and self.fail_command(args):
            return SimpleNamespace(returncode=1, stdout='', stderr=PRIVATE)
        if args[0] == 'inspect':
            output = json.dumps(self.facts)
        elif args[0] == 'cp':
            source, destination = args[-2:]
            self.files[destination.split(':', 1)[1]] = Path(source).read_bytes()
            output = ''
        elif 'psql' in args:
            if 'capture_output' in kwargs or 'stdin' in kwargs:
                raise AssertionError('Password console handles must be inherited')
            action = next(a.split('=', 2)[2] for a in args if a.startswith('--set=dao_action='))
            marker = next(a.split('=', 1)[1] for a in args if a.startswith('--output='))
            self.files[marker] = ((PRIVATE if self.bad_marker else op.INSPECT_MARKER if action == 'inspect'
                                   else op.RETIRE_MARKER) + '\n').encode()
            return SimpleNamespace(returncode=self.psql_exit)
        elif args[-2:] == ['postgres', '--version']:
            output = self.version
        elif args[-1] == 'pg_controldata':
            output = self.control
        elif 'mkdir' in args:
            if args[-1] in self.directories:
                return SimpleNamespace(returncode=1, stdout='', stderr=PRIVATE)
            self.directories.add(args[-1])
            output = ''
        elif 'sha256sum' in args:
            output = ('0' * 64 if self.bad_hash else op._sha(self.files[args[-1]])) + '  ' + args[-1] + '\n'
        elif 'test' in args:
            return SimpleNamespace(returncode=int(args[-1] in self.files), stdout='', stderr=PRIVATE)
        elif 'cat' in args:
            output = self.files[args[-1]].decode()
        else:
            raise AssertionError('Unreviewed mocked command')
        return SimpleNamespace(returncode=0, stdout=output, stderr=PRIVATE)


class OperatorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dao operator offline ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recovery = self.root / 'historical evidence'
        self.recovery.mkdir()
        synthetic_evidence(self.recovery)
        self.originals = {p: p.read_bytes() for p in self.recovery.iterdir()}
        self.records = self.root / 'new operator records'
        self.docker = DockerDouble()
        self.process = patch.object(op.subprocess, 'run', side_effect=self.docker).start()
        self.addCleanup(patch.stopall)

    def run_operator(self, **changes):
        args = dict(allow_read_only_connection=True, original_container_id=CONTAINER_ID,
                    admin_role='postgres', recovery_directory=self.recovery, record_directory=self.records)
        args.update(changes)
        with redirect_stdout(io.StringIO()):
            return op.operate(**args)

    def refusal(self, category, **changes):
        with self.assertRaises(op.OperatorRejected) as error:
            self.run_operator(**changes)
        self.assertEqual(error.exception.category, category)
        self.assertNotIn(PRIVATE, str(error.exception))
        self.assertEqual(self.originals, {p: p.read_bytes() for p in self.originals if p.exists()})

    def psql_calls(self):
        return [command for command, _ in self.docker.calls if 'psql' in command]

    def test_disabled_gate_precedes_files_and_native_processes(self):
        with patch.object(op, '_evidence', side_effect=AssertionError('no read')):
            self.refusal('DISABLED', allow_read_only_connection=False)
        self.process.assert_not_called()

    def test_inspect_success_is_fully_recorded_and_originals_unchanged(self):
        result = self.run_operator()
        run = Path(result['record_directory'])
        self.assertEqual(result['status'], 'INSPECT_PASSED')
        self.assertEqual(result['historical_rollback_verification'], 'UNKNOWN')
        snapshot = json.loads((run / 'evidence.json').read_text())
        self.assertEqual(snapshot['files'], {p.name: op._sha(data) for p, data in self.originals.items()})
        self.assertEqual((snapshot['database_oid'], snapshot['owner_oid']), (16388, 16389))
        self.assertEqual(set(p.name for p in run.iterdir()),
                         {'evidence.json', 'inspect.attempt.json', 'inspect.result.json'})
        self.assertEqual(self.originals, {p: p.read_bytes() for p in self.originals})
        self.assertEqual(len(self.psql_calls()), 1)
        self.assertIn('--set=dao_action=inspect', self.psql_calls()[0])
        self.assertTrue(all('--set=dao_action=retire' not in cmd for cmd, _ in self.docker.calls))

    def test_existing_read_only_evidence_needs_no_write_open(self):
        original_open = Path.open
        def guarded(path, mode='r', *args, **kwargs):
            if path in self.originals and mode != 'rb':
                raise AssertionError('Original evidence must never be opened for writes')
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            self.assertEqual(self.run_operator()['status'], 'INSPECT_PASSED')

    def test_unreadable_evidence_refuses_before_record_creation_or_docker(self):
        with patch('integration_inspector._read_file', side_effect=PermissionError(PRIVATE)):
            self.refusal('EVIDENCE_VALIDATION_UNAVAILABLE')
        self.process.assert_not_called()
        self.assertFalse(self.records.exists())

    def test_record_directory_access_denied_refuses_before_docker(self):
        with patch.object(op, '_new_run', side_effect=PermissionError(PRIVATE)):
            self.refusal('FILE_ACCESS_DENIED')
        self.process.assert_not_called()

    def test_existing_hash_record_is_never_overwritten(self):
        path = self.root / 'existing.json'
        path.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            op._write_new(path, {'new': True})
        self.assertEqual(path.read_bytes(), b'original')

    def test_hash_record_fsync_failure_refuses_before_docker(self):
        with patch.object(op.os, 'fsync', side_effect=OSError(PRIVATE)):
            self.refusal('FILE_OR_PROCESS_IO')
        self.process.assert_not_called()
        self.assertTrue(list(self.records.glob('operation-*/evidence.json')))

    def test_both_receipts_must_confirm_the_original_container(self):
        self.refusal('EVIDENCE_RECEIPT_IDENTITY', original_container_id='b' * 64)
        self.process.assert_not_called()

    def test_changed_database_or_owner_receipt_refuses_before_docker(self):
        for field, value, category in (('database_oid', '16387', 'EVIDENCE_RECEIPT_DATABASE_MISMATCH'),
                ('owner_oid', '16388', 'EVIDENCE_RECEIPT_IDENTITY')):
            path = self.recovery / (op.SCHEMA + '.precommit.json')
            data = json.loads(self.originals[path])
            data[field] = value
            path.write_text(json.dumps(data))
            with self.assertRaises(op.OperatorRejected) as error:
                self.run_operator()
            self.assertEqual(error.exception.category, category)
            path.write_bytes(self.originals[path])
        self.process.assert_not_called()

    def test_missing_or_changed_docker_identity_refuses_before_staging(self):
        for field, value in (('id', 'b' * 64), ('name', '/replacement'), ('running', False)):
            self.docker.facts = {**docker_facts(), field: value}
            self.refusal('CONTAINER_IDENTITY')
        self.assertFalse(self.psql_calls())
        self.assertFalse(self.docker.directories)

    def test_unexpected_port_bindings_are_rejected(self):
        for bindings in (None, [], [dict(HostIp='0.0.0.0', HostPort='5433')],
                [dict(HostIp='', HostPort='5433')], [dict(HostPort='5433')],
                [dict(HostIp='127.0.0.1', HostPort='5432')],
                [dict(HostIp='127.0.0.1', HostPort='5433')] * 2):
            self.docker.facts['ports'] = {'5432/tcp': bindings}
            self.refusal('PORT_BINDING')
        self.assertFalse(self.psql_calls())

    def test_additional_postgres_protocol_binding_refuses(self):
        self.docker.facts['ports']['5432/udp'] = [dict(HostIp='127.0.0.1', HostPort='5433')]
        self.refusal('PORT_BINDING')

    def test_ambiguous_json_and_networks_fail_closed(self):
        for text in ('null', '{}{}', '{"id":null,"id":null}', '{"running":NaN}'):
            with self.assertRaises((op.OperatorRejected, ValueError)):
                op.validate_docker_facts(text, CONTAINER_ID)
        self.docker.facts['networks']['second'] = dict(IPAddress='172.17.0.2')
        self.refusal('INTERNAL_ADDRESS')

    def test_wrong_internal_address_version_and_cluster_refuse_before_staging(self):
        self.docker.facts['networks']['bridge']['IPAddress'] = '172.17.0.3'
        self.refusal('INTERNAL_ADDRESS')
        self.docker.facts = docker_facts()
        self.docker.version = 'postgres (PostgreSQL) 18.5\n'
        self.refusal('POSTGRES_VERSION')
        self.docker.version = 'postgres (PostgreSQL) 18.6\n'
        for control in ('', 'Database system identifier: 1\n', self.docker.control * 2):
            self.docker.control = control
            self.refusal('CLUSTER_IDENTITY')
        self.assertFalse(self.docker.directories)

    def test_docker_nonzero_and_timeout_are_sanitized_without_retry(self):
        self.docker.fail_command = lambda args: args[0] == 'inspect'
        self.refusal('DOCKER_COMMAND')
        self.assertEqual(len(self.docker.calls), 1)
        with patch.object(op.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker', 20, output=PRIVATE)):
            self.refusal('DOCKER_TIMEOUT')

    def test_staged_hash_mismatch_refuses_before_password_prompt(self):
        self.docker.bad_hash = True
        self.refusal('STAGED_HASH')
        self.assertFalse(self.psql_calls())

    def test_unique_container_directory_uses_atomic_mkdir_and_preserves_prior_files(self):
        self.run_operator()
        old = dict(self.docker.files)
        self.run_operator()
        self.assertEqual(len(self.docker.directories), 2)
        self.assertEqual(old, {path: self.docker.files[path] for path in old})
        self.assertFalse(any('rm' in cmd or 'chmod' in cmd for cmd, _ in self.docker.calls))

    def test_staging_failure_has_no_psql_call(self):
        self.docker.fail_command = lambda args: 'mkdir' in args or args[0] == 'cp'
        self.refusal('DOCKER_COMMAND')
        self.assertFalse(self.psql_calls())

    def test_connection_attempt_record_is_durable_before_psql(self):
        original = self.docker.__call__
        def native(cmd, **kwargs):
            if 'psql' in cmd:
                attempts = list(self.records.glob('operation-*/inspect.attempt.json'))
                self.assertEqual(len(attempts), 1)
                self.assertEqual(json.loads(attempts[0].read_text())['outcome'], 'UNKNOWN')
            return original(cmd, **kwargs)
        self.process.side_effect = native
        self.run_operator()

    def test_credentials_are_prompted_in_container_without_environment_mutation(self):
        self.run_operator()
        args = self.psql_calls()[0]
        for required in ('--interactive', '--tty', '-X', '-W', '--port=5432',
                         '--host=172.17.0.2', '--dbname=dao_eternity_test',
                         'PGOPTIONS=-c default_transaction_read_only=on'):
            self.assertIn(required, args)
        self.assertNotIn('password=', ' '.join(args).lower())
        self.assertIn('PGPASSWORD', args)  # Cleared with env -u, never assigned.
        self.assertFalse(any('env' in kwargs for _, kwargs in self.docker.calls))

    def test_nonzero_psql_exit_and_missing_marker_remain_unknown(self):
        self.docker.psql_exit = 3
        self.refusal('PSQL_EXIT')
        result = json.loads(next(self.records.glob('operation-*/inspect.result.json')).read_text())
        self.assertEqual(result['outcome'], 'UNKNOWN')
        self.docker.psql_exit, self.docker.bad_marker = 0, True
        self.refusal('COMPLETION_MARKER')
        self.assertEqual(len(self.psql_calls()), 2)  # Two separate authorized fixture runs, no retries.

    def test_evidence_drift_before_connection_prevents_psql(self):
        with patch('integration_inspector.InspectionEvidence.assert_unchanged',
                   side_effect=op.OperatorRejected('EVIDENCE_CHANGED')):
            self.refusal('EVIDENCE_CHANGED')
        self.assertFalse(self.psql_calls())

    def test_result_record_failure_never_reports_success(self):
        original = op._write_new
        def write(path, data):
            if path.name == 'inspect.result.json':
                raise PermissionError(PRIVATE)
            return original(path, data)
        with patch.object(op, '_write_new', side_effect=write):
            self.refusal('FILE_ACCESS_DENIED')
        self.assertTrue(list(self.records.glob('operation-*/inspect.attempt.json')))

    def test_result_fsync_failure_cannot_release_an_inspection_token(self):
        original = op._write_new
        def write(path, data):
            result = original(path, data)
            if path.name == 'inspect.result.json':
                raise OSError(PRIVATE)  # A readable but unacknowledged success file.
            return result
        with patch.object(op, '_write_new', side_effect=write), redirect_stdout(io.StringIO()) as output:
            code = op.main(['--allow-read-only-connection', '--original-container-id', CONTAINER_ID,
                '--admin-role', 'postgres', '--recovery-directory', str(self.recovery),
                '--record-directory', str(self.records)])
        self.assertEqual(code, 1)
        self.assertNotIn('"inspection_token":', output.getvalue())
        proof = next(self.records.glob('operation-*/inspect.result.json'))
        self.refusal('INSPECTION_PROOF', action='retire', authorize_retirement=True,
            inspection_record=proof, inspection_token='0' * 64)

    def test_attempt_checkpoint_failure_prevents_psql(self):
        original = op._write_new
        def write(path, data):
            if path.name == 'inspect.attempt.json':
                raise PermissionError(PRIVATE)
            return original(path, data)
        with patch.object(op, '_write_new', side_effect=write):
            self.refusal('FILE_ACCESS_DENIED')
        self.assertFalse(self.psql_calls())

    def test_binding_drift_on_second_identity_check_prevents_psql(self):
        original = self.process.side_effect
        inspections = 0
        def native(cmd, **kwargs):
            nonlocal inspections
            if cmd[1] == 'inspect':
                inspections += 1
                if inspections == 2:
                    self.docker.facts['ports']['5432/tcp'][0]['HostIp'] = '0.0.0.0'
            return original(cmd, **kwargs)
        self.process.side_effect = native
        self.refusal('PORT_BINDING')
        self.assertFalse(self.psql_calls())

    def test_retirement_refuses_an_incorrect_inspection_token(self):
        inspected = self.run_operator()
        proof = Path(inspected['record_directory']) / 'inspect.result.json'
        self.refusal('INSPECTION_PROOF', action='retire', authorize_retirement=True,
                     inspection_record=proof, inspection_token='0' * 64)
        self.assertEqual(len(self.psql_calls()), 1)

    def test_environment_file_paths_are_refused_before_open(self):
        with patch.object(Path, 'stat', side_effect=AssertionError('no environment path inspection')):
            with self.assertRaises(op.OperatorRejected) as error:
                op._plain_path(self.root / '.env.test')
        self.assertEqual(error.exception.category, 'FILE_FORMAT')

    def test_cancelled_psql_retains_attempt_and_unknown_result(self):
        original = self.process.side_effect
        def cancel(cmd, **kwargs):
            if 'psql' in cmd:
                raise KeyboardInterrupt()
            return original(cmd, **kwargs)
        self.process.side_effect = cancel
        with self.assertRaises(KeyboardInterrupt):
            self.run_operator()
        result = json.loads(next(self.records.glob('operation-*/inspect.result.json')).read_text())
        self.assertEqual(result['outcome'], 'UNKNOWN')

    def test_retirement_gate_requires_separate_authorization_and_inspection_proof(self):
        self.refusal('RETIREMENT_NOT_AUTHORIZED', action='retire')
        self.process.assert_not_called()

    def test_separately_authorized_retirement_is_single_use_and_keeps_originals(self):
        inspected = self.run_operator()
        proof = Path(inspected['record_directory']) / 'inspect.result.json'
        result = self.run_operator(action='retire', authorize_retirement=True, inspection_record=proof,
                                   inspection_token=inspected['inspection_token'])
        self.assertEqual(result['status'], 'ABSENCE_OBSERVED')
        self.assertEqual(self.originals, {p: p.read_bytes() for p in self.originals})
        self.refusal('RECORD_OR_PATH_EXISTS', action='retire', authorize_retirement=True, inspection_record=proof,
                     inspection_token=inspected['inspection_token'])
        self.assertEqual(sum('--set=dao_action=retire' in args for args in self.psql_calls()), 1)

    def test_failed_retirement_commit_or_postcommit_check_remains_unknown(self):
        inspected = self.run_operator()
        self.docker.psql_exit = 3
        proof = Path(inspected['record_directory']) / 'inspect.result.json'
        self.refusal('PSQL_EXIT', action='retire', authorize_retirement=True, inspection_record=proof,
                     inspection_token=inspected['inspection_token'])
        results = list(self.records.glob('operation-*/retire.result.json'))
        self.assertEqual(json.loads(results[0].read_text())['outcome'], 'UNKNOWN')
        self.assertTrue((proof.parent / 'retirement.authorization.json').exists())


class RetirementSqlContractTests(unittest.TestCase):
    def test_full_policy_retains_the_existing_pg_settings_exception_contract(self):
        from integration_support import _standard_pg_settings_update
        from test_integration_followup import pg_settings_access
        text = op.policy_guard()
        expected = dict(is_pg_settings=True, owned=False, other_table_access=False,
                        other_column_access=False, table_update=True, update_acl_safe=True,
                        explicit_column_update=False)
        self.assertTrue(_standard_pg_settings_update(pg_settings_access()))
        for field, value in expected.items():
            self.assertIn('w.' + field + (' IS TRUE' if value else ' IS FALSE'), text)
            self.assertFalse(_standard_pg_settings_update({**pg_settings_access(), field: not value}))
        self.assertIn('w.oid > 0 AND w.oid < 16384', text)
        self.assertIn('count(*) = 1', text)
        self.assertIn("w.schema = 'pg_catalog'", text)
        self.assertIn("w.name = 'pg_settings' AND w.kind = 'v'", text)

    def test_sealed_batch_matches_canonical_preflight_policy_twice(self):
        path, digest = op._batch()
        self.assertEqual(digest, op._sha(path.read_bytes()))
        self.assertEqual(path.read_text().count(op.policy_guard()), 2)

    def test_changed_source_refuses_before_any_process(self):
        with patch.object(op, '_read', return_value=b'unreviewed SQL'):
            with self.assertRaises(op.OperatorRejected) as error:
                op._batch()
        self.assertEqual(error.exception.category, 'BATCH_SEAL')

    def test_literal_completion_rows_are_only_after_inspect_or_postcommit_success(self):
        text = (op.PROJECT / 'retire_empty_schema.psql').read_text()
        inspect = text.index("SELECT 'RETIREMENT_INSPECT_PASSED'")
        write = text.index('BEGIN READ WRITE;')
        self.assertLess(inspect, write)
        self.assertLess(text.index('RETIREMENT_POSTCOMMIT_STATE_UNKNOWN'),
                        text.index("SELECT '" + op.RETIRE_MARKER))

    def test_psql_conditionals_route_inspect_away_from_all_writes(self):
        # Trace psql control flow only; supplied catalog outcomes are Booleans.
        text = (op.PROJECT / 'retire_empty_schema.psql').read_text()
        for action in ('inspect', 'retire'):
            variables = dict.fromkeys(('dao_action', 'dao_admin_role', 'dao_database_oid',
                                      'dao_container_id', 'dao_original_container_id'), True)
            variables.update(dao_input_safe=True, dao_initial_safe=True, dao_effective_safe=True,
                dao_mode_inspecting=action == 'inspect', dao_retire_confirmed=True,
                dao_rechecked_safe=True, dao_absent_safe=True)
            stack, active, statements = [], True, []
            for line in text.splitlines():
                command = line.strip()
                if command.startswith('\\if '):
                    value = command[4:]
                    condition = value[3:-1] in variables if value.startswith(':{?') else variables[value[1:]]
                    stack.append((active, condition))
                    active = active and condition
                elif command == '\\else':
                    active = stack[-1][0] and not stack[-1][1]
                elif command == '\\endif':
                    active = stack.pop()[0]
                elif active:
                    if command == '\\quit':
                        break
                    if 'RAISE EXCEPTION' in command:
                        self.fail('Valid synthetic route hit a rejection branch')
                    statements.append(command)
            self.assertEqual(any(line.startswith('DROP SCHEMA') for line in statements), action == 'retire')
            self.assertEqual('BEGIN READ WRITE;' in statements, action == 'retire')
            if action == 'inspect':
                self.assertFalse(any(line.startswith('\\prompt') for line in statements))

    def test_psql_refusal_branches_stop_and_never_claim_absence(self):
        text = (op.PROJECT / 'retire_empty_schema.psql').read_text()
        for failed in ('dao_admin_role', 'dao_input_safe', 'dao_initial_safe', 'dao_effective_safe',
                       'dao_retire_confirmed', 'dao_rechecked_safe', 'dao_absent_safe'):
            variables = dict.fromkeys(('dao_action', 'dao_admin_role', 'dao_database_oid',
                                      'dao_container_id', 'dao_original_container_id', 'dao_input_safe',
                                      'dao_initial_safe', 'dao_effective_safe', 'dao_retire_confirmed',
                                      'dao_rechecked_safe', 'dao_absent_safe'), True)
            variables['dao_mode_inspecting'] = False
            if failed == 'dao_admin_role':
                variables.pop(failed)
            else:
                variables[failed] = False
            stack, active, statements, rejected = [], True, [], False
            for line in text.splitlines():
                command = line.strip()
                if command.startswith('\\if '):
                    value = command[4:]
                    condition = value[3:-1] in variables if value.startswith(':{?') else variables[value[1:]]
                    stack.append((active, condition))
                    active = active and condition
                elif command == '\\else':
                    active = stack[-1][0] and not stack[-1][1]
                elif command == '\\endif':
                    active = stack.pop()[0]
                elif active:
                    if 'RAISE EXCEPTION' in command:
                        rejected = True  # ON_ERROR_STOP exits here; no SQL is executed by this tracer.
                        break
                    statements.append(command)
            with self.subTest(failed=failed):
                self.assertTrue(rejected)
                self.assertFalse(any(line.startswith("SELECT '" + op.RETIRE_MARKER) for line in statements))
                self.assertEqual(any(line.startswith('DROP SCHEMA') for line in statements),
                                 failed == 'dao_absent_safe')
