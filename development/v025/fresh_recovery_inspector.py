"""Separate read-only inspector for the second failed attempt. Disabled by default.

check reads sealed archival evidence offline. inspect requires explicit connection
authorization and a masked test-role password. No Docker/SQL write/retry path.
Existing evidence is only read; results go to a separate new operation directory.
"""
import argparse
import asyncio
from dataclasses import dataclass
import getpass
import json
from pathlib import Path
import warnings

import fresh_integration as fresh
import integration_inspector as inspector
from integration_diagnostics import decode_setup_diagnostic, safe_setup_diagnostic
from integration_inspection_diagnostics import inspection_diagnostic
from integration_support import TestDatabaseConfig
import retirement_operator as files


PROJECT = Path(__file__).resolve().parent
SCHEMA = 'dao_it_8f601c52b5494c36adac957d4ad1454a'
NAMESPACE_OID = 24576
OWNER_OID = 16389
# Public identity recorded in the original provisioning receipts, read offline.
PINS = {'container_id': '18667622c6f45feca03e96570d82fde22ba822b66d1fc3c9585b9d9f2899b8ea',
        'database_oid': 16388, 'admin_role': 'postgres'}
DEFAULT_BUNDLE = PROJECT / 'fresh_integration' / 'operation-b2ffea0be1c24872a4338dfe5580f6bf'
DEFAULT_RECORDS = PROJECT / 'fresh_inspection_records'
OBSERVED_DIAGNOSTIC = {
    'category': 'SAFETY', 'stage': 'INVENTORY_QUERY', 'statement_ordinal': 0, 'table': None,
    'rollback': 'ACKNOWLEDGED', 'rollback_verification': 'UNKNOWN',
    'connection_cleanup': 'ACKNOWLEDGED'}
EVIDENCE_NAMES = (SCHEMA + '.plan.json', SCHEMA + '.json', 'seal.json', 'provision.psql',
    'offline.validation.json', 'verify.attempt.json', 'verify.exit.json',
    'verify.receipt.raw.json', 'verify.receipt.json', 'verify.result.json', 'verify.staging.json',
    'provision.attempt.json', 'provision.exit.json', 'provision.precommit.raw.json',
    'provision.precommit.json', 'provision.receipt.raw.json', 'provision.receipt.json',
    'provision.result.json', 'provision.staging.json', 'suite.attempt.json', 'suite.result.json')


@dataclass(frozen=True, repr=False)
class FreshEvidence(inspector.InspectionEvidence):
    def assert_unchanged(self):
        # ALL 21 snapshots, including attempt and seal files, are checked.
        for path, data in self.snapshots:
            if files._read(path) != data:
                files.reject('EVIDENCE_CHANGED')

    def hashes(self):
        return {path.name: files._sha(data) for path, data in self.snapshots}

    def digest(self):
        return files._sha(json.dumps(self.hashes(), sort_keys=True).encode())


def _exact(record, expected, category):
    if (type(record) is not dict or set(record) != set(expected)
            or any(type(record[k]) is not type(v) or record[k] != v for k, v in expected.items())):
        files.reject(category)


def read_evidence(bundle=DEFAULT_BUNDLE):
    """Validate the real fresh format; never construct a RecoveryJournal.

    In particular, do not rename/copy receipts into the retired inspector format
    or update any source seal to make a failed single-use reservation runnable.
    """
    bundle = fresh._bundle_path(bundle)
    snapshots = tuple((bundle / name, files._read(bundle / name)) for name in EVIDENCE_NAMES)
    payloads = {p.name: files._json(data.decode('utf-8-sig')) for p, data in snapshots if p.suffix == '.json'}
    _, plan, seal = fresh.check(bundle)
    if plan.schema != SCHEMA:
        files.reject('FRESH_SCHEMA_PIN')
    verified_pins, verified = fresh._proof(bundle, 'verify', seal)
    provisioned_pins, receipt = fresh._proof(bundle, 'provision', seal)
    if verified_pins != PINS or provisioned_pins != PINS or receipt['namespace_oid'] != NAMESPACE_OID:
        files.reject('FRESH_IDENTITY_PIN')
    precommit = fresh._receipt(payloads['provision.precommit.json'], 'commit_unknown', plan, PINS)
    if precommit['namespace_oid'] != NAMESPACE_OID:
        files.reject('PRECOMMIT_OID_MISMATCH')
    for raw, normalized in (('verify.receipt.raw.json', verified),
                            ('provision.precommit.raw.json', precommit),
                            ('provision.receipt.raw.json', receipt)):
        _exact(payloads[raw], normalized, 'RAW_RECEIPT_MISMATCH')
    seal_hash = files._sha(dict(snapshots)[bundle / 'seal.json'])
    _exact(payloads['suite.attempt.json'], {
        'version': 1, 'outcome': 'UNKNOWN', 'schema': SCHEMA,
        'namespace_oid': NAMESPACE_OID, 'seal_sha256': seal_hash}, 'SUITE_ATTEMPT')
    _exact(payloads['suite.result.json'], {'version': 1, 'status': 'UNKNOWN',
                                         'runner_exit_code': 1}, 'SUITE_RESULT')
    for action in ('verify', 'provision'):
        _exact(payloads[action + '.exit.json'], {'exit_code': 0}, 'PROVISIONING_EXIT')
        attempt = payloads[action + '.attempt.json']
        if (type(attempt) is not dict or set(attempt) != {
                'version', 'outcome', 'pins', 'created_utc', 'seal_sha256'}
                or type(attempt['created_utc']) is not str or not attempt['created_utc']):
            files.reject('PROVISIONING_ATTEMPT')
        _exact({k: v for k, v in attempt.items() if k != 'created_utc'}, {
            'version': 1, 'outcome': 'UNKNOWN', 'pins': PINS, 'seal_sha256': seal_hash},
            'PROVISIONING_ATTEMPT')
    journal = payloads[SCHEMA + '.json']
    expected = {'database': 'dao_eternity_test', 'role': 'dao_test_user', 'schema': SCHEMA,
        'namespace_oid': NAMESPACE_OID, 'owner_oid': OWNER_OID, 'phase': 'creating_tables',
        'outcome': 'rollback_unverified', 'table_oids': [], 'inventory_digest': None,
        'diagnostic': OBSERVED_DIAGNOSTIC}
    _exact(journal, expected, 'JOURNAL_IDENTITY_OR_STATE')
    diagnostic = decode_setup_diagnostic(journal['diagnostic'])
    if safe_setup_diagnostic(diagnostic) != OBSERVED_DIAGNOSTIC:
        files.reject('JOURNAL_DIAGNOSTIC')
    evidence = FreshEvidence(SCHEMA, NAMESPACE_OID, OWNER_OID, PINS['database_oid'],
        journal['phase'], journal['outcome'], diagnostic, snapshots)
    evidence.assert_unchanged()
    return evidence


