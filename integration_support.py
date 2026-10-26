"""Fail-closed infrastructure only. Importing opens no connections or files.

preflight_catalog returns a report, never a connection. Write-capable
operations are separate, opt-in and require a persistent local recovery journal.
Gameplay integration cases and their explicit runner live in separate modules.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
import asyncio
import hashlib
import ipaddress
import json
import os
import re
from uuid import uuid4

import asyncpg

from integration_catalog import (
    IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL, PRIVILEGE_FLAGS, NAMESPACE_SQL,
    PATH_SQL, INVENTORY_SQL, CATALOG_WRITES_SQL, EMPTY_NAMESPACE_SQL,
    ROLE_PRIVILEGE_FLAGS, CATALOG_WRITE_METADATA_FLAGS, CATALOG_WRITE_DIAGNOSTIC_FLAGS,
)
from integration_manifest import reviewed_table_definitions, TABLE_SHAPES
from integration_recovery import RecoveryJournal, RecoveryRecord
from inventory_fetch_diagnostics import classify_fetch_failure
from integration_diagnostics import (
    SetupDiagnostic, SetupStage, SetupCategory, safe_setup_diagnostic, setup_category,
)


TEST_DATABASE = 'dao_eternity_test'
TEST_ROLE = 'dao_test_user'
CONFIG_KEYS = ('DAO_TEST_HOST', 'DAO_TEST_PORT', 'DAO_TEST_DATABASE',
               'DAO_TEST_USER', 'DAO_TEST_PASSWORD')
SCHEMA_PATTERN = re.compile(r'dao_it_[0-9a-f]{32}\Z')


class FailureCategory(str, Enum):
    """Reviewed diagnostic codes; never interpolate driver or catalog text."""
    CONFIGURATION = 'CONFIGURATION'
    AUTHENTICATION = 'AUTHENTICATION'
    CONNECTION = 'CONNECTION'
    IDENTITY = 'IDENTITY'
    READ_ONLY = 'READ_ONLY'
    PRIVILEGES = 'PRIVILEGES'
    CATALOG_COMPATIBILITY = 'CATALOG_COMPATIBILITY'
    VERSION = 'VERSION'
    CONNECTION_CLEANUP = 'CONNECTION_CLEANUP'
    SAFETY = 'SAFETY'


class PrivilegeStage(str, Enum):
    """Fixed labels for the existing rejection boundaries, never server text."""
    CONNECTION_STARTUP = 'CONNECTION_STARTUP'
    SESSION_METADATA = 'SESSION_METADATA'
    ROLE_POLICY = 'ROLE_POLICY'
    SERVER_IDENTITY = 'SERVER_IDENTITY'
    EFFECTIVE_PRIVILEGES_QUERY = 'EFFECTIVE_PRIVILEGES_QUERY'
    EFFECTIVE_PRIVILEGES_POLICY = 'EFFECTIVE_PRIVILEGES_POLICY'
    CATALOG_WRITES_QUERY = 'CATALOG_WRITES_QUERY'
    CATALOG_WRITES_POLICY = 'CATALOG_WRITES_POLICY'


_PRIVILEGE_DIAGNOSTIC_FLAGS = (
    *ROLE_PRIVILEGE_FLAGS, *PRIVILEGE_FLAGS, *CATALOG_WRITE_DIAGNOSTIC_FLAGS,
)


def _safe_privilege_flags(flags):
    # Iterate our labels, never copy caller/catalog keys or coerce unknown values.
    if type(flags) is not dict:
        return {}
    return {key: flags[key] for key in _PRIVILEGE_DIAGNOSTIC_FLAGS
            if type(flags.get(key)) is bool}


class IntegrationSafetyError(RuntimeError):
    def __init__(self, message, *, recovery=None, category=FailureCategory.SAFETY,
                 privilege_stage=None, privilege_flags=None, setup_diagnostic=None):
        super().__init__(message)
        self.recovery = recovery
        self.category = category if type(category) is FailureCategory else FailureCategory.SAFETY
        self.privilege_stage = privilege_stage if type(privilege_stage) is PrivilegeStage else None
        self.privilege_flags = _safe_privilege_flags(privilege_flags)
        self.setup_diagnostic = setup_diagnostic


def setup_failure_diagnostic(error):
    value = getattr(error, 'setup_diagnostic', None)
    return safe_setup_diagnostic(value) if type(value) is SetupDiagnostic else None


def failure_category(error):
    """Return only an allowlisted code, even for an uncategorized/mutated error."""
    category = getattr(error, 'category', None)
    return category if type(category) is FailureCategory else FailureCategory.SAFETY


def privilege_diagnostic(error):
    """Optional safe output from a rejected run; no queries, rows or driver text.

    Revalidate at output time as exception attributes may have been mutated.
    Empty flags mean the metadata query failed before any Booleans were known.
    """
    if failure_category(error) is not FailureCategory.PRIVILEGES:
        return None
    stage = getattr(error, 'privilege_stage', None)
    if type(stage) is not PrivilegeStage:
        return {'stage': 'UNAVAILABLE', 'flags': {}}
    return {'stage': stage.value,
            'flags': _safe_privilege_flags(getattr(error, 'privilege_flags', None))}


def _driver_failure_category(error, *, fallback):
    # Inspect class metadata, never message/detail/hint/query fields. A category
    # identifies the rejected stage, not a diagnosis of server configuration.
    if isinstance(error, asyncpg.PostgresError):
        state = getattr(type(error), 'sqlstate', None)
        if isinstance(state, str):
            if state.startswith('28'):
                return FailureCategory.AUTHENTICATION
            if state == '42501':
                return FailureCategory.PRIVILEGES
            if state == '3D000':
                return FailureCategory.CONFIGURATION
            if state.startswith('08') or state in (
                    '57P01', '57P02', '57P03', '57014', '53300', '53400'):
                return FailureCategory.CONNECTION
    if isinstance(error, (OSError, TimeoutError, asyncpg.InterfaceError)):
        return FailureCategory.CONNECTION
    return fallback


@dataclass(frozen=True, repr=False)
class TestDatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    def __post_init__(self):
        if self.host not in ('localhost', '127.0.0.1'):
            raise IntegrationSafetyError('DAO_TEST_HOST must be localhost or 127.0.0.1.',
                                         category=FailureCategory.CONFIGURATION)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise IntegrationSafetyError('DAO_TEST_PORT must be an explicit valid TCP port.',
                                         category=FailureCategory.CONFIGURATION)
        if self.database != TEST_DATABASE or self.user != TEST_ROLE:
            raise IntegrationSafetyError('Only the dedicated test database and role are allowed.',
                                         category=FailureCategory.CONFIGURATION)
        if not isinstance(self.password, str) or not self.password:
            raise IntegrationSafetyError('DAO_TEST_PASSWORD must be explicitly supplied.',
                                         category=FailureCategory.CONFIGURATION)

    def __repr__(self):
        return 'TestDatabaseConfig(<redacted>)'

    @classmethod
    def from_environment(cls, environ=None):
        source = os.environ if environ is None else environ
        if any(not isinstance(source.get(key), str) or not source[key] for key in CONFIG_KEYS):
            raise IntegrationSafetyError('All five DAO_TEST_* connection variables are required.',
                                         category=FailureCategory.CONFIGURATION)
        port = source['DAO_TEST_PORT']
        if not re.fullmatch(r'[0-9]{1,5}', port):
            raise IntegrationSafetyError('DAO_TEST_PORT must contain only decimal digits.',
                                         category=FailureCategory.CONFIGURATION)
        return cls(source['DAO_TEST_HOST'], int(port), source['DAO_TEST_DATABASE'],
                   source['DAO_TEST_USER'], source['DAO_TEST_PASSWORD'])


@dataclass(frozen=True)
class ExpectedServerIdentity:
    """Independently reviewed Docker listener and cluster; never learned via SQL.

    Only the catalog-only API accepts this policy. Client transport still uses
    TestDatabaseConfig's pinned loopback destination and explicit client port.
    """
    address: str
    port: int
    system_identifier: int = field(repr=False)

    def __post_init__(self):
        address = None
        if type(self.address) is str:
            try:
                address = ipaddress.IPv4Address(self.address)
            except ipaddress.AddressValueError:
                pass
        private_networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
        if (address is None or str(address) != self.address or
                not (self.address == '127.0.0.1' or any(
                    address in ipaddress.IPv4Network(network) for network in private_networks))):
            raise IntegrationSafetyError('A reviewed loopback or private IPv4 listener is required.',
                                         category=FailureCategory.CONFIGURATION)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise IntegrationSafetyError('An explicit valid internal listener port is required.',
                                         category=FailureCategory.CONFIGURATION)
        if type(self.system_identifier) is not int or not 0 < self.system_identifier < 2**63:
            raise IntegrationSafetyError('An independently pinned cluster identifier is required.',
                                         category=FailureCategory.CONFIGURATION)


def _require_expected_server(expected_server):
    if expected_server is not None:
        if type(expected_server) is not ExpectedServerIdentity:
            raise IntegrationSafetyError('An explicit ExpectedServerIdentity is required.',
                                         category=FailureCategory.CONFIGURATION)
        expected_server.__post_init__()


@dataclass(frozen=True)
class PreflightReport:
    """host/port describe the client destination; server_port is the listener."""
    database: str
    role: str
    host: str
    port: int
    read_only: bool
    effective_privileges_verified: bool
    can_create_schema: bool
    server_major: int
    server_port: int
    cluster_identity_verified: bool


def _require_config(config):
    # Exact type prevents a subclass overriding validation/accessors.
    if type(config) is not TestDatabaseConfig:
        raise IntegrationSafetyError('An explicit TestDatabaseConfig is required.',
                                     category=FailureCategory.CONFIGURATION)
    config.__post_init__()


def _terminate(con):
    try:
        con.terminate()
        return True
    except Exception:
        return False


async def _close_connection(con, *, cancellation=None):
    try:
        await con.close(timeout=5)
        return 'ACKNOWLEDGED'
    except asyncio.CancelledError as exc:
        exc = cancellation if cancellation is not None else exc
        if not _terminate(con):
            exc.termination_unconfirmed = True
            exc.add_note('Connection termination was not confirmed; investigate the test session.')
        raise exc from None
    except Exception:
        if not _terminate(con):
            if cancellation is not None:
                cancellation.termination_unconfirmed = True
                cancellation.add_note('Connection closure was not confirmed; investigate the test session.')
                raise cancellation from None
            error = IntegrationSafetyError('Connection closure could not be confirmed.',
                                           category=FailureCategory.CONNECTION_CLEANUP)
            error.termination_unconfirmed = True
            raise error from None
        return 'TERMINATED'


async def _terminal_result(task):
    """Drain an owned task despite repeated caller cancellation, then re-raise it."""
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.done() and task.cancelled():
                break
            cancellation = exc
        except Exception:
            break
    if cancellation is not None:
        # Retrieve any worker error; it remains available through the task and
        # run recovery record. Never replace the caller's cancellation with it.
        try:
            task.result()
        except BaseException:
            pass
        raise cancellation
    return task.result()


async def _connect(config):
    """Private transport: explicit fields only; returns a read-only session."""
    _require_config(config)
    pending = None
    try:
        pending = asyncio.create_task(asyncpg.connect(
            host='127.0.0.1', port=config.port, database=config.database,
            user=config.user, password=config.password, ssl=False,
            timeout=5, command_timeout=10,
            server_settings={
                'application_name': 'dao_integration_guard',
                'search_path': 'pg_catalog',
                'default_transaction_read_only': 'on',
                'default_transaction_isolation': 'read committed',
                'statement_timeout': '10000', 'lock_timeout': '3000',
                'idle_in_transaction_session_timeout': '15000',
            }))
        return await asyncio.shield(pending)
    except asyncio.CancelledError as exc:
        if pending is None:
            raise
        pending.cancel()
        termination_unconfirmed = False

        async def drain():
            nonlocal termination_unconfirmed
            try:
                con = await pending
            except BaseException:
                return
            termination_unconfirmed = not _terminate(con)
        try:
            await _terminal_result(asyncio.create_task(drain()))
        except asyncio.CancelledError:
            pass  # Preserve the original cancellation after draining late results.
        if termination_unconfirmed:
            exc.termination_unconfirmed = True
            exc.add_note('Late connection termination was not confirmed; investigate the test session.')
        raise exc
    except Exception as exc:
        raise IntegrationSafetyError(
            'Test connection failed; details suppressed.',
            category=_driver_failure_category(exc, fallback=FailureCategory.CONNECTION),
            privilege_stage=PrivilegeStage.CONNECTION_STARTUP) from None


async def verify_connection(con, config, *, namespace_oid=None, expected_server=None):
    """Private-use catalog verification. Returns identity metadata, never a session."""
    _require_config(config)
    _require_expected_server(expected_server)
    stage = PrivilegeStage.SESSION_METADATA
    try:
        row = await con.fetchrow(IDENTITY_SQL)
        if not row or (row['database_name'], row['role_name'], row['session_role']) != (
                TEST_DATABASE, TEST_ROLE, TEST_ROLE):
            raise IntegrationSafetyError('Connected database or role identity was rejected.',
                                         category=FailureCategory.IDENTITY)
        # inet_server_* describes the listener inside Docker, not its published
        # client destination. The default policy is unchanged; forwarding needs
        # both an explicit endpoint pin and the independent cluster pin below.
        address = '127.0.0.1' if expected_server is None else expected_server.address
        port = config.port if expected_server is None else expected_server.port
        if (type(row['server_address']) is not str or row['server_address'] != address
                or type(row['server_port']) is not int or row['server_port'] != port):
            raise IntegrationSafetyError('Connected server address or port was rejected.',
                                         category=FailureCategory.IDENTITY)
        if row['read_only'] != 'on' or row['default_read_only'] != 'on':
            raise IntegrationSafetyError('Read-only transaction and session defaults are required.',
                                         category=FailureCategory.READ_ONLY)
        if row['in_recovery'] is not False or row['temp_schema'] != 0:
            raise IntegrationSafetyError('Primary session without temporary objects required.',
                                         category=FailureCategory.IDENTITY)
        if type(row['version']) is not int or not 150000 <= row['version'] < 190000:
            raise IntegrationSafetyError('Catalog layout is not approved for this server version.',
                                         category=FailureCategory.VERSION)
        if any(type(row[key]) is not bool for key in (
                'can_login', 'elevated', 'other_memberships', 'enabled_event_triggers')):
            raise IntegrationSafetyError('Role metadata could not be established.',
                                         category=FailureCategory.CATALOG_COMPATIBILITY)
        if (row['can_login'] is not True or row['elevated'] is not False
                or row['other_memberships'] is not False or row['enabled_event_triggers'] is not False):
            raise IntegrationSafetyError('Role privileges, membership or event triggers were rejected.',
                                         category=FailureCategory.PRIVILEGES,
                                         privilege_stage=PrivilegeStage.ROLE_POLICY,
                                         privilege_flags={key: row[key] for key in ROLE_PRIVILEGE_FLAGS})
        if type(row['can_create_schema']) is not bool or type(row['role_oid']) is not int:
            raise IntegrationSafetyError('Identity metadata could not be established.',
                                         category=FailureCategory.CATALOG_COMPATIBILITY)
        if expected_server is not None:
            stage = PrivilegeStage.SERVER_IDENTITY
            server = await con.fetchrow(SERVER_IDENTITY_SQL)
            if (not server or type(server.get('system_identifier')) is not int
                    or server['system_identifier'] != expected_server.system_identifier):
                raise IntegrationSafetyError('Connected cluster identity was rejected.',
                                             category=FailureCategory.IDENTITY)
        stage = PrivilegeStage.EFFECTIVE_PRIVILEGES_QUERY
        privileges = await con.fetchrow(PRIVILEGES_SQL, namespace_oid)
        if not privileges or any(type(privileges.get(key)) is not bool for key in PRIVILEGE_FLAGS):
            raise IntegrationSafetyError('Privilege metadata could not be established.',
                                         category=FailureCategory.CATALOG_COMPATIBILITY)
        if any(privileges[key] for key in PRIVILEGE_FLAGS if key != 'system_catalog_writes'):
            raise IntegrationSafetyError('Effective privileges/default ACL policy was not satisfied.',
                                         category=FailureCategory.PRIVILEGES,
                                         privilege_stage=PrivilegeStage.EFFECTIVE_PRIVILEGES_POLICY,
                                         privilege_flags={key: privileges[key] for key in PRIVILEGE_FLAGS})
        if privileges['system_catalog_writes']:
            stage = PrivilegeStage.CATALOG_WRITES_QUERY
            writes = await con.fetch(CATALOG_WRITES_SQL, namespace_oid)
            if len(writes) != 1 or not _standard_pg_settings_update(writes[0]):
                raise IntegrationSafetyError('Catalog modification privileges were rejected.',
                                             category=FailureCategory.PRIVILEGES,
                                             privilege_stage=PrivilegeStage.CATALOG_WRITES_POLICY,
                                             privilege_flags=_catalog_write_diagnostic(writes))
        return dict(row)
    except asyncio.CancelledError:
        raise
    except IntegrationSafetyError:
        raise
    except Exception as exc:
        raise IntegrationSafetyError(
            'Catalog verification failed; details suppressed.',
            category=_driver_failure_category(
                exc, fallback=FailureCategory.CATALOG_COMPATIBILITY),
            privilege_stage=stage) from None


def _standard_pg_settings_update(row):
    """Allow the built-in view's ordinary PUBLIC UPDATE, never arbitrary grants."""
    expected = dict(schema='pg_catalog', name='pg_settings', kind='v',
                    is_pg_settings=True, owned=False, other_table_access=False,
                    other_column_access=False, table_update=True,
                    update_acl_safe=True, explicit_column_update=False)
    return (type(row.get('oid')) is int and 0 < row['oid'] < 16384
            and all(type(row.get(key)) is type(value) and row[key] == value
                    for key, value in expected.items()))


