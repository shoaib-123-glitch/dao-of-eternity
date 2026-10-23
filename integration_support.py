"""Fail-closed infrastructure only. Importing opens no connections or files.

preflight_catalog returns a report, never a connection. Future write-capable
operations are separate, opt-in and require a persistent local recovery journal.
No game integration cases or database runner are implemented here.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
import asyncio
import hashlib
import json
import os
import re
from uuid import uuid4

import asyncpg

from integration_catalog import (
    IDENTITY_SQL, PRIVILEGES_SQL, PRIVILEGE_FLAGS, NAMESPACE_SQL,
    PATH_SQL, INVENTORY_SQL, CATALOG_WRITES_SQL,
)
from integration_manifest import reviewed_table_definitions, TABLE_SHAPES
from integration_recovery import RecoveryJournal, RecoveryRecord


TEST_DATABASE = 'dao_eternity_test'
TEST_ROLE = 'dao_test_user'
CONFIG_KEYS = ('DAO_TEST_HOST', 'DAO_TEST_PORT', 'DAO_TEST_DATABASE',
               'DAO_TEST_USER', 'DAO_TEST_PASSWORD')
SCHEMA_PATTERN = re.compile(r'dao_it_[0-9a-f]{32}\Z')


class IntegrationSafetyError(RuntimeError):
    def __init__(self, message, *, recovery=None):
        super().__init__(message)
        self.recovery = recovery


@dataclass(frozen=True, repr=False)
class TestDatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    def __post_init__(self):
        if self.host not in ('localhost', '127.0.0.1'):
            raise IntegrationSafetyError('DAO_TEST_HOST must be localhost or 127.0.0.1.')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise IntegrationSafetyError('DAO_TEST_PORT must be an explicit valid TCP port.')
        if self.database != TEST_DATABASE or self.user != TEST_ROLE:
            raise IntegrationSafetyError('Only the dedicated test database and role are allowed.')
        if not isinstance(self.password, str) or not self.password:
            raise IntegrationSafetyError('DAO_TEST_PASSWORD must be explicitly supplied.')

    def __repr__(self):
        return 'TestDatabaseConfig(<redacted>)'

    @classmethod
    def from_environment(cls, environ=None):
        source = os.environ if environ is None else environ
        if any(not isinstance(source.get(key), str) or not source[key] for key in CONFIG_KEYS):
            raise IntegrationSafetyError('All five DAO_TEST_* connection variables are required.')
        port = source['DAO_TEST_PORT']
        if not re.fullmatch(r'[0-9]{1,5}', port):
            raise IntegrationSafetyError('DAO_TEST_PORT must contain only decimal digits.')
        return cls(source['DAO_TEST_HOST'], int(port), source['DAO_TEST_DATABASE'],
                   source['DAO_TEST_USER'], source['DAO_TEST_PASSWORD'])


@dataclass(frozen=True)
class PreflightReport:
    database: str
    role: str
    host: str
    port: int
    read_only: bool
    effective_privileges_verified: bool
    can_create_schema: bool


def _require_config(config):
    # Exact type prevents a subclass overriding validation/accessors.
    if type(config) is not TestDatabaseConfig:
        raise IntegrationSafetyError('An explicit TestDatabaseConfig is required.')
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
            error = IntegrationSafetyError('Connection closure could not be confirmed.')
            error.termination_unconfirmed = True
            raise error from None


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
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError as exc:
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
    except Exception:
        raise IntegrationSafetyError('Test connection failed; details suppressed.') from None


async def verify_connection(con, config, *, namespace_oid=None):
    """Private-use catalog verification. Returns identity metadata, never a session."""
    try:
        row = await con.fetchrow(IDENTITY_SQL)
        if not row or (row['database_name'], row['role_name'], row['session_role']) != (
                TEST_DATABASE, TEST_ROLE, TEST_ROLE):
            raise IntegrationSafetyError('Connected database or role identity was rejected.')
        if row['server_address'] != '127.0.0.1' or row['server_port'] != config.port:
            raise IntegrationSafetyError('Connected server address or port was rejected.')
        if row['read_only'] != 'on' or row['default_read_only'] != 'on':
            raise IntegrationSafetyError('Read-only transaction and session defaults are required.')
        if row['in_recovery'] is not False or row['temp_schema'] != 0:
            raise IntegrationSafetyError('Primary session without temporary objects required.')
        if not 150000 <= row['version'] < 190000:
            raise IntegrationSafetyError('Catalog layout is not approved for this server version.')
        if (row['can_login'] is not True or row['elevated'] is not False
                or row['other_memberships'] is not False or row['enabled_event_triggers'] is not False):
            raise IntegrationSafetyError('Role privileges, membership or event triggers were rejected.')
        if type(row['can_create_schema']) is not bool or type(row['role_oid']) is not int:
            raise IntegrationSafetyError('Identity metadata could not be established.')
        privileges = await con.fetchrow(PRIVILEGES_SQL, namespace_oid)
        if (not privileges or type(privileges.get('system_catalog_writes')) is not bool
                or any(privileges.get(key) is not False for key in PRIVILEGE_FLAGS
                       if key != 'system_catalog_writes')):
            raise IntegrationSafetyError('Effective privileges/default ACL policy was not satisfied.')
        if privileges['system_catalog_writes']:
            writes = await con.fetch(CATALOG_WRITES_SQL, namespace_oid)
            if len(writes) != 1 or not _standard_pg_settings_update(writes[0]):
                raise IntegrationSafetyError('Catalog modification privileges were rejected.')
        return dict(row)
    except asyncio.CancelledError:
        raise
    except IntegrationSafetyError:
        raise
    except Exception:
        raise IntegrationSafetyError('Catalog verification failed; details suppressed.') from None


def _standard_pg_settings_update(row):
    """Allow the built-in view's ordinary PUBLIC UPDATE, never arbitrary grants."""
    expected = dict(schema='pg_catalog', name='pg_settings', kind='v',
                    is_pg_settings=True, owned=False, other_table_access=False,
                    other_column_access=False, table_update=True,
                    update_acl_safe=True, explicit_column_update=False)
    return (type(row.get('oid')) is int and 0 < row['oid'] < 16384
            and all(type(row.get(key)) is type(value) and row[key] == value
                    for key, value in expected.items()))


