"""PG18 catalog representations and DDL review; no server or SQL execution."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import integration_runner as runner
import integration_support as infra
from integration_catalog import INVENTORY_SQL
from integration_manifest import TABLE_SHAPES
from test_inspector_record_shapes import record
from test_integration_followup import prepared_run
from test_integration_inspector import synthetic_evidence
from test_integration_safety import object_row
from test_postgres_suite_safety import PreparedConnectionDouble, config


def oid_json_inventory(rows, *, relations=True, types=True, records=True):
    """Model PG JSON OID strings, independently of native top-level OID ints."""
    result = deepcopy(rows)
    for row in result:
        fields = {'relation': ('namespace', 'toast', 'rowtype'),
                  'column': ('type', 'collation'), 'type': ('element', 'relation')}
        if row['kind'] not in fields or (row['kind'] == 'relation' and not relations
                                        or row['kind'] == 'type' and not types):
            continue
        details = json.loads(row['definition'])
        for key in fields[row['kind']]:
            if key in details:
                details[key] = str(details[key])
        row['definition'] = json.dumps(details, sort_keys=True)
    return [record(row) for row in result] if records else result


def change_definition(rows, kind, predicate, **changes):
    target = next(row for row in rows if row['kind'] == kind and predicate(json.loads(row['definition'])))
    target['definition'] = json.dumps({**json.loads(target['definition']), **changes}, sort_keys=True)


class InventoryWireCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.db, self.con = prepared_run(180006)
        self.definitions = infra._table_definitions()

    def validate(self, rows):
        self.db._validate_initial_inventory(rows, self.definitions)

    def test_native_record_fields_and_nested_oid_strings_validate_for_all_tables(self):
        rows = oid_json_inventory(self.con.inventory())
        original = infra._fingerprint(rows)
        self.validate(rows)
        self.assertEqual(len(self.db._tables), 27)
        self.assertEqual(infra._fingerprint(rows), original)
        self.assertTrue(all(type(row['oid']) is int for row in rows))
        self.assertTrue(all(type(json.loads(row['definition'])['namespace']) is str
                            for row in rows if row['kind'] == 'relation'))

    def test_json_namespace_strings_are_accepted_without_array_normalization_masking(self):
        self.validate(oid_json_inventory(self.con.inventory(), types=False))

    def test_json_element_strings_are_accepted_without_namespace_normalization_masking(self):
        self.validate(oid_json_inventory(self.con.inventory(), relations=False))

    def test_oid_comparison_accepts_only_bounded_canonical_values(self):
        for value in (0, 1, 4294967295):
            for encoded in (value, str(value)):
                with self.subTest(value=encoded):
                    self.assertEqual(infra._inventory_oid(encoded), value)
        for value in (None, True, False, 1.0, -1, 4294967296, '', '01', '00', '+1', '-1',
                      ' 1', '1 ', '1\n', '1.0', '1e3', '\u0661', '4294967296', '9' * 100):
            with self.subTest(value=value), self.assertRaises(infra.IntegrationSafetyError):
                infra._inventory_oid(value)

    def test_malformed_nested_oid_values_fail_closed(self):
        for kind, key in (('relation', 'namespace'), ('type', 'element')):
            for value in (None, True, 1.0, '01', '+2000', '4294967296'):
                rows = oid_json_inventory(self.con.inventory(), records=False)
                change_definition(rows, kind, lambda d: kind != 'type' or d['kind'] == 'b', **{key: value})
                with self.subTest(kind=kind, value=value), self.assertRaises(infra.IntegrationSafetyError):
                    self.validate([record(row) for row in rows])

    def test_wrong_namespace_or_array_target_remains_rejected(self):
        for kind, key in (('relation', 'namespace'), ('type', 'element')):
            rows = oid_json_inventory(self.con.inventory(), records=False)
            change_definition(rows, kind, lambda d: d['kind'] == ('r' if kind == 'relation' else 'b'),
                              **{key: '4294967295'})
            with self.subTest(kind=kind), self.assertRaises(infra.IntegrationSafetyError):
                self.validate([record(row) for row in rows])

    def test_nonprimary_index_in_json_string_namespace_is_rejected(self):
        rows = oid_json_inventory(self.con.inventory(), records=False)
        change_definition(rows, 'relation', lambda d: d['kind'] == 'i', primary_index=False)
        with self.assertRaises(infra.IntegrationSafetyError):
            self.validate([record(row) for row in rows])

    def test_internal_toast_relations_keep_their_separate_namespace(self):
        rows = oid_json_inventory(self.con.inventory(), records=False)
        for kind, name, oid in (('t', 'pg_toast_offline', 90000), ('i', 'pg_toast_offline_index', 90001)):
            rows.append(object_row('relation', oid, name, definition=json.dumps(
                dict(kind=kind, namespace='99', primary_index=False))))
        self.validate([record(row) for row in rows])

    def test_native_records_preserve_owner_acl_and_dependency_refusals(self):
        for field, value in (('owner', 99999), ('acl_safe', False), ('dependencies_safe', False)):
            rows = oid_json_inventory(self.con.inventory(), records=False)
            rows[-1][field] = value
            with self.subTest(field=field), self.assertRaises(infra.IntegrationSafetyError):
                self.validate([record(row) for row in rows])

    def test_pg18_missing_not_null_constraint_still_rejects(self):
        rows = oid_json_inventory(self.con.inventory(), records=False)
        target = next(row for row in rows if row['kind'] == 'constraint'
                      and json.loads(row['definition'])['type'] == 'n')
        rows.remove(target)
        with self.assertRaises(infra.IntegrationSafetyError):
            self.validate([record(row) for row in rows])

    def test_fingerprint_keeps_raw_definitions_and_detects_catalog_drift(self):
        rows = oid_json_inventory(self.con.inventory(), records=False)
        self.validate([record(row) for row in rows])
        self.assertEqual(infra._fingerprint(rows), infra._fingerprint([record(row) for row in rows]))
        changed = deepcopy(rows)
        change_definition(changed, 'type', lambda d: d['kind'] == 'b', element='4294967295')
        self.assertNotEqual(infra._fingerprint(rows), infra._fingerprint(changed))


class SetupWireCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def adopt(self, con, directory):
        with patch('integration_support.asyncpg.connect', AsyncMock(return_value=con)) as driver:
            db = await infra.IsolatedTestDatabase.adopt_empty(config(), con.schema, 20000,
                expected_server=infra.ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747),
                allow_database_writes=True, recovery_directory=directory)
        driver.assert_awaited_once()
        return db

    def wire_connection(self, *, invalid_inventory=False):
        con = PreparedConnectionDouble()
        original_row = con._fetchrow

        async def fetchrow(sql, *args):
            row = await original_row(sql, *args)
            return record(row) if row is not None else None

        async def fetch(sql, *args):
            if sql != INVENTORY_SQL:
                raise AssertionError('Unexpected offline catalog query')
            rows = oid_json_inventory(con.inventory(), records=False)
            if invalid_inventory:
                rows[-1]['dependencies_safe'] = False
            return [record(row) for row in rows]

        con.fetchrow.side_effect = fetchrow
        con.fetch.side_effect = fetch
        return con

    async def test_mocked_adoption_checkpoints_all_27_tables_with_driver_records(self):
        con = self.wire_connection()
        with tempfile.TemporaryDirectory(prefix='dao_setup_wire_offline_') as directory:
            db = await self.adopt(con, directory)
            self.assertEqual(db._state, 'ready')
            self.assertEqual(len(db.recovery.table_oids), 27)
            self.assertIsNotNone(db.recovery.inventory_digest)
            self.assertEqual(db.recovery.outcome, 'committed')
            self.assertEqual(sum(call.args[0].startswith('CREATE TABLE ') for call in con.execute.await_args_list), 27)
            # Mocked cleanup checks the same raw inventory; no real SQL is sent.
            await db.close()
            self.assertEqual(db.recovery.outcome, 'cleaned')
        con.close.assert_awaited_once()

    async def test_inventory_rejection_after_all_mocked_creates_matches_historical_shape(self):
        con = self.wire_connection(invalid_inventory=True)
        with tempfile.TemporaryDirectory(prefix='dao_setup_wire_offline_') as directory:
            with self.assertRaises(infra.IntegrationSafetyError) as raised:
                await self.adopt(con, directory)
            recovery = raised.exception.recovery
            self.assertEqual((recovery.phase, recovery.outcome), ('creating_tables', 'rollback_unverified'))
            self.assertEqual(recovery.table_oids, ())
            self.assertIsNone(recovery.inventory_digest)
            self.assertEqual(recovery.diagnostic['stage'], 'INVENTORY_VALIDATION')
            self.assertEqual(recovery.diagnostic['rollback'], 'ACKNOWLEDGED')
            self.assertEqual(recovery.diagnostic['rollback_verification'], 'UNKNOWN')
            self.assertEqual(sum(call.args[0].startswith('CREATE TABLE ') for call in con.execute.await_args_list), 27)
            self.assertFalse(any(call.args[0].startswith('DROP ') for call in con.execute.await_args_list))
        con.close.assert_awaited_once()


def ddl_clauses(body):
    """Split only the reviewed DDL grammar, outside literals and parentheses."""
    # Mask quoted literals so commas/parentheses in defaults cannot form syntax.
    masked = re.sub(r"'(?:[^']|'')*'", lambda match: ' ' * len(match.group()), body)
    depth, start, result = 0, 1, []
    for index, token in enumerate(masked):
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        if depth < 0:
            raise AssertionError('Unbalanced reviewed DDL')
        if token == ',' and depth == 1 or token == ')' and depth == 0:
            result.append(body[start:index].strip())
            start = index + 1
    if depth != 0 or start != len(body):
        raise AssertionError('Unexpected trailing reviewed DDL')
    return result


class DdlAndFreshPlanReviewTests(unittest.TestCase):
    def test_all_27_declarations_match_supported_fixed_grammar_and_manifest(self):
        definitions = infra._table_definitions()
        self.assertEqual(len(definitions), 27)
        self.assertEqual(set(definitions), set(TABLE_SHAPES))
        check_count = 0
        for name, body in definitions.items():
            with self.subTest(table=name):
                columns, primary, checks = [], [], []
                for clause in ddl_clauses(body):
                    table_key = re.fullmatch(r'PRIMARY KEY\s*\((\s*[a-z_]+(?:\s*,\s*[a-z_]+)*\s*)\)', clause)
                    check = re.fullmatch(r'CHECK\s*\(proposer\s*<>\s*recipient\)', clause)
                    if table_key:
                        primary.append(tuple(key.strip() for key in table_key[1].split(',')))
                    elif check:
                        checks.append(('proposer', 'recipient'))
                    else:
                        column = re.fullmatch(r"([a-z_]+)\s+(BIGINT|TEXT|JSONB|TIMESTAMPTZ|INT)\s+"
                            r"(PRIMARY KEY|NOT NULL)(?:\s+DEFAULT\s+(NOW\(\)|0|'[^']*'(?:::jsonb)?))?", clause)
                        self.assertIsNotNone(column)
                        columns.append(column[1])
                        if column[3] == 'PRIMARY KEY':
                            primary.append((column[1],))
                        if column[4]:
                            expected_type = 'TIMESTAMPTZ' if column[4] == 'NOW()' else (
                                'BIGINT' if column[4] == '0' else 'JSONB' if column[4].endswith('::jsonb') else 'TEXT')
                            self.assertEqual(column[2], expected_type)
                self.assertEqual(tuple(columns), TABLE_SHAPES[name].columns)
                self.assertEqual(primary, [TABLE_SHAPES[name].primary_key])
                self.assertEqual(tuple(checks), TABLE_SHAPES[name].check_columns)
                check_count += len(checks)
        self.assertEqual(check_count, 2)

    def test_fresh_plan_reservation_preserves_all_four_historical_files(self):
        with tempfile.TemporaryDirectory(prefix='dao_fresh_plan_offline_') as directory:
            old = synthetic_evidence(directory)
            originals = {path: path.read_bytes() for path in Path(directory).iterdir()}
            new = runner.prepare_plan(directory)
            self.assertNotEqual(old, new)
            self.assertNotEqual(runner.read_plan(old).schema, runner.read_plan(new).schema)
            self.assertEqual(originals, {path: path.read_bytes() for path in originals})
            self.assertEqual({path.resolve() for path in Path(directory).iterdir()},
                             {path.resolve() for path in (*originals, new)})

    def test_fresh_plan_collision_refuses_to_overwrite_history(self):
        with tempfile.TemporaryDirectory(prefix='dao_fresh_plan_offline_') as directory:
            old = synthetic_evidence(directory)
            before = {path: path.read_bytes() for path in Path(directory).iterdir()}
            suffix = runner.read_plan(old).schema.removeprefix('dao_it_')
            with patch.object(runner, 'uuid4') as generated:
                generated.return_value.hex = suffix
                with self.assertRaises(infra.IntegrationSafetyError):
                    runner.prepare_plan(directory)
            self.assertEqual(before, {path: path.read_bytes() for path in Path(directory).iterdir()})


class RetirementBatchStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.batch = Path(__file__).with_name('retire_empty_schema.psql').read_text(encoding='utf-8')

    def test_only_one_fixed_restrictive_drop_is_supplied(self):
        drops = re.findall(r'(?m)^DROP\s+.*;', self.batch)
        self.assertEqual(drops, ['DROP SCHEMA "dao_it_ea92ae2483dd4ac494729f5628b429e2" RESTRICT;'])
        self.assertNotRegex(self.batch, r'\bCASCADE\b')
        self.assertNotRegex(self.batch, r'(?m)^\s*(CREATE|ALTER|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE)\b')

    def test_default_inspection_exits_before_explicitly_confirmed_write(self):
        self.assertIn('\\set dao_action inspect', self.batch)
        self.assertIn(":'dao_action' IN ('inspect', 'retire')", self.batch)
        self.assertIn('\\set ON_ERROR_STOP on', self.batch)
        self.assertIn('\\set ON_ERROR_ROLLBACK off', self.batch)
        write = self.batch.index('BEGIN READ WRITE;')
        self.assertEqual(self.batch.count('BEGIN READ WRITE;'), 1)
        self.assertLess(self.batch.index('RETIREMENT_INSPECTION_ONLY_NO_DDL'), self.batch.index('\\quit'))
        self.assertLess(self.batch.index('\\quit'), self.batch.index('\\prompt'))
        self.assertLess(self.batch.index('RETIREMENT_NOT_CONFIRMED'), write)
        self.assertIn('RETIRE EMPTY dao_it_ea92ae2483dd4ac494729f5628b429e2 OID 16390 OWNER 16389', self.batch)

    def test_read_only_and_write_recheck_use_identical_identity_and_empty_predicates(self):
        initial = self.batch.split('BEGIN READ ONLY;', 1)[1].split('\\gset dao_initial_', 1)[0]
        rechecked = self.batch.split('BEGIN READ WRITE;', 1)[1].split('\\gset dao_rechecked_', 1)[0]
        self.assertEqual(initial[initial.index('SELECT COALESCE('):],
                         rechecked[rechecked.index('SELECT COALESCE('):])
        for required in ("'7699864005918920747'", '= 180006', "'172.17.0.2'", '= 5432',
                "'dao_eternity_test'", "n.oid = 16390 AND n.nspowner = 16389",
                "r.oid = 16389 AND r.rolname = 'dao_test_user'", 'aclexplode',
                'pg_class WHERE relnamespace = n.oid', 'pg_depend', 'pg_extension',
                "NOT has_database_privilege('dao_test_user', current_database(), 'CREATE')",
                "= :'dao_database_oid'::oid", 'pg_event_trigger', "default_transaction_read_only') = 'on'"):
            self.assertIn(required, initial)
        self.assertIn(":'dao_container_id' = :'dao_original_container_id'", self.batch)
        self.assertEqual(self.batch.count("'^[0-9a-f]{64}$'"), 2)

    def test_drop_runs_under_pinned_owner_and_preserves_evidence_files(self):
        self.assertIn('SET LOCAL ROLE dao_test_user;\nDROP SCHEMA', self.batch)
        self.assertIn('RESTRICT;\nRESET ROLE;\nCOMMIT;', self.batch)
        self.assertNotRegex(self.batch, r'(?m)^\s*\\(?:o\b|!|i\b|ir\b|gexec\b|copy\b|w\b)')
        self.assertNotIn('.plan.json', self.batch)
        self.assertNotIn('.precommit.json', self.batch)
        self.assertNotIn('.provisioning.json', self.batch)
        self.assertNotIn('RecoveryJournal', self.batch)

    def test_quiescence_timeouts_and_postcommit_absence_are_mandatory(self):
        self.assertEqual(self.batch.count("usename = 'dao_test_user'"), 2)
        self.assertNotIn('pg_terminate_backend', self.batch)
        self.assertIn("SET LOCAL lock_timeout = '3s';", self.batch)
        self.assertIn("SET LOCAL idle_in_transaction_session_timeout = '30s';", self.batch)
        self.assertIn("WHERE nspname = 'dao_it_ea92ae2483dd4ac494729f5628b429e2' OR oid = 16390", self.batch)
        self.assertLess(self.batch.index('RETIREMENT_POSTCOMMIT_STATE_UNKNOWN'),
                        self.batch.index('RETIREMENT_ABSENCE_OBSERVED_PRESERVE_ALL_ORIGINAL_EVIDENCE'))
        depth = 0
        for line in self.batch.splitlines():
            if line.strip().startswith('\\if '):
                depth += 1
            elif line.strip() == '\\endif':
                depth -= 1
            self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)