def _catalog_write_diagnostic(rows):
    """Summarize a failed existing predicate, without returning object identities."""
    flags = {'single_catalog_write_candidate': len(rows) == 1,
             'standard_pg_settings_update': False}
    if len(rows) == 1:
        row = rows[0]
        flags.update({key: row.get(key) for key in CATALOG_WRITE_METADATA_FLAGS
                      if type(row.get(key)) is bool})
        flags.update(
            builtin_catalog_oid=type(row.get('oid')) is int and 0 < row['oid'] < 16384,
            pg_catalog_schema=type(row.get('schema')) is str and row['schema'] == 'pg_catalog',
            pg_settings_name=type(row.get('name')) is str and row['name'] == 'pg_settings',
            view_kind=type(row.get('kind')) is str and row['kind'] == 'v')
    return flags


async def preflight_catalog(config, *, allow_connection=False, expected_server=None):
    """Catalog-only API: fixed SELECTs, a sanitized report, and mandatory close."""
    if allow_connection is not True:
        raise IntegrationSafetyError('Database connections are disabled without explicit opt-in.',
                                     category=FailureCategory.CONFIGURATION)
    _require_expected_server(expected_server)
    con = await _connect(config)
    cancellation = None
    try:
        row = await verify_connection(con, config, expected_server=expected_server)
        return PreflightReport(TEST_DATABASE, TEST_ROLE, '127.0.0.1', config.port,
                               True, True, row['can_create_schema'], row['version'] // 10000,
                               row['server_port'], expected_server is not None)
    except asyncio.CancelledError as exc:
        cancellation = exc
        raise
    finally:
        await _close_connection(con, cancellation=cancellation)