async def preflight_catalog(config, *, allow_connection=False):
    """Catalog-only API: fixed SELECTs, a sanitized report, and mandatory close."""
    if allow_connection is not True:
        raise IntegrationSafetyError('Database connections are disabled without explicit opt-in.')
    con = await _connect(config)
    cancellation = None
    try:
        row = await verify_connection(con, config)
        return PreflightReport(TEST_DATABASE, TEST_ROLE, '127.0.0.1', config.port,
                               True, True, row['can_create_schema'])
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


class IsolatedTestDatabase:
    """Separate future write-capable API; no integration runner exists.

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

    def _record(self, **changes):
        # Retain the latest in-memory metadata even when a disk update fails.
        self.recovery = replace(self.recovery, **changes)
        self._journal.save(self.recovery)

    def _annotate_cancel(self, exc):
        exc.recovery = self.recovery
        exc.add_note(f'Test run {self.schema}; inspect its recovery journal. No prefix cleanup.')

    def _record_unconfirmed_close(self):
        self._state = 'failed'
        self.recovery = replace(self.recovery, phase='connection_close_unconfirmed')
        try:
            self._journal.save(self.recovery)
        except Exception:
            # Retain in-memory evidence; the previous journal stays intact.
            pass

    async def _dispose(self, con, *, cancellation=None):
        try:
            await _close_connection(con, cancellation=cancellation)
        except (asyncio.CancelledError, IntegrationSafetyError) as exc:
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
        cancellation = None
        try:
            db._journal = RecoveryJournal(recovery_directory, db.recovery)
            db._record(phase='connecting')
            db._control = await _connect(config)
            db._record(phase='verifying')
            identity = await verify_connection(db._control, config)
            if identity['can_create_schema'] is not True:
                raise IntegrationSafetyError('Private schema CREATE permission is required.')
            db._owner = identity['role_oid']
            db._version = identity['version']
            db._record(owner_oid=db._owner)
            if await db._control.fetchrow(NAMESPACE_SQL, db.schema) is not None:
                raise IntegrationSafetyError('Generated namespace already exists; refusing reuse.')
            await db._control.execute('SET default_transaction_read_only = off')
            async with db._control.transaction():
                db._record(phase='creating_schema', outcome='rollback_unverified')
                await db._control.execute(f'CREATE SCHEMA "{db.schema}" AUTHORIZATION CURRENT_USER')
                # No grants are changed on existing objects, including PUBLIC.
                namespace = await db._control.fetchrow(NAMESPACE_SQL, db.schema)
                if (not namespace or namespace['owner'] != db._owner
                        or namespace['acl_safe'] is not True):
                    raise IntegrationSafetyError('Created namespace ownership/ACL was rejected.')
                db._namespace_oid = namespace['oid']
                db._record(namespace_oid=db._namespace_oid, phase='creating_tables')
                await db._set_path(db._control)
                for name, body in definitions.items():
                    await db._control.execute(f'CREATE TABLE "{db.schema}"."{name}" {body}')
                db._record(phase='inventory')
                rows = await db._control.fetch(INVENTORY_SQL, db._namespace_oid)
                db._validate_initial_inventory(rows, definitions)
                db._inventory = _fingerprint(rows)
                digest = hashlib.sha256(json.dumps(db._inventory).encode()).hexdigest()
                db._record(inventory_digest=digest, table_oids=tuple(sorted(db._tables.items())),
                           phase='committing_setup', outcome='commit_unknown')
            db._record(phase='setup_committed', outcome='committed')
            await db._control.execute('SET default_transaction_read_only = on')
            db._record(phase='ready')
            db._state = 'ready'
            return db
        except asyncio.CancelledError as exc:
            cancellation = exc
            db._state = 'failed'
            if getattr(exc, 'termination_unconfirmed', False):
                db._record_unconfirmed_close()
            db._annotate_cancel(exc)
            raise
        except Exception:
            db._state = 'failed'
            raise IntegrationSafetyError('Isolation setup failed; inspect recovery metadata.',
                                         recovery=db.recovery) from None
        finally:
            if db._state != 'ready' and db._control is not None:
                await db._dispose_control(cancellation=cancellation)

    def _validate_initial_inventory(self, rows, definitions):
        self._validate_inventory_access(rows)
        base = {}
        for row in rows:
            if row['kind'] in ('trigger', 'rule', 'policy', 'statistics'):
                raise IntegrationSafetyError('Unapproved dependent object appeared during setup.')
            if row['kind'] == 'relation':
                details = json.loads(row['definition'])
                if details['namespace'] == self._namespace_oid and details['kind'] == 'r':
                    base[row['name']] = row['oid']
                elif details['kind'] == 'i':
                    # Only manifest PK indexes, or internal TOAST indexes.
                    if details['namespace'] == self._namespace_oid and details['primary_index'] is not True:
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
                  and json.loads(r['definition'])['element'] in composite]
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
                    await verify_connection(con, self.config, namespace_oid=self._namespace_oid)
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
            await verify_connection(self._control, self.config, namespace_oid=self._namespace_oid)
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