def _record_root(directory, evidence):
    directory = files._plain_path(directory)
    bundle = evidence.snapshots[0][0].parent
    for protected in (PROJECT / 'integration_recovery', PROJECT / 'fresh_integration', bundle):
        if directory == protected or protected in directory.parents:
            files.reject('RECORD_DIRECTORY_OVERLAP')
    return directory


async def inspect_fresh(config, *, bundle=DEFAULT_BUNDLE, record_directory=DEFAULT_RECORDS,
                        allow_read_only_connection=False):
    if allow_read_only_connection is not True:
        files.reject('CONNECTION_DISABLED')
    # Reject bad configs before creating any local operation records.
    fresh.runner.require_target(config)
    evidence = read_evidence(bundle)
    root = _record_root(record_directory, evidence)
    run = files._new_run(root)
    # Each invocation consumes its own immutable operation; no retries on failure.
    files._write_new(run / 'attempt.json', {'version': 1, 'outcome': 'UNKNOWN',
        'schema': SCHEMA, 'namespace_oid': NAMESPACE_OID, 'evidence_sha256': evidence.digest()})
    files._write_new(run / 'evidence.hashes.json', evidence.hashes())
    try:
        evidence.assert_unchanged()
        report = await inspector._inspect_evidence(config, lambda: evidence,
            NAMESPACE_OID, OWNER_OID, allow_read_only_connection=True)
        evidence.assert_unchanged()  # Also covers the connection-disposal interval.
        result = {'version': 1, 'schema': SCHEMA, 'namespace_oid': NAMESPACE_OID,
                  'record_directory': str(run), 'evidence_sha256': evidence.digest(), **report}
        files._write_new(run / 'result.json', result)
        return result
    except BaseException as error:
        # No raw driver/error strings, passwords or SQL are serialized.
        path = run / 'result.json'
        if not path.exists():
            try:
                files._write_new(path, {'version': 1, 'status': 'UNKNOWN',
                    'rollback_verification': 'UNKNOWN', 'inspection_diagnostic': inspection_diagnostic(error)})
            except BaseException:
                pass  # Preserve partial files; never repair, replace or remove.
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'inspect'), nargs='?')
    parser.add_argument('--allow-read-only-connection', action='store_true')
    parser.add_argument('--bundle', default=str(DEFAULT_BUNDLE))
    parser.add_argument('--record-directory', default=str(DEFAULT_RECORDS))
    args = parser.parse_args(argv)
    if args.action is None or args.action == 'inspect' and not args.allow_read_only_connection:
        print('FRESH RECOVERY INSPECTION DISABLED [CONNECTION_DISABLED]')
        return 2
    try:
        evidence = read_evidence(args.bundle)  # Validate before any password prompt.
        if args.action == 'check':
            result = {'status': 'EVIDENCE_VALIDATED_OFFLINE', 'schema': SCHEMA,
                'namespace_oid': NAMESPACE_OID, 'evidence_files': len(evidence.snapshots),
                'evidence_sha256': evidence.digest(), 'historical_diagnostic': OBSERVED_DIAGNOSTIC}
        else:
            _record_root(args.record_directory, evidence)
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                password = getpass.getpass('Read-only recovery password for dao_test_user: ')
            config = TestDatabaseConfig('127.0.0.1', 5433, 'dao_eternity_test', 'dao_test_user', password)
            result = asyncio.run(inspect_fresh(config, bundle=args.bundle,
                record_directory=args.record_directory, allow_read_only_connection=True))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        print('FRESH RECOVERY INSPECTION CANCELLED [UNKNOWN]; preserve records.')
        return 130
    except Exception as error:
        category = error.category if isinstance(error, files.OperatorRejected) else 'VALIDATION_OR_OBSERVATION'
        print(json.dumps({'status': 'REJECTED', 'category': category,
                          'inspection_diagnostic': inspection_diagnostic(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