def _table_definitions():
    try:
        return reviewed_table_definitions()
    except Exception:
        raise IntegrationSafetyError('DDL manifest/source drift; explicit review required.') from None


def _fingerprint(rows):
    fields = ('kind', 'classid', 'oid', 'subid', 'name', 'parent', 'owner', 'definition')
    return tuple(sorted(tuple(row[key] for key in fields) for row in rows))


def _inventory_oid(value):
    """Decode only canonical uint32 OIDs nested in PostgreSQL JSON metadata.

    Native asyncpg OID columns are integers; jsonb_build_object emits OIDs as
    decimal strings. Normalize comparisons only, preserving raw fingerprints.
    """
    if type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,9}', value):
        value = int(value)
    if type(value) is not int or not 0 <= value < 2**32:
        raise IntegrationSafetyError('Inventory OID metadata was rejected.')
    return value


class IsolatedTestDatabase:
    """Separate opt-in write-capable API with persistent recovery evidence.

    Callers require explicit write opt-in and an existing recovery directory.
    A closing state is established without yielding; all close callers join one
    worker. Nothing here is a sandbox against a malicious DBA or arbitrary SQL.
    """
    def __init__(self, config):
        self.config = config
        self.schema = 'dao_it_' + uuid4().hex
        self.recovery = RecoveryRecord(self.schema)
        self._journal = None
        self._control = None
        self._namespace_oid = None
        self._owner = None
        self._version = None
        self._tables = {}
        self._inventory = ()
        self._state = 'new'
        self._active = 0
        self._slots = asyncio.Semaphore(6)
        self._close_task = None
        self._control_dispose_task = None
        self._expected_server = None
        self._prepared_namespace = False
        self._setup_diagnostic = None
        self._setup_active = False
        self._journal_writable = True

    async def _verify(self, con, *, namespace_oid=None):
        kwargs = {'namespace_oid': namespace_oid}
        if self._expected_server is not None:
            kwargs['expected_server'] = self._expected_server
        row = await verify_connection(con, self.config, **kwargs)
        if self._prepared_namespace and row['can_create_schema'] is not False:
            raise IntegrationSafetyError('Prepared-schema runs require database CREATE to remain absent.')
        if self._prepared_namespace and row['version'] // 10000 != 18:
            raise IntegrationSafetyError('Prepared-schema runs require PostgreSQL 18.')
        return row

    @classmethod
    async def adopt_empty(cls, config, schema, namespace_oid, *, expected_server,
                          allow_database_writes=False, recovery_directory=None):
        """Adopt exactly one independently provisioned empty namespace; no grants.

        This path never creates a schema and never requires database CREATE.
        Only a fully verified ready run can later remove its manifest objects.
        """
        if allow_database_writes is not True:
            raise IntegrationSafetyError('Database writes are disabled without explicit opt-in.')
        _require_config(config)
        if type(expected_server) is not ExpectedServerIdentity:
            raise IntegrationSafetyError('A complete independently pinned server is required.')
        _require_expected_server(expected_server)
        if (type(schema) is not str or not SCHEMA_PATTERN.fullmatch(schema)
                or type(namespace_oid) is not int or not 16384 <= namespace_oid < 2**32):
            raise IntegrationSafetyError('An exact prepared namespace name and independent OID are required.')
        definitions = _table_definitions()
        db = cls(config)
        db.schema = schema
        db.recovery = RecoveryRecord(schema)
        db._namespace_oid = namespace_oid
        db._expected_server = expected_server
        db._prepared_namespace = True
        db._setup_active = True
        db._setup_diagnostic = SetupDiagnostic(stage=SetupStage.JOURNAL_PERSISTENCE)
        cancellation = None
        failure = None
        try:
            db._journal = RecoveryJournal(recovery_directory, db.recovery)
            db._setup_stage(SetupStage.CONNECTION)
            db._record(phase='connecting_prepared_namespace', namespace_oid=namespace_oid)
            db._control = await _connect(config)
            db._setup_stage(SetupStage.IDENTITY_VERIFICATION)
            db._record(phase='verifying_prepared_namespace')
            identity = await db._verify(db._control, namespace_oid=namespace_oid)
            db._owner, db._version = identity['role_oid'], identity['version']
            db._record(owner_oid=db._owner)
            db._setup_stage(SetupStage.NAMESPACE_VERIFICATION)
            await db._check_namespace(db._control)
            await db._require_empty_namespace()
            db._setup_stage(SetupStage.ENABLE_WRITES)
            await db._control.execute('SET default_transaction_read_only = off')
            async with db._setup_transaction():
                # Recheck after entering the setup transaction. Concurrent setup
                # or a changed namespace must never lead to reuse of user data.
                db._setup_stage(SetupStage.NAMESPACE_VERIFICATION)
                await db._check_namespace(db._control)
                await db._require_empty_namespace()
                db._record(phase='creating_tables', outcome='rollback_unverified')
                digest = await db._setup_tables(definitions)
                db._record(inventory_digest=digest, table_oids=tuple(sorted(db._tables.items())),
                           phase='committing_setup', outcome='commit_unknown')
            db._record(phase='setup_committed', outcome='committed')
            db._setup_stage(SetupStage.RESTORE_READ_ONLY)
            await db._control.execute('SET default_transaction_read_only = on')
            db._record(phase='ready')
            db._state = 'ready'
            db._setup_active = False
            return db
        except asyncio.CancelledError as exc:
            cancellation = exc
            db._state = 'failed'
            db._capture_setup_failure(exc)
            if getattr(exc, 'termination_unconfirmed', False):
                db._record_unconfirmed_close()
            db._annotate_cancel(exc)
            raise
        except Exception as exc:
            db._state = 'failed'
            db._capture_setup_failure(exc)
            failure = IntegrationSafetyError('Prepared isolation setup failed; inspect recovery metadata.',
                recovery=db.recovery, setup_diagnostic=db._setup_diagnostic)
            raise failure from None
        finally:
            if db._state != 'ready' and db._control is not None:
                await db._dispose_control(cancellation=cancellation)
            if failure is not None:
                failure.recovery, failure.setup_diagnostic = db.recovery, db._setup_diagnostic
            if cancellation is not None:
                cancellation.recovery, cancellation.setup_diagnostic = db.recovery, db._setup_diagnostic

    async def _require_empty_namespace(self):
        row = await self._control.fetchrow(EMPTY_NAMESPACE_SQL, self._namespace_oid)
        if not row or row.get('empty_namespace') is not True:
            raise IntegrationSafetyError('Prepared namespace is not proven empty; refusing reuse.')

    def _record(self, **changes):
        # Retain the latest in-memory metadata even when a disk update fails.
        if self._setup_diagnostic is not None:
            changes['diagnostic'] = safe_setup_diagnostic(self._setup_diagnostic)
        self.recovery = replace(self.recovery, **changes)
        try:
            self._journal.save(self.recovery)
        except BaseException as exc:
            self._journal_writable = False  # Never retry a failed journal checkpoint.
            if self._setup_active:
                self._setup_diagnostic = replace(self._setup_diagnostic,
                    stage=SetupStage.JOURNAL_PERSISTENCE,
                    category=setup_category(exc, SetupStage.JOURNAL_PERSISTENCE))
                self.recovery = replace(self.recovery, diagnostic=safe_setup_diagnostic(self._setup_diagnostic))
            raise

    def _setup_stage(self, stage, *, ordinal=0, table=None):
        self._setup_diagnostic = replace(self._setup_diagnostic, stage=stage,
            category=SetupCategory.NONE, statement_ordinal=ordinal, table=table, inventory_fetch=None)
        self._record()

    def _save_setup_failure(self):
        self.recovery = replace(self.recovery, diagnostic=safe_setup_diagnostic(self._setup_diagnostic))
        if self._journal is not None and self._journal_writable:
            try:
                self._record()
            except (Exception, asyncio.CancelledError):
                pass  # Original failure/cancellation wins; prior disk evidence stays intact.

    def _capture_setup_failure(self, error):
        if self._setup_diagnostic.category is SetupCategory.NONE:
            category = setup_category(error, self._setup_diagnostic.stage)
            if isinstance(error, IntegrationSafetyError) and self._setup_diagnostic.stage is not SetupStage.JOURNAL_PERSISTENCE:
                category = SetupCategory(failure_category(error).value)
            self._setup_diagnostic = replace(self._setup_diagnostic,
                category=category)
        self._save_setup_failure()

    @asynccontextmanager
    async def _setup_transaction(self):
        self._setup_stage(SetupStage.BEGIN_SETUP)
        transaction = self._control.transaction()
        await transaction.__aenter__()
        try:
            yield
            # A failed precommit diagnostic checkpoint must also request rollback.
            self._setup_stage(SetupStage.COMMIT_SETUP)
        except BaseException as original:
            self._capture_setup_failure(original)
            self._setup_diagnostic = replace(self._setup_diagnostic, rollback='UNKNOWN')
            try:
                await transaction.__aexit__(type(original), original, original.__traceback__)
            except BaseException as rollback_error:
                self._setup_diagnostic = replace(self._setup_diagnostic, rollback='RAISED')
                if isinstance(rollback_error, asyncio.CancelledError):
                    self._save_setup_failure()
                    raise rollback_error from None
            else:
                self._setup_diagnostic = replace(self._setup_diagnostic, rollback='ACKNOWLEDGED')
            self._save_setup_failure()
            raise original from None
        else:
            # The durable commit_unknown checkpoint must precede this call.
            await transaction.__aexit__(None, None, None)

    async def _setup_tables(self, definitions):
        self._setup_stage(SetupStage.SEARCH_PATH_SETUP)
        await self._set_path(self._control)
        for ordinal, (name, body) in enumerate(definitions.items(), 1):
            self._setup_stage(SetupStage.CREATE_TABLE, ordinal=ordinal, table=name)
            await self._control.execute(f'CREATE TABLE "{self.schema}"."{name}" {body}')
        self._setup_stage(SetupStage.INVENTORY_QUERY)
        try:
            rows = await self._control.fetch(INVENTORY_SQL, self._namespace_oid)
        except BaseException as error:
            self._setup_diagnostic = replace(self._setup_diagnostic,
                inventory_fetch=classify_fetch_failure(error))
            raise
        self._setup_stage(SetupStage.INVENTORY_VALIDATION)
        self._validate_initial_inventory(rows, definitions)
        self._inventory = _fingerprint(rows)
        return hashlib.sha256(json.dumps(self._inventory).encode()).hexdigest()

    def _annotate_cancel(self, exc):
        exc.recovery = self.recovery
        exc.setup_diagnostic = self._setup_diagnostic
        exc.add_note(f'Test run {self.schema}; inspect its recovery journal. No prefix cleanup.')

    def _record_unconfirmed_close(self):
        self._state = 'failed'
        self.recovery = replace(self.recovery, phase='connection_close_unconfirmed')
        if not self._journal_writable:
            return
        try:
            self._record()
        except Exception:
            # Retain in-memory evidence; the previous journal stays intact.
            pass

    async def _dispose(self, con, *, cancellation=None):
        try:
            cleanup = await _close_connection(con, cancellation=cancellation)
            if self._setup_active:
                self._setup_diagnostic = replace(self._setup_diagnostic, connection_cleanup=cleanup)
                self._save_setup_failure()
        except (asyncio.CancelledError, IntegrationSafetyError) as exc:
            if self._setup_active:
                self._setup_diagnostic = replace(self._setup_diagnostic, connection_cleanup='UNCONFIRMED')
                self._save_setup_failure()
                exc.setup_diagnostic = self._setup_diagnostic
            if getattr(exc, 'termination_unconfirmed', False):
                self._record_unconfirmed_close()
            if isinstance(exc, asyncio.CancelledError):
                self._annotate_cancel(exc)
            else:
                exc.recovery = self.recovery
            raise

    async def _dispose_control(self, *, cancellation=None):
        """One shared, non-destructive connection-disposal worker, independent of DDL."""
        if self._control is None:
            return
        if self._control_dispose_task is None:
            self._control_dispose_task = asyncio.create_task(
                self._dispose(self._control, cancellation=cancellation))
        try:
            await _terminal_result(self._control_dispose_task)
        except (asyncio.CancelledError, IntegrationSafetyError) as exc:
            if cancellation is not None:
                if getattr(exc, 'termination_unconfirmed', False):
                    cancellation.termination_unconfirmed = True
                self._annotate_cancel(cancellation)
                raise cancellation from None
            if isinstance(exc, asyncio.CancelledError):
                self._annotate_cancel(exc)
            raise

    async def disconnect(self):
        """Dispose of the control session without SQL or removal of test objects.

        Existing checkouts still own their connections and must be awaited or
        cancelled by their caller. A running cleanup worker owns control disposal.
        """
        if self._close_task is not None and not self._close_task.done():
            raise IntegrationSafetyError('Cleanup already owns the control connection.',
                                         recovery=self.recovery)
        if self._state != 'closed':
            self._state = 'failed'  # Block acquisitions before awaiting disposal.
        await self._dispose_control()

    @classmethod
    async def create(cls, config, *, allow_database_writes=False, recovery_directory=None):
        if allow_database_writes is not True:
            raise IntegrationSafetyError('Database writes are disabled without explicit opt-in.')
        _require_config(config)
        definitions = _table_definitions()  # No network or SQL before manifest review.
        db = cls(config)
        db._setup_active = True
        db._setup_diagnostic = SetupDiagnostic(stage=SetupStage.JOURNAL_PERSISTENCE)
        cancellation = None
        failure = None
        try:
            db._journal = RecoveryJournal(recovery_directory, db.recovery)
            db._setup_stage(SetupStage.CONNECTION)
            db._record(phase='connecting')
            db._control = await _connect(config)
            db._setup_stage(SetupStage.IDENTITY_VERIFICATION)
            db._record(phase='verifying')
            identity = await verify_connection(db._control, config)
            if identity['can_create_schema'] is not True:
                raise IntegrationSafetyError('Private schema CREATE permission is required.')
            db._owner = identity['role_oid']
            db._version = identity['version']
            db._record(owner_oid=db._owner)
            db._setup_stage(SetupStage.NAMESPACE_VERIFICATION)
            if await db._control.fetchrow(NAMESPACE_SQL, db.schema) is not None:
                raise IntegrationSafetyError('Generated namespace already exists; refusing reuse.')
            db._setup_stage(SetupStage.ENABLE_WRITES)
            await db._control.execute('SET default_transaction_read_only = off')
            async with db._setup_transaction():
                db._record(phase='creating_schema', outcome='rollback_unverified')
                db._setup_stage(SetupStage.CREATE_SCHEMA)
                await db._control.execute(f'CREATE SCHEMA "{db.schema}" AUTHORIZATION CURRENT_USER')
                # No grants are changed on existing objects, including PUBLIC.
                namespace = await db._control.fetchrow(NAMESPACE_SQL, db.schema)
                if (not namespace or namespace['owner'] != db._owner
                        or namespace['acl_safe'] is not True):
                    raise IntegrationSafetyError('Created namespace ownership/ACL was rejected.')
                db._namespace_oid = namespace['oid']
                db._record(namespace_oid=db._namespace_oid, phase='creating_tables')
                digest = await db._setup_tables(definitions)
                db._record(inventory_digest=digest, table_oids=tuple(sorted(db._tables.items())),
                           phase='committing_setup', outcome='commit_unknown')
            db._record(phase='setup_committed', outcome='committed')
            db._setup_stage(SetupStage.RESTORE_READ_ONLY)
            await db._control.execute('SET default_transaction_read_only = on')
            db._record(phase='ready')
            db._state = 'ready'
            db._setup_active = False
            return db
        except asyncio.CancelledError as exc:
            cancellation = exc
            db._state = 'failed'
            db._capture_setup_failure(exc)
            if getattr(exc, 'termination_unconfirmed', False):
                db._record_unconfirmed_close()
            db._annotate_cancel(exc)
            raise
        except Exception as exc:
            db._state = 'failed'
            db._capture_setup_failure(exc)
            failure = IntegrationSafetyError('Isolation setup failed; inspect recovery metadata.',
                recovery=db.recovery, setup_diagnostic=db._setup_diagnostic)
            raise failure from None
        finally:
            if db._state != 'ready' and db._control is not None:
                await db._dispose_control(cancellation=cancellation)
            if failure is not None:
                failure.recovery, failure.setup_diagnostic = db.recovery, db._setup_diagnostic
            if cancellation is not None:
                cancellation.recovery, cancellation.setup_diagnostic = db.recovery, db._setup_diagnostic

    def _validate_initial_inventory(self, rows, definitions):
        self._validate_inventory_access(rows)
        base = {}
        for row in rows:
            if row['kind'] in ('trigger', 'rule', 'policy', 'statistics'):
                raise IntegrationSafetyError('Unapproved dependent object appeared during setup.')
            if row['kind'] == 'relation':
                details = json.loads(row['definition'])
                namespace = _inventory_oid(details['namespace'])
                if namespace == self._namespace_oid and details['kind'] == 'r':
                    base[row['name']] = row['oid']
                elif details['kind'] == 'i':
                    # Only manifest PK indexes, or internal TOAST indexes.
                    if namespace == self._namespace_oid and details['primary_index'] is not True:
                        raise IntegrationSafetyError('Unapproved index appeared during setup.')
                elif details['kind'] != 't':
                    raise IntegrationSafetyError('Unapproved relation kind appeared during setup.')
        if set(base) != set(definitions):
            raise IntegrationSafetyError('Created table manifest was not satisfied.')
        self._validate_constraints(rows, base)
        types = [r for r in rows if r['kind'] == 'type']
        composite = {r['oid'] for r in types if json.loads(r['definition'])['kind'] == 'c'
                     and r['parent'] in base.values()}
        arrays = [r for r in types if json.loads(r['definition'])['kind'] == 'b'
                  and _inventory_oid(json.loads(r['definition'])['element']) in composite]
        if len(composite) != len(base) or len(arrays) != len(base) or len(types) != 2 * len(base):
            raise IntegrationSafetyError('Unapproved or incomplete dependent type inventory.')
        self._tables = base

    def _validate_constraints(self, rows, base):
        if (set(base) != set(TABLE_SHAPES) or type(self._version) is not int
                or not 150000 <= self._version < 190000):
            raise IntegrationSafetyError('Constraint manifest or catalog version was rejected.')
        columns = {}
        for name, oid in base.items():
            entries = sorted((r for r in rows if r['kind'] == 'column' and r['parent'] == oid),
                             key=lambda r: r['subid'])
            shape = TABLE_SHAPES[name]
            if (tuple(r['name'] for r in entries) != shape.columns
                    or tuple(r['subid'] for r in entries) != tuple(range(1, len(shape.columns) + 1))):
                raise IntegrationSafetyError('Column addresses do not match the reviewed manifest.')
            for row in entries:
                details = json.loads(row['definition'])
                if (row['oid'] != oid or type(row['subid']) is not int
                        or details.get('notnull') is not True or details.get('dropped') is not False):
                    raise IntegrationSafetyError('Column nullability/identity was rejected.')
            columns[oid] = shape.columns
        expected = set()
        for name, shape in TABLE_SHAPES.items():
            expected.add((base[name], 'p', shape.primary_key))
            expected.update((base[name], 'c', tuple(sorted(keys))) for keys in shape.check_columns)
            if self._version >= 180000:
                expected.update((base[name], 'n', (column,)) for column in shape.columns)
        seen = set()
        for row in rows:
            if row['kind'] != 'constraint':
                continue
            details = json.loads(row['definition'])
            keys = details.get('columns')
            if (details.get('type') not in ('p', 'c', 'n')
                    or row['parent'] not in columns or not isinstance(keys, list) or not keys
                    or any(type(key) is not int or not 1 <= key <= len(columns[row['parent']]) for key in keys)
                    or len(set(keys)) != len(keys)
                    or any(details.get(key) is not True for key in ('validated', 'enforced', 'local'))
                    or any(details.get(key) is not False for key in ('deferrable', 'deferred'))
                    or type(details.get('inherited')) is not int or details['inherited'] != 0
                    or not isinstance(details.get('definition'), str) or not details['definition']):
                raise IntegrationSafetyError('Constraint metadata was rejected.')
            affected = tuple(columns[row['parent']][key - 1] for key in keys)
            if details.get('type') == 'c':
                affected = tuple(sorted(affected))
            signature = (row['parent'], details.get('type'), affected)
            if signature not in expected or signature in seen:
                raise IntegrationSafetyError('Constraint type/columns do not match the reviewed manifest.')
            seen.add(signature)
        if seen != expected:
            raise IntegrationSafetyError('Unapproved or incomplete constraint inventory.')

    def _validate_inventory_access(self, rows):
        if not rows or any(row.get('owner') != self._owner or row.get('acl_safe') is not True
                           or row.get('dependencies_safe') is not True for row in rows):
            raise IntegrationSafetyError('Object ownership, ACL or dependency policy was rejected.')

    async def _check_namespace(self, con):
        if not SCHEMA_PATTERN.fullmatch(self.schema):
            raise IntegrationSafetyError('Invalid disposable namespace identifier.')
        row = await con.fetchrow(NAMESPACE_SQL, self.schema)
        if (not row or row['oid'] != self._namespace_oid or row['owner'] != self._owner
                or row['acl_safe'] is not True):
            raise IntegrationSafetyError('Namespace identity, ownership or ACL changed.')

    async def _set_path(self, con):
        if not SCHEMA_PATTERN.fullmatch(self.schema):
            raise IntegrationSafetyError('Invalid disposable namespace identifier.')
        await con.fetchval("SELECT pg_catalog.set_config('search_path', $1, false)", f'"{self.schema}"')
        row = await con.fetchrow(PATH_SQL)
        if (not row or row['current_schema'] != self.schema
                or list(row['schemas']) != [self.schema] or row['temp_schema'] != 0):
            raise IntegrationSafetyError('Exclusive schema search path could not be established.')

    @asynccontextmanager
    async def acquire(self):
        if self._state != 'ready':
            raise IntegrationSafetyError('Disposable database is not ready.')
        self._active += 1  # No await between state test and registration.
        try:
            async with self._slots:
                con = None
                cancellation = None
                try:
                    con = await _connect(self.config)
                    await self._verify(con, namespace_oid=self._namespace_oid)
                    await self._check_namespace(con)
                    await self._set_path(con)
                    if self._state != 'ready':
                        raise IntegrationSafetyError('Test run failed during checkout.')
                    await con.execute('SET default_transaction_read_only = off')
                    if self._state != 'ready':
                        raise IntegrationSafetyError('Test run failed while enabling the checkout.',
                                                     recovery=self.recovery)
                    yield con
                except asyncio.CancelledError as exc:
                    cancellation = exc
                    raise
                finally:
                    if con is not None:
                        await self._dispose(con, cancellation=cancellation)
        except asyncio.CancelledError as exc:
            if getattr(exc, 'termination_unconfirmed', False):
                self._record_unconfirmed_close()
            self._annotate_cancel(exc)
            raise
        finally:
            self._active -= 1

    async def close(self):
        if self._state == 'closed':
            return
        if self._close_task is None:
            if self._state == 'failed':
                await self.disconnect()
                raise IntegrationSafetyError('Failed run disconnected; database objects were not removed.',
                                             recovery=self.recovery)
            if self._state != 'ready' or self._active:
                raise IntegrationSafetyError('Cleanup requires a ready run with no active checkouts.',
                                             recovery=self.recovery)
            self._state = 'closing'  # Before the first await; prevents late acquisitions.
            self._close_task = asyncio.create_task(self._close_impl())
        try:
            await _terminal_result(self._close_task)
        except asyncio.CancelledError as exc:
            self._annotate_cancel(exc)
            raise

    async def _close_impl(self):
        cancellation = None
        try:
            self._record(phase='cleanup_verifying')
            await self._verify(self._control, namespace_oid=self._namespace_oid)
            await self._check_namespace(self._control)
            await self._control.execute('SET default_transaction_read_only = off')
            async with self._control.transaction():
                for name in sorted(self._tables):
                    await self._control.execute(
                        f'LOCK TABLE "{self.schema}"."{name}" IN ACCESS EXCLUSIVE MODE')
                await self._check_namespace(self._control)
                rows = await self._control.fetch(INVENTORY_SQL, self._namespace_oid)
                self._validate_inventory_access(rows)
                if _fingerprint(rows) != self._inventory:
                    raise IntegrationSafetyError('Dependent-object inventory changed; refusing cleanup.')
                self._record(phase='dropping_objects', outcome='cleanup_rollback_unverified')
                for name in reversed(sorted(self._tables)):
                    await self._control.execute(f'DROP TABLE "{self.schema}"."{name}" RESTRICT')
                await self._control.execute(f'DROP SCHEMA "{self.schema}" RESTRICT')
                self._record(phase='committing_cleanup', outcome='cleanup_commit_unknown')
            self._record(phase='cleaned', outcome='cleaned')
            self._state = 'closed'
        except asyncio.CancelledError as exc:
            cancellation = exc
            self._state = 'failed'
            self._annotate_cancel(exc)
            raise
        except Exception:
            self._state = 'failed'
            raise IntegrationSafetyError('Cleanup failed; inspect recovery metadata. No broad retry.',
                                         recovery=self.recovery) from None
        finally:
            await self._dispose_control(cancellation=cancellation)
