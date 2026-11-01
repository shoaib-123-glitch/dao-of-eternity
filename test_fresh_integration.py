"""Fresh workflow regressions; synthetic evidence and mocked processes only."""
from contextlib import redirect_stdout
import asyncio
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fresh_integration as fresh
import integration_runner as runner
import retirement_operator as safety
from test_retirement_operator import docker_facts
from test_integration_inspector import CONTAINER_ID


PINS = dict(container_id=CONTAINER_ID, database_oid=20001, admin_role='postgres')


class NativeDouble:
    def __init__(self, schema):
        self.schema, self.calls, self.files = schema, [], {}
        self.exit = 0
        self.namespace_oid, self.precommit_oid = 25000, 25000
        self.mutation = None
        self.bad_hash = False

    def __call__(self, args, *, interactive=False):
        self.calls.append((list(args), interactive))
        if args[0] == 'inspect':
            return json.dumps(docker_facts())
        if args[-2:] == ['postgres', '--version']:
            return 'postgres (PostgreSQL) 18.6\n'
        if args[-1] == 'pg_controldata':
            return 'Database system identifier: 7699864005918920747\n'
        if args[0] == 'cp':
            self.files[args[-1].split(':', 1)[1]] = Path(args[-2]).read_bytes()
        elif 'sha256sum' in args:
            return ('0' * 64 if self.bad_hash else safety._sha(self.files[args[-1]])) + '  ' + args[-1] + '\n'
        elif 'psql' in args:
            assert interactive
            variables = {a.split('=', 2)[1]: a.split('=', 2)[2] for a in args if a.startswith('--set=')}
            action = variables['dao_action']
            record = dict(version=1, phase='verified_absent' if action == 'verify' else 'provisioned',
                schema=self.schema, namespace_oid=None if action == 'verify' else self.namespace_oid,
                owner_oid=fresh.OWNER_OID, database='dao_eternity_test', database_oid=PINS['database_oid'],
                role='dao_test_user', container_id=CONTAINER_ID,
                cluster_identifier='7699864005918920747', checks_passed=True)
            if self.mutation:
                self.mutation(record)
            self.files[variables['dao_receipt_path']] = (json.dumps(record) + '\n').encode()
            if action == 'provision':
                self.files[variables['dao_precommit_path']] = (json.dumps({**record,
                    'phase': 'commit_unknown', 'namespace_oid': self.precommit_oid}) + '\n').encode()
            return self.exit
        elif 'cat' in args:
            if args[-1] not in self.files:
                safety.reject('DOCKER_COMMAND')
            return self.files[args[-1]].decode()
        return ''


class FreshWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dao fresh offline ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prepared = fresh.prepare(self.root)
        self.bundle = Path(self.prepared['bundle'])
        self.plan = runner.read_plan(self.bundle / (self.prepared['schema'] + '.plan.json'))
        self.native = NativeDouble(self.plan.schema)
        self.native_mock = patch.object(safety, '_native', side_effect=self.native).start()
        self.addCleanup(patch.stopall)

    def verify(self):
        return fresh.operate(self.bundle, allow_read_only_connection=True, **PINS)

    def provision(self):
        return fresh.operate(self.bundle, action='provision', allow_read_only_connection=True,
                             allow_database_writes=True)

    def assert_rejected(self, category, function, *args, **kwargs):
        with self.assertRaises(safety.OperatorRejected) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.category, category)

    def test_new_reservation_is_unique_and_never_has_an_oid_before_provisioning(self):
        second = fresh.prepare(self.root)
        self.assertNotEqual(second['schema'], self.plan.schema)
        self.assertNotIn(self.plan.schema, runner.RETIRED_SCHEMAS)
        self.assertNotIn('namespace_oid', json.loads((self.bundle / (self.plan.schema + '.plan.json')).read_text()))
        self.assertEqual(set(p.name for p in self.bundle.iterdir()),
                         {'seal.json', 'provision.psql', self.plan.schema + '.plan.json'})
        self.native_mock.assert_not_called()

    def test_retired_plan_and_forced_reservation_are_rejected(self):
        retired = next(iter(runner.RETIRED_SCHEMAS))
        with self.assertRaises(runner.IntegrationSafetyError):
            runner.require_fresh_plan(runner.IntegrationPlan(retired, runner.manifest_digest()))
        with patch.object(runner, 'uuid4', return_value=SimpleNamespace(hex=retired[7:])):
            with self.assertRaises(runner.IntegrationSafetyError):
                runner.prepare_plan(self.root)
        self.assertFalse((self.root / (retired + '.plan.json')).exists())

    def test_archival_plan_validation_remains_available_for_history(self):
        plan = runner.IntegrationPlan(next(iter(runner.RETIRED_SCHEMAS)), runner.manifest_digest())
        runner.validate_plan(plan)
        with patch.object(runner.getpass, 'getpass', side_effect=AssertionError('must not prompt')):
            with patch.object(runner, 'read_plan', return_value=plan), redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(['run', '--plan', 'unused', '--namespace-oid', '25000',
                                              '--allow-database-writes']), 1)

    def test_direct_retired_suite_cannot_reach_adoption(self):
        from test_postgres_suite_safety import config
        plan = runner.IntegrationPlan(next(iter(runner.RETIRED_SCHEMAS)), runner.manifest_digest())
        with patch.object(runner.IsolatedTestDatabase, 'adopt_empty') as adopt:
            with self.assertRaises(runner.IntegrationSafetyError):
                asyncio.run(runner.run_suite(config(), plan, 25000, recovery_directory=self.bundle,
                                               allow_database_writes=True))
            adopt.assert_not_called()

    def test_collision_never_overwrites_a_plan(self):
        snapshot = {p: p.read_bytes() for p in self.bundle.iterdir()}
        with patch.object(runner, 'uuid4', return_value=SimpleNamespace(hex=self.plan.schema[7:])):
            with self.assertRaises(FileExistsError):
                runner.prepare_plan(self.bundle)
        self.assertEqual(snapshot, {p: p.read_bytes() for p in self.bundle.iterdir()})

    def test_historical_directory_is_rejected_without_opening_files(self):
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            self.assert_rejected('HISTORICAL_DIRECTORY', fresh.prepare, fresh.PROJECT / 'integration_recovery')
            self.assert_rejected('HISTORICAL_DIRECTORY', fresh.check, fresh.PROJECT / 'integration_recovery' / 'new')

    def test_disabled_api_gates_precede_all_file_and_native_access(self):
        with patch.object(fresh, 'check', side_effect=AssertionError('must not read')):
            for value in (False, 1, None, 'true'):
                self.assert_rejected('CONNECTION_DISABLED', fresh.operate, '.env', allow_read_only_connection=value)
                self.assert_rejected('WRITES_DISABLED', fresh.operate, '.env', action='provision',
                                     allow_read_only_connection=True, allow_database_writes=value)
                self.assert_rejected('WRITES_DISABLED', fresh.run, '.env', allow_database_writes=value)
        self.native_mock.assert_not_called()

    def test_cli_defaults_require_explicit_gates_before_prompt_or_access(self):
        for args in ([], ['verify'], ['provision', '--allow-read-only-connection'], ['run']):
            with self.subTest(args=args), redirect_stdout(io.StringIO()), patch('builtins.input', side_effect=AssertionError):
                self.assertEqual(fresh.main(args), 2)
        self.native_mock.assert_not_called()

    def test_seal_check_is_entirely_offline(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fresh.main(['check', '--bundle', str(self.bundle)]), 0)
        self.native_mock.assert_not_called()

    def test_changed_batch_rejects_even_with_recomputed_digest(self):
        path = self.bundle / 'provision.psql'
        path.write_bytes(path.read_bytes() + b'-- mutation\n')
        seal_path = self.bundle / 'seal.json'
        seal = json.loads(seal_path.read_text())
        seal['batch_sha256'] = safety._sha(path.read_bytes())
        seal_path.write_text(json.dumps(seal))
        self.assert_rejected('SEAL_DRIFT', self.verify)
        self.native_mock.assert_not_called()

    def test_source_drift_rejects_before_docker(self):
        with patch.object(fresh, '_sources', return_value={}):
            self.assert_rejected('SEAL_DRIFT', self.verify)
        self.native_mock.assert_not_called()

    def test_new_seal_retains_reported_absence_and_unknown_historical_rollback(self):
        report = json.loads((self.bundle / 'seal.json').read_text())['retirement_report']
        self.assertEqual(report['status'], 'ABSENCE_OBSERVED')
        self.assertEqual(report['historical_rollback_verification'], 'UNKNOWN')
        self.assertEqual(report['source'], 'operator_report_2026-10-24')

    def test_local_attempt_flush_failure_prevents_all_docker_operations(self):
        with patch.object(safety.os, 'fsync', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.verify()
        self.native_mock.assert_not_called()
        self.assertTrue((self.bundle / 'verify.attempt.json').exists())
        with self.assertRaises(FileExistsError):
            self.verify()

    def test_success_record_flush_failure_cannot_be_retried(self):
        self.verify()
        original = safety._write_new
        def persist(path, record):
            if Path(path).name == 'provision.result.json':
                raise OSError('synthetic')
            return original(path, record)
        with patch.object(safety, '_write_new', side_effect=persist):
            with self.assertRaises(OSError):
                self.provision()
        self.assertTrue((self.bundle / 'provision.attempt.json').exists())
        with self.assertRaises(FileNotFoundError):
            fresh._proof(self.bundle, 'provision', fresh.check(self.bundle)[2])
        with self.assertRaises(FileExistsError):
            self.provision()

    def test_container_identity_mismatch_is_rejected_before_staging(self):
        def wrong(args, **kwargs):
            if args[0] == 'inspect':
                return json.dumps({**docker_facts(), 'id': 'b' * 64})
            return self.native(args, **kwargs)
        with patch.object(safety, '_native', side_effect=wrong):
            self.assert_rejected('CONTAINER_IDENTITY', self.verify)
        self.assertFalse(self.native.calls)

    def test_control_file_identity_is_checked_before_any_sql(self):
        def wrong(args, **kwargs):
            if args[-1] == 'pg_controldata':
                return 'Database system identifier: 1\n'
            return self.native(args, **kwargs)
        with patch.object(safety, '_native', side_effect=wrong):
            self.assert_rejected('CLUSTER_IDENTITY', self.verify)
        self.assertFalse(any('psql' in args for args, _ in self.native.calls))

    def test_changed_plan_target_rejects_before_docker(self):
        path = self.bundle / (self.plan.schema + '.plan.json')
        payload = json.loads(path.read_text())
        payload['target']['host'] = 'production'
        path.write_text(json.dumps(payload))
        with self.assertRaises(runner.IntegrationSafetyError):
            self.verify()
        self.native_mock.assert_not_called()

    def test_public_pins_are_strict_and_never_guessed(self):
        for kwargs in ({**PINS, 'container_id': 'short'}, {**PINS, 'database_oid': True},
                       {**PINS, 'database_oid': 1}, {**PINS, 'admin_role': 'dao_test_user'}):
            with self.assertRaises(safety.OperatorRejected):
                fresh.operate(self.bundle, allow_read_only_connection=True, **kwargs)
        self.native_mock.assert_not_called()

    def test_verify_records_only_absence_and_does_not_authorize_writes(self):
        result = self.verify()
        self.assertEqual(result['status'], 'VERIFIED_ABSENT')
        self.assertIsNone(result['namespace_oid'])
        self.assert_rejected('WRITES_DISABLED', fresh.operate, self.bundle, action='provision', allow_read_only_connection=True)
        self.assertFalse((self.bundle / 'provision.attempt.json').exists())
        commands = [a for a, _ in self.native.calls if 'psql' in a]
        self.assertEqual(len(commands), 1)
        self.assertIn('--set=dao_authorize_schema=DISABLED', commands[0])

    def test_actual_oid_and_matching_precommit_are_recorded_automatically(self):
        self.verify()
        result = self.provision()
        self.assertEqual(result['namespace_oid'], 25000)
        self.assertEqual(result['status'], 'PROVISIONED')
        self.assertEqual(json.loads((self.bundle / 'provision.precommit.json').read_text())['namespace_oid'], 25000)
        pins, receipt = fresh._proof(self.bundle, 'provision', fresh.check(self.bundle)[2])
        self.assertEqual(pins, PINS)
        self.assertEqual(receipt['namespace_oid'], 25000)

    def test_provision_requires_verification_proof(self):
        with self.assertRaises(FileNotFoundError):
            self.provision()
        self.native_mock.assert_not_called()

    def test_verification_and_provision_are_single_use_and_preserve_all_files(self):
        self.verify()
        before = {p: p.read_bytes() for p in self.bundle.iterdir()}
        with self.assertRaises(FileExistsError):
            self.verify()
        self.assertEqual(before, {p: p.read_bytes() for p in self.bundle.iterdir()})
        self.provision()
        before = {p: p.read_bytes() for p in self.bundle.iterdir()}
        with self.assertRaises(FileExistsError):
            self.provision()
        self.assertEqual(before, {p: p.read_bytes() for p in self.bundle.iterdir()})

    def test_wrong_oid_or_partial_success_stays_unknown(self):
        self.verify()
        self.native.precommit_oid = 25001
        self.assert_rejected('PRECOMMIT_OID_MISMATCH', self.provision)
        self.assertEqual(json.loads((self.bundle / 'provision.result.json').read_text())['status'], 'UNKNOWN')
        self.assertTrue((self.bundle / 'provision.precommit.raw.json').exists())
        self.assertFalse((self.bundle / 'provision.receipt.json').exists())

    def test_psql_failure_preserves_raw_receipts_and_never_retries(self):
        self.verify()
        self.native.exit = 3
        self.assert_rejected('PSQL_EXIT_UNKNOWN', self.provision)
        for name in ('provision.exit.json', 'provision.precommit.raw.json', 'provision.receipt.raw.json', 'provision.result.json'):
            self.assertTrue((self.bundle / name).is_file())
        with self.assertRaises(FileExistsError):
            self.provision()

    def test_staged_hash_failure_never_reaches_psql(self):
        self.native.bad_hash = True
        self.assert_rejected('STAGED_HASH', self.verify)
        self.assertFalse(any('psql' in a for a, _ in self.native.calls))
        self.assertEqual(json.loads((self.bundle / 'verify.result.json').read_text())['status'], 'UNKNOWN')

    def test_receipts_reject_wrong_identity_boolean_or_retired_oid(self):
        base = dict(version=1, phase='provisioned', schema=self.plan.schema, owner_oid=fresh.OWNER_OID,
                    database='dao_eternity_test', database_oid=PINS['database_oid'], role='dao_test_user',
                    container_id=CONTAINER_ID, cluster_identifier='7699864005918920747',
                    checks_passed=True, namespace_oid=25000)
        for key, value in (('namespace_oid', fresh.RETIRED_OID), ('namespace_oid', '25000'),
                           ('namespace_oid', True), ('owner_oid', 1), ('checks_passed', 1),
                           ('container_id', 'b' * 64), ('schema', next(iter(runner.RETIRED_SCHEMAS))),
                           ('database_oid', PINS['database_oid'] + 1), ('version', True)):
            with self.subTest(key=key), self.assertRaises(safety.OperatorRejected):
                fresh._receipt({**base, key: value}, 'provisioned', self.plan, PINS)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(safety.OperatorRejected):
            safety._json('{"namespace_oid":25000,"namespace_oid":16390}')

    def test_missing_postcommit_receipt_cannot_authorize_suite(self):
        self.verify()
        self.native.mutation = lambda r: r.update(checks_passed=False)
        self.assert_rejected('RECEIPT_IDENTITY', self.provision)
        with patch.object(runner, 'main') as main:
            with self.assertRaises(safety.OperatorRejected):
                fresh.run(self.bundle, allow_database_writes=True)
            main.assert_not_called()

    def test_receipt_tamper_rejects_suite_before_prompt_or_connection(self):
        self.verify()
        self.provision()
        path = self.bundle / 'provision.receipt.json'
        path.write_text(path.read_text() + ' ')
        with patch.object(runner, 'main') as main:
            self.assert_rejected('RECEIPT_DRIFT', fresh.run, self.bundle, allow_database_writes=True)
            main.assert_not_called()

    def test_suite_uses_actual_oid_explicit_write_gate_and_only_five_cases(self):
        self.verify()
        self.provision()
        with patch.object(runner, 'main', return_value=0) as main:
            self.assertEqual(fresh.run(self.bundle, allow_database_writes=True), 0)
            args = main.call_args.args[0]
        self.assertIn('--allow-database-writes', args)
        self.assertEqual(args[args.index('--namespace-oid') + 1], '25000')
        self.assertEqual(len(fresh.check(self.bundle)[2]['cases']), 5)
        with self.assertRaises(FileExistsError):
            fresh.run(self.bundle, allow_database_writes=True)

    def test_failed_suite_remains_unknown_and_single_use(self):
        self.verify()
        self.provision()
        with patch.object(runner, 'main', return_value=130):
            self.assertEqual(fresh.run(self.bundle, allow_database_writes=True), 130)
        self.assertEqual(json.loads((self.bundle / 'suite.result.json').read_text())['status'], 'UNKNOWN')
        with self.assertRaises(FileExistsError):
            fresh.run(self.bundle, allow_database_writes=True)

    def test_existing_runner_journal_prevents_adoption(self):
        self.verify()
        self.provision()
        (self.bundle / (self.plan.schema + '.json')).write_text('synthetic')
        with patch.object(runner, 'main') as main:
            self.assert_rejected('RUNNER_JOURNAL_EXISTS', fresh.run, self.bundle, allow_database_writes=True)
            main.assert_not_called()

    def test_psql_prompts_privately_and_clears_connection_overrides(self):
        self.verify()
        args = next(a for a, _ in self.native.calls if 'psql' in a)
        for value in ('-X', '-W', '--interactive', '--tty', 'PGHOSTADDR=172.17.0.2',
                      'PGOPTIONS=-c default_transaction_read_only=on', 'PGPASSWORD', 'PGPASSFILE'):
            self.assertIn(value, args)
        self.assertFalse(any('password=' in a.lower() for a in args))
        self.assertEqual(sum(a[0] == 'inspect' for a, _ in self.native.calls), 2)


class FreshSqlTests(unittest.TestCase):
    def setUp(self):
        self.plan = runner.IntegrationPlan('dao_it_' + 'c' * 32, runner.manifest_digest())
        self.text = fresh.batch_text(self.plan)

    def test_only_new_schema_can_be_created_and_no_permissions_are_modified(self):
        self.assertEqual(self.text.count('CREATE SCHEMA '), 1)
        self.assertIn('CREATE SCHEMA "' + self.plan.schema + '" AUTHORIZATION dao_test_user;', self.text)
        self.assertNotRegex(self.text, r'(?im)^\s*(DROP|GRANT|REVOKE|ALTER|CREATE TABLE|INSERT|UPDATE|DELETE|TRUNCATE)\b')
        self.assertNotIn('IF NOT EXISTS', self.text)

    def test_retired_name_and_oid_only_appear_in_absence_or_exclusion_checks(self):
        self.assertEqual(self.text.count('OR oid=16390'), 4)
        self.assertEqual(self.text.count("n.oid<>16390"), 2)
        self.assertNotIn('"' + next(iter(runner.RETIRED_SCHEMAS)) + '"', self.text)

    def test_complete_policy_before_create_and_after_actual_oid_is_retained(self):
        for present in (False, True):
            self.assertEqual(self.text.count(fresh._policy_sql(self.plan.schema, present=present)), 2)
        before = fresh._policy_sql(self.plan.schema, present=False)
        self.assertNotIn('has_schema_privilege(current_user,0::oid,', before)
        from integration_catalog import PRIVILEGE_FLAGS
        for flag in PRIVILEGE_FLAGS:
            self.assertIn('f.' + flag + ' IS FALSE', before)
        for term in ("w.name = 'pg_settings'", 'w.update_acl_safe IS TRUE', 'count(*) = 1'):
            self.assertIn(term, before)

    def test_verify_and_missing_write_authorization_stop_before_ddl(self):
        self.assertIn('\\set dao_action verify', self.text)
        self.assertLess(self.text.index('\\quit'), self.text.index('BEGIN READ WRITE;'))
        self.assertLess(self.text.index('FRESH_WRITES_NOT_AUTHORIZED'), self.text.index('BEGIN READ WRITE;'))
        self.assertEqual(self.text.count('BEGIN READ WRITE;'), 1)
        self.assertEqual(self.text.count('BEGIN READ ONLY;'), 2)
        self.assertIn('\\set ON_ERROR_STOP on', self.text)
        self.assertIn('\\set ON_ERROR_ROLLBACK off', self.text)

    def test_precommit_and_success_receipts_surround_commit(self):
        create = self.text.index('CREATE SCHEMA ')
        commit = self.text.index('COMMIT;', create)
        self.assertLess(self.text.index('\\o :dao_precommit_path'), commit)
        self.assertLess(commit, self.text.index("'phase','provisioned'"))
        self.assertLess(self.text.rindex('FRESH_SCHEMA_STATE_UNKNOWN'), self.text.index("'phase','provisioned'"))
        self.assertIn("'namespace_oid',:'dao_actual_oid'::bigint", self.text)

    def test_psql_conditionals_are_balanced(self):
        depth = 0
        for line in self.text.splitlines():
            if line.strip().startswith('\\if '):
                depth += 1
            elif line.strip() == '\\endif':
                depth -= 1
            self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)


if __name__ == '__main__':
    unittest.main()
