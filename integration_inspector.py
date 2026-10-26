"""Opt-in, fixed-query recovery observation. Never writes evidence or database objects."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
import getpass
import json
from pathlib import Path
import re
import stat
import warnings

from integration_catalog import (
    IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL, CATALOG_WRITES_SQL,
    NAMESPACE_SQL, RECOVERY_OBSERVATION_SQL,
)
from integration_diagnostics import decode_setup_diagnostic, safe_setup_diagnostic, TABLE_NAMES
from integration_inspection_diagnostics import (
    InspectionStage, InspectionCategory, InspectionTrace, InspectionFailure, inspection_diagnostic,
)
from integration_runner import IntegrationPlan, TARGET, validate_plan, require_target
from integration_support import (
    ExpectedServerIdentity, FailureCategory, IntegrationSafetyError, SCHEMA_PATTERN,
    TestDatabaseConfig, _connect, _close_connection, _terminal_result, verify_connection,
    failure_category,
)


_PHASES = frozenset(('not_started', 'connecting', 'verifying', 'connecting_prepared_namespace',
    'verifying_prepared_namespace', 'creating_schema', 'creating_tables', 'inventory',
    'committing_setup', 'setup_committed', 'ready', 'cleanup_verifying', 'dropping_objects',
    'committing_cleanup', 'cleaned', 'connection_close_unconfirmed'))
_OUTCOMES = frozenset(('no_writes', 'rollback_unverified', 'commit_unknown', 'committed',
    'cleanup_rollback_unverified', 'cleanup_commit_unknown', 'cleaned'))
_JOURNAL_KEYS = frozenset(('schema', 'phase', 'outcome', 'database', 'role',
    'namespace_oid', 'owner_oid', 'inventory_digest', 'table_oids'))
_RECEIPT_KEYS = frozenset(('phase', 'container_id', 'cluster_identifier', 'database',
    'database_oid', 'role', 'owner_oid', 'schema', 'namespace_oid'))
_RECEIPT_FLAGS = frozenset(('owner_verified', 'acl_private', 'public_access_absent',
    'schema_empty', 'database_create_disabled'))
_OBSERVATION_FLAGS = frozenset(('database_oid_verified', 'namespace_exists',
    'namespace_identity_verified', 'acl_private', 'schema_usage', 'schema_create',
    'no_relations', 'no_namespace_dependencies', 'no_extension_placement', 'no_other_guard_sessions'))


class PreconnectionCategory(str, Enum):
    ARGUMENTS = 'ARGUMENTS'
    EVIDENCE_PATH = 'EVIDENCE_PATH'
    FILE_MISSING = 'FILE_MISSING'
    FILE_ACCESS_DENIED = 'FILE_ACCESS_DENIED'
    FILE_IO = 'FILE_IO'
    FILE_TYPE_OR_SIZE = 'FILE_TYPE_OR_SIZE'
    FILE_REPARSE = 'FILE_REPARSE'
    FILE_ENCODING = 'FILE_ENCODING'
    FILE_JSON = 'FILE_JSON'
    DUPLICATE_JSON_KEY = 'DUPLICATE_JSON_KEY'
    PLAN_FORMAT = 'PLAN_FORMAT'
    PLAN_TARGET = 'PLAN_TARGET'
    MANIFEST_REVIEW = 'MANIFEST_REVIEW'
    JOURNAL_FORMAT = 'JOURNAL_FORMAT'
    JOURNAL_IDENTITY = 'JOURNAL_IDENTITY'
    JOURNAL_STATE = 'JOURNAL_STATE'
    JOURNAL_INVENTORY = 'JOURNAL_INVENTORY'
    JOURNAL_DIAGNOSTIC = 'JOURNAL_DIAGNOSTIC'
    RECEIPT_FORMAT = 'RECEIPT_FORMAT'
    RECEIPT_IDENTITY = 'RECEIPT_IDENTITY'
    RECEIPT_OID_FORMAT = 'RECEIPT_OID_FORMAT'
    RECEIPT_FLAGS = 'RECEIPT_FLAGS'
    RECEIPT_DATABASE_MISMATCH = 'RECEIPT_DATABASE_MISMATCH'
    EVIDENCE_CHANGED = 'EVIDENCE_CHANGED'
    CREDENTIAL_PROMPT = 'CREDENTIAL_PROMPT'
    VALIDATION_UNAVAILABLE = 'VALIDATION_UNAVAILABLE'


class EvidenceSource(str, Enum):
    INPUT = 'INPUT'
    PLAN = 'PLAN'
    JOURNAL = 'JOURNAL'
    PRECOMMIT = 'PRECOMMIT'
    RECEIPT = 'RECEIPT'
    MANIFEST = 'MANIFEST'
    CREDENTIALS = 'CREDENTIALS'
    UNKNOWN = 'UNKNOWN'


_SOURCES = (EvidenceSource.PLAN, EvidenceSource.JOURNAL, EvidenceSource.PRECOMMIT, EvidenceSource.RECEIPT)


class EvidenceValidationError(IntegrationSafetyError):
    def __init__(self, code, source):
        super().__init__('Recovery evidence rejected; details suppressed.')
        self.preconnection_code = code
        self.evidence_source = source


def preconnection_diagnostic(error):
    if type(error) is not EvidenceValidationError:
        return None
    code = getattr(error, 'preconnection_code', None)
    source = getattr(error, 'evidence_source', None)
    return {'category': code.value if type(code) is PreconnectionCategory else 'VALIDATION_UNAVAILABLE',
            'source': source.value if type(source) is EvidenceSource else 'UNKNOWN'}


def _evidence_reject(code, source=EvidenceSource.INPUT):
    raise EvidenceValidationError(code, source) from None


def _reject():
    raise IntegrationSafetyError('Recovery inspection refused; details suppressed.') from None


def _oid(value):
    return type(value) is int and 16384 <= value < 2**32


def _json_object(pairs, source=EvidenceSource.UNKNOWN):
    result = {}
    for key, value in pairs:
        if key in result:
            _evidence_reject(PreconnectionCategory.DUPLICATE_JSON_KEY, source)
        result[key] = value
    return result


def _read_file(path, source=EvidenceSource.UNKNOWN):
    # File names are constructed from an approved schema, never supplied freely.
    # Reject symlinks/junctions in the path before opening; reads are bounded.
    try:
        for component in (path, *path.parents):
            info = component.lstat()
            if component.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                _evidence_reject(PreconnectionCategory.FILE_REPARSE, source)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 65536:
            _evidence_reject(PreconnectionCategory.FILE_TYPE_OR_SIZE, source)
        with path.open('rb') as stream:
            data = stream.read(65537)
        if not 1 <= len(data) <= 65536:
            _evidence_reject(PreconnectionCategory.FILE_TYPE_OR_SIZE, source)
    except FileNotFoundError:
        _evidence_reject(PreconnectionCategory.FILE_MISSING, source)
    except PermissionError:
        _evidence_reject(PreconnectionCategory.FILE_ACCESS_DENIED, source)
    except OSError:
        _evidence_reject(PreconnectionCategory.FILE_IO, source)
    return data


def _decode_file(data, source):
    try:
        # A UTF-8 BOM is a transport marker, not part of the JSON identity.
        text = data.decode('utf-8-sig')
    except UnicodeError:
        _evidence_reject(PreconnectionCategory.FILE_ENCODING, source)
    try:
        return json.loads(text, object_pairs_hook=lambda pairs: _json_object(pairs, source))
    except (ValueError, RecursionError):
        _evidence_reject(PreconnectionCategory.FILE_JSON, source)


def _receipt_oid(value, source):
    # jsonb_build_object serializes PostgreSQL oid values as decimal strings.
    # Only receipt OID fields accept that representation. Never coerce journal
    # fields, Booleans, floats, whitespace, signs, leading zeros or Unicode digits.
    if type(value) is str and re.fullmatch(r'[1-9][0-9]{0,9}', value):
        value = int(value)
    if not _oid(value):
        _evidence_reject(PreconnectionCategory.RECEIPT_OID_FORMAT, source)
    return value


@dataclass(frozen=True, repr=False)
class InspectionEvidence:
    schema: str
    namespace_oid: int
    owner_oid: int
    database_oid: int
    phase: str
    outcome: str
    diagnostic: object
    snapshots: tuple

    def assert_unchanged(self):
        for source, (path, data) in zip(_SOURCES, self.snapshots):
            if _read_file(path, source) != data:
                _evidence_reject(PreconnectionCategory.EVIDENCE_CHANGED, source)


def read_evidence(plan_path, namespace_oid, owner_oid, container_id):
    try:
        return _read_evidence(plan_path, namespace_oid, owner_oid, container_id)
    except EvidenceValidationError:
        raise
    except Exception:
        _evidence_reject(PreconnectionCategory.VALIDATION_UNAVAILABLE)


def _read_evidence(plan_path, namespace_oid, owner_oid, container_id):
    """Accept legacy/new journals without constructing RecoveryJournal or saving anything."""
    if (not _oid(namespace_oid) or not _oid(owner_oid) or type(container_id) is not str
            or not re.fullmatch(r'[0-9a-f]{64}', container_id)):
        _evidence_reject(PreconnectionCategory.ARGUMENTS)
    if not isinstance(plan_path, (str, Path)):
        _evidence_reject(PreconnectionCategory.EVIDENCE_PATH)
    path = Path(plan_path)
    schema = path.name.removesuffix('.plan.json')
    if path.name != schema + '.plan.json' or not SCHEMA_PATTERN.fullmatch(schema):
        _evidence_reject(PreconnectionCategory.EVIDENCE_PATH)  # Before any open.
    paths = (path, path.with_name(schema + '.json'),
        path.with_name(schema + '.precommit.json'), path.with_name(schema + '.provisioning.json'))
    snapshots = tuple((p, _read_file(p, source)) for p, source in zip(paths, _SOURCES))
    plan, journal, precommit, receipt = (
        _decode_file(data, source) for source, (_, data) in zip(_SOURCES, snapshots))
    if (type(plan) is not dict or set(plan) != {'version', 'schema', 'manifest_digest', 'target'}
            or type(plan['version']) is not int or plan['version'] != 1 or plan['schema'] != schema):
        _evidence_reject(PreconnectionCategory.PLAN_FORMAT, EvidenceSource.PLAN)
    if (type(plan['target']) is not dict or set(plan['target']) != set(TARGET)
            or any(type(plan['target'][key]) is not type(value) or plan['target'][key] != value
                   for key, value in TARGET.items())):
        _evidence_reject(PreconnectionCategory.PLAN_TARGET, EvidenceSource.PLAN)
    try:
        validate_plan(IntegrationPlan(schema, plan['manifest_digest']))
    except Exception:
        _evidence_reject(PreconnectionCategory.MANIFEST_REVIEW, EvidenceSource.MANIFEST)
    if type(journal) is not dict or set(journal) not in (_JOURNAL_KEYS, _JOURNAL_KEYS | {'diagnostic'}):
        _evidence_reject(PreconnectionCategory.JOURNAL_FORMAT, EvidenceSource.JOURNAL)
    if (journal['schema'] != schema or journal['database'] != 'dao_eternity_test'
            or journal['role'] != 'dao_test_user' or journal['namespace_oid'] != namespace_oid
            or type(journal['namespace_oid']) is not int or journal['owner_oid'] != owner_oid
            or type(journal['owner_oid']) is not int):
        _evidence_reject(PreconnectionCategory.JOURNAL_IDENTITY, EvidenceSource.JOURNAL)
    if (type(journal['phase']) is not str or journal['phase'] not in _PHASES
            or type(journal['outcome']) is not str or journal['outcome'] not in _OUTCOMES):
        _evidence_reject(PreconnectionCategory.JOURNAL_STATE, EvidenceSource.JOURNAL)
    if type(journal['table_oids']) is not list:
        _evidence_reject(PreconnectionCategory.JOURNAL_INVENTORY, EvidenceSource.JOURNAL)
    digest = journal['inventory_digest']
    if digest is not None and (type(digest) is not str or not re.fullmatch('[0-9a-f]{64}', digest)):
        _evidence_reject(PreconnectionCategory.JOURNAL_INVENTORY, EvidenceSource.JOURNAL)
    names, oids = set(), set()
    for item in journal['table_oids']:
        if (type(item) is not list or len(item) != 2 or type(item[0]) is not str
                or item[0] not in TABLE_NAMES or item[0] in names or not _oid(item[1]) or item[1] in oids):
            _evidence_reject(PreconnectionCategory.JOURNAL_INVENTORY, EvidenceSource.JOURNAL)
        names.add(item[0])
        oids.add(item[1])
    try:
        diagnostic = decode_setup_diagnostic(journal.get('diagnostic'))
    except Exception:
        _evidence_reject(PreconnectionCategory.JOURNAL_DIAGNOSTIC, EvidenceSource.JOURNAL)
    receipt_oids = []
    for record, phase, keys, source in ((precommit, 'commit_unknown', _RECEIPT_KEYS, EvidenceSource.PRECOMMIT),
            (receipt, 'provisioned', _RECEIPT_KEYS | _RECEIPT_FLAGS, EvidenceSource.RECEIPT)):
        if type(record) is not dict or set(record) != keys or record['phase'] != phase:
            _evidence_reject(PreconnectionCategory.RECEIPT_FORMAT, source)
        addresses = {key: _receipt_oid(record[key], source)
                     for key in ('namespace_oid', 'owner_oid', 'database_oid')}
        if (record['schema'] != schema or record['container_id'] != container_id
                or record['cluster_identifier'] != '7699864005918920747'
                or record['database'] != 'dao_eternity_test' or record['role'] != 'dao_test_user'
                or addresses['namespace_oid'] != namespace_oid or addresses['owner_oid'] != owner_oid):
            _evidence_reject(PreconnectionCategory.RECEIPT_IDENTITY, source)
        receipt_oids.append(addresses)
    if receipt_oids[0]['database_oid'] != receipt_oids[1]['database_oid']:
        _evidence_reject(PreconnectionCategory.RECEIPT_DATABASE_MISMATCH, EvidenceSource.RECEIPT)
    if any(receipt[flag] is not True for flag in _RECEIPT_FLAGS):
        _evidence_reject(PreconnectionCategory.RECEIPT_FLAGS, EvidenceSource.RECEIPT)
    return InspectionEvidence(schema, namespace_oid, owner_oid, receipt_oids[1]['database_oid'],
        journal['phase'], journal['outcome'], diagnostic, snapshots)


class _CatalogReader:
    """Only reviewed SELECTs and exact parameter shapes; no execute/write API."""
    def __init__(self, connection, evidence, trace=None):
        self.__connection, self.__evidence = connection, evidence
        self.__trace = trace

    async def fetchrow(self, sql, *args):
        e = self.__evidence
        allowed = {IDENTITY_SQL: (), SERVER_IDENTITY_SQL: (), PRIVILEGES_SQL: (e.namespace_oid,),
            NAMESPACE_SQL: (e.schema,), RECOVERY_OBSERVATION_SQL:
                (e.schema, e.namespace_oid, e.owner_oid, e.database_oid)}
        if sql not in allowed or args != allowed[sql]:
            if self.__trace is not None:
                self.__trace.stage = InspectionStage.QUERY_ALLOWLIST
                self.__trace.reject(InspectionCategory.SAFETY, 'query_allowed')
            _reject()
        if self.__trace is not None:
            stages = {IDENTITY_SQL: InspectionStage.SESSION_IDENTITY,
                SERVER_IDENTITY_SQL: InspectionStage.CLUSTER_IDENTITY,
                PRIVILEGES_SQL: InspectionStage.EFFECTIVE_PRIVILEGES,
                NAMESPACE_SQL: InspectionStage.SCHEMA_CATALOG,
                RECOVERY_OBSERVATION_SQL: InspectionStage.RECOVERY_CATALOG}
            if not (sql == IDENTITY_SQL and self.__trace.stage is InspectionStage.FINAL_READ_ONLY):
                self.__trace.stage = stages[sql]
        return await self.__connection.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        if sql != CATALOG_WRITES_SQL or args != (self.__evidence.namespace_oid,):
            if self.__trace is not None:
                self.__trace.stage = InspectionStage.QUERY_ALLOWLIST
                self.__trace.reject(InspectionCategory.SAFETY, 'query_allowed')
            _reject()
        if self.__trace is not None:
            self.__trace.stage = InspectionStage.CATALOG_WRITE_POLICY
        return await self.__connection.fetch(sql, *args)


@asynccontextmanager
async def _inspection_transaction(con, trace):
    """Observe the existing read-only driver transaction, with no retries."""
    trace.stage = InspectionStage.TRANSACTION_BEGIN
    try:
        transaction = con.transaction(readonly=True, isolation='repeatable_read')
        await transaction.__aenter__()
    except BaseException as error:
        trace.transaction_exit = 'BEGIN_RAISED'
        trace.capture(error)
        raise
    try:
        yield
    except BaseException as original:
        trace.capture(original)  # Preserve the first failure before cleanup.
        trace.stage = InspectionStage.TRANSACTION_ROLLBACK
        try:
            await transaction.__aexit__(type(original), original, original.__traceback__)
        except BaseException as error:
            trace.transaction_exit = 'ROLLBACK_RAISED'
            trace.secondary_failure(error)
            if isinstance(error, asyncio.CancelledError):
                raise error from None
        else:
            trace.transaction_exit = 'ROLLBACK_ACKNOWLEDGED'
        raise original from None
    else:
        trace.stage = InspectionStage.TRANSACTION_COMMIT
        try:
            await transaction.__aexit__(None, None, None)
        except BaseException as error:
            trace.transaction_exit = 'COMMIT_RAISED'
            trace.capture(error)
            raise
        trace.transaction_exit = 'COMMIT_ACKNOWLEDGED'


async def inspect_recovery(config, plan_path, namespace_oid, owner_oid, container_id, *,
                           allow_read_only_connection=False):
    """One observation, zero writes/retries. No connection/evidence reads without opt-in."""
    return await _inspect_evidence(config,
        lambda: read_evidence(plan_path, namespace_oid, owner_oid, container_id),
        namespace_oid, owner_oid, allow_read_only_connection=allow_read_only_connection)


async def _inspect_evidence(config, evidence_reader, namespace_oid, owner_oid, *,
                            allow_read_only_connection=False):
    """Shared fixed-query engine; readers validate their own archival formats.

    The fresh workflow has different receipt names and seals. Its separate reader
    supplies validated immutable evidence, without translating or saving journals.
    """
    trace = InspectionTrace()
    con = None
    failure = None
    report = None
    try:
        if allow_read_only_connection is not True:
            trace.reject(InspectionCategory.CONFIGURATION)
        trace.stage = InspectionStage.CONFIGURATION
        require_target(config)
        trace.stage = InspectionStage.EVIDENCE_REVALIDATION
        evidence = evidence_reader()
        expected = ExpectedServerIdentity('172.17.0.2', 5432, 7699864005918920747)
        trace.stage = InspectionStage.CONNECTION
        con = await _connect(config)
        # Startup defaults are read-only too; never enable writes, even temporarily.
        async with _inspection_transaction(con, trace):
            reader = _CatalogReader(con, evidence, trace)
            identity = await verify_connection(reader, config, namespace_oid=namespace_oid,
                                               expected_server=expected)
            trace.stage = InspectionStage.PREPARED_ROLE_POLICY
            if identity['can_create_schema'] is not False:
                trace.reject(InspectionCategory.PRIVILEGES, 'prepared_database_create_absent')
            if identity['version'] != 180006:
                trace.reject(InspectionCategory.VERSION, 'server_version_verified')
            if identity['role_oid'] != owner_oid:
                trace.reject(InspectionCategory.IDENTITY, 'role_oid_verified')
            namespace = await reader.fetchrow(NAMESPACE_SQL, evidence.schema)
            trace.stage = InspectionStage.SCHEMA_VERIFICATION
            if not namespace:
                trace.reject(InspectionCategory.SCHEMA_VERIFICATION, 'namespace_present')
            # asyncpg.Record iterates over values, unlike the dictionary doubles.
            if (len(namespace) != 3 or set(namespace.keys()) != {'oid', 'owner', 'acl_safe'}
                    or type(namespace['oid']) is not int or type(namespace['owner']) is not int
                    or type(namespace['acl_safe']) is not bool):
                trace.reject(InspectionCategory.CATALOG_COMPATIBILITY, 'metadata_shape_valid')
            failed = tuple(key for key, passed in (
                ('namespace_oid_verified', namespace['oid'] == namespace_oid),
                ('namespace_owner_verified', namespace['owner'] == owner_oid),
                ('namespace_acl_private', namespace['acl_safe'] is True)) if not passed)
            if failed:
                trace.reject(InspectionCategory.SCHEMA_VERIFICATION, *failed)
            row = await reader.fetchrow(RECOVERY_OBSERVATION_SQL, evidence.schema,
                                       namespace_oid, owner_oid, evidence.database_oid)
            trace.stage = InspectionStage.RECOVERY_METADATA
            if not row or len(row) != len(_OBSERVATION_FLAGS) or set(row.keys()) != _OBSERVATION_FLAGS:
                trace.reject(InspectionCategory.CATALOG_COMPATIBILITY, 'metadata_shape_valid')
            malformed = tuple(key for key in sorted(_OBSERVATION_FLAGS) if type(row[key]) is not bool)
            if malformed:
                if malformed == ('no_other_guard_sessions',):
                    trace.stage = InspectionStage.SESSION_INSPECTION
                trace.reject(InspectionCategory.CATALOG_COMPATIBILITY, *malformed)
            trace.stage = InspectionStage.DATABASE_IDENTITY
            if row['database_oid_verified'] is not True:
                trace.reject(InspectionCategory.IDENTITY, 'database_oid_verified')
            trace.stage = InspectionStage.RECOVERY_SCHEMA_POLICY
            failed = tuple(key for key in ('namespace_exists', 'namespace_identity_verified', 'acl_private')
                           if row[key] is not True)
            if failed:
                trace.reject(InspectionCategory.SCHEMA_VERIFICATION, *failed)
            trace.stage = InspectionStage.SCHEMA_ACCESS_POLICY
            failed = tuple(key for key in ('schema_usage', 'schema_create') if row[key] is not True)
            if failed:
                trace.reject(InspectionCategory.PRIVILEGES, *failed)
            # Recheck the session defaults before leaving the read-only transaction.
            trace.stage = InspectionStage.FINAL_READ_ONLY
            final = await reader.fetchrow(IDENTITY_SQL)
            if not final or 'read_only' not in final or 'default_read_only' not in final:
                trace.reject(InspectionCategory.CATALOG_COMPATIBILITY, 'metadata_shape_valid')
            failed = tuple(key for key in ('read_only', 'default_read_only') if final[key] != 'on')
            if failed:
                trace.reject(InspectionCategory.READ_ONLY, *failed)
        trace.stage = InspectionStage.EVIDENCE_RECHECK
        evidence.assert_unchanged()
        trace.stage = InspectionStage.REPORT
        empty = all(row[key] for key in ('no_relations', 'no_namespace_dependencies', 'no_extension_placement'))
        report = {'status': 'EMPTY_SCHEMA_OBSERVED' if empty and row['no_other_guard_sessions']
                else 'OBJECTS_OR_SESSIONS_PRESENT',
            'read_only': True, 'cluster_identity_verified': True,
            'evidence_unchanged': True, 'rollback_verification': 'UNKNOWN',
            'journal_phase': evidence.phase, 'journal_outcome': evidence.outcome,
            'historical_diagnostic': safe_setup_diagnostic(evidence.diagnostic),
            'flags': {key: row[key] for key in sorted(_OBSERVATION_FLAGS)}}
    except BaseException as error:
        failure = error
        trace.capture(error)
        if getattr(error, 'termination_unconfirmed', False):
            trace.connection_cleanup = 'UNCONFIRMED'
    finally:
        if con is not None:
            trace.stage = InspectionStage.CONNECTION_CLEANUP
            cancellation = failure if isinstance(failure, asyncio.CancelledError) else None
            task = asyncio.create_task(_close_connection(con, cancellation=cancellation))
            try:
                trace.connection_cleanup = await _terminal_result(task)
            except BaseException as error:
                trace.connection_cleanup = 'UNCONFIRMED'
                if task.done() and not task.cancelled():
                    try:
                        trace.connection_cleanup = task.result()
                    except BaseException:
                        pass
                if failure is None:
                    trace.capture(error)
                    failure = error
                else:
                    trace.secondary_failure(error)
                    if isinstance(error, asyncio.CancelledError) and not isinstance(failure, asyncio.CancelledError):
                        failure = error
    if failure is not None:
        diagnostic = trace.diagnostic()
        if isinstance(failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, EvidenceValidationError)):
            failure.inspection_diagnostic = diagnostic
            raise failure from None
        raise InspectionFailure(diagnostic) from None
    report['connection_cleanup'] = trace.connection_cleanup
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Disabled-by-default read-only recovery inspector')
    parser.add_argument('action', choices=('inspect',), nargs='?')
    parser.add_argument('--allow-read-only-connection', action='store_true')
    parser.add_argument('--plan')
    parser.add_argument('--namespace-oid', type=int)
    parser.add_argument('--owner-oid', type=int)
    parser.add_argument('--container-id')
    args = parser.parse_args(argv)
    if args.action is None or not args.allow_read_only_connection:
        print('RECOVERY INSPECTION DISABLED [CONNECTION_DISABLED]')
        return 2
    post_prompt = None
    try:
        # Validate files before prompting; this reader never constructs a journal.
        read_evidence(args.plan, args.namespace_oid, args.owner_oid, args.container_id)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                password = getpass.getpass('Read-only recovery password for dao_test_user: ')
        except Exception:
            _evidence_reject(PreconnectionCategory.CREDENTIAL_PROMPT, EvidenceSource.CREDENTIALS)
        post_prompt = InspectionTrace(InspectionStage.CONFIGURATION)
        config = TestDatabaseConfig('127.0.0.1', 5433, 'dao_eternity_test', 'dao_test_user', password)
        post_prompt.stage = InspectionStage.EVENT_LOOP
        result = asyncio.run(inspect_recovery(config, args.plan, args.namespace_oid,
            args.owner_oid, args.container_id, allow_read_only_connection=True))
        post_prompt.stage = InspectionStage.REPORT
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        print('RECOVERY INSPECTION CANCELLED [CANCELLED]')
        diagnostic = inspection_diagnostic(error)
        if diagnostic is not None:
            print(json.dumps({'inspection_diagnostic': diagnostic}, sort_keys=True))
        return 130
    except Exception as error:
        evidence_diagnostic = preconnection_diagnostic(error)
        diagnostic = inspection_diagnostic(error)
        if diagnostic is None and post_prompt is not None:
            post_prompt.capture(error)
            error.inspection_diagnostic = post_prompt.diagnostic()
            diagnostic = inspection_diagnostic(error)
        if evidence_diagnostic is not None:
            print('RECOVERY INSPECTION REJECTED [' + evidence_diagnostic['category'] + '] source=' + evidence_diagnostic['source'])
        else:
            category = diagnostic['category'] if diagnostic is not None else failure_category(error).value
            print('RECOVERY INSPECTION REJECTED [' + category + ']')
        if diagnostic is not None:
            print(json.dumps({'inspection_diagnostic': diagnostic}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
