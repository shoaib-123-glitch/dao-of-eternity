"""One throwaway PostgreSQL container per run, never an existing cluster.

The container is named dao-it-<hex>, labelled dao.it=1, publishes only a random
127.0.0.1 port and is removed by the exact ID this process created. Passwords
are random, held in memory and handed to Docker through the process
environment only. Game and console code connect as a non-superuser role that
owns a dao_it_<hex> database carrying the sandbox marker comment. Importing
this module runs nothing.
"""
import os
import re
import secrets
import subprocess
import time

IMAGE = 'postgres:18'
LABEL = 'dao.it=1'
CONTAINER_PATTERN = re.compile(r'dao-it-[0-9a-f]{32}\Z')
DATABASE_PATTERN = re.compile(r'dao_it_[0-9a-f]{32}\Z')
ROLE_PATTERN = re.compile(r'dao_it_app_[0-9a-f]{12}\Z')
PROTECTED_CONTAINERS = frozenset({'dao-postgres18-test'})
RESERVED_PORTS = frozenset({5432, 5433})
READY_LOG = 'PostgreSQL init process complete'
IDENTITY_SQL = '''SELECT pg_catalog.current_database()::text AS database_name, r.rolsuper, r.rolcreatedb,
 r.rolcreaterole, COALESCE(pg_catalog.shobj_description(d.oid, 'pg_database'), '') AS marker,
 d.datdba = r.oid AS owns_database
 FROM pg_catalog.pg_roles r, pg_catalog.pg_database d
 WHERE r.rolname = current_user AND d.datname = pg_catalog.current_database()'''


class ThrowawayRefused(RuntimeError):
    """Fixed messages only; never contains passwords, logs or SQL results."""


def check_container_name(name):
    if type(name) is not str or name in PROTECTED_CONTAINERS or not CONTAINER_PATTERN.fullmatch(name):
        raise ThrowawayRefused('Only a dao-it-<32 hex> container created by this run is allowed.')
    return name


def check_database_name(name):
    if type(name) is not str or not DATABASE_PATTERN.fullmatch(name):
        raise ThrowawayRefused('Only a dao_it_<32 hex> database is allowed.')
    return name


def check_role_name(name):
    if type(name) is not str or not ROLE_PATTERN.fullmatch(name):
        raise ThrowawayRefused('Only a dao_it_app_<12 hex> role is allowed.')
    return name


def parse_port(output):
    """`docker port` output must be exactly one 127.0.0.1 mapping on a free high port."""
    lines = [line.strip() for line in (output or '').splitlines() if line.strip()]
    match = re.fullmatch(r'127\.0\.0\.1:([0-9]{4,5})', lines[0]) if len(lines) == 1 else None
    port = int(match.group(1)) if match else 0
    if not 1024 <= port <= 65535 or port in RESERVED_PORTS:
        raise ThrowawayRefused('The container must publish exactly one random 127.0.0.1 port.')
    return port


def check_identity(row, database, marker):
    """Refuse unless the app session is a plain owner role in the marked dao_it_ database."""
    if (row is None or row['database_name'] != check_database_name(database) or row['marker'] != marker
            or row['owns_database'] is not True
            or any(row[flag] is not False for flag in ('rolsuper', 'rolcreatedb', 'rolcreaterole'))):
        raise ThrowawayRefused('The test session is not the non-superuser owner of the marked dao_it_ database.')


def run_arguments(name):
    check_container_name(name)
    # -e NAME without a value makes Docker read the password from our environment.
    return ['docker', 'run', '-d', '--name', name, '--label', LABEL,
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD', IMAGE]


def _docker(arguments, *, env=None, timeout=120):
    if not arguments or arguments[0] != 'docker' or arguments[1] not in ('run', 'port', 'logs', 'restart', 'rm'):
        raise ThrowawayRefused('Only docker run, port, logs, restart and rm are used.')
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout,
                            env=env, stdin=subprocess.DEVNULL)
    return result.returncode, result.stdout, result.stderr


class ThrowawayCluster:
    """Lifecycle: start() then connect as app; close() always removes the container."""

    def __init__(self, token=None):
        token = token or secrets.token_hex(16)
        self.name = check_container_name('dao-it-' + token)
        self.database = check_database_name('dao_it_' + token)
        self.role = check_role_name('dao_it_app_' + token[:12])
        self._admin_password = secrets.token_urlsafe(24)
        self.app_password = secrets.token_urlsafe(24)
        self.container_id = None
        self.port = None

    def __repr__(self):
        return f'ThrowawayCluster({self.name})'

    def start(self, *, ready_timeout=90):
        env = dict(os.environ, POSTGRES_PASSWORD=self._admin_password)
        code, out, _ = _docker(run_arguments(self.name), env=env)
        container_id = out.strip()
        if code != 0 or not re.fullmatch(r'[0-9a-f]{64}', container_id):
            raise ThrowawayRefused('docker run failed; no container was recorded.')
        self.container_id = container_id
        self._read_port()
        deadline = time.monotonic() + ready_timeout
        while True:  # Lifecycle polling only; test cases never use sleeps.
            code, out, err = _docker(['docker', 'logs', container_id])
            if code == 0 and READY_LOG in out + err:
                return self
            if time.monotonic() > deadline:
                raise ThrowawayRefused('The throwaway server did not finish initialising.')
            time.sleep(0.5)

    def _read_port(self):
        code, out, _ = _docker(['docker', 'port', self.container_id, '5432/tcp'])
        if code != 0:
            raise ThrowawayRefused('docker port failed.')
        self.port = parse_port(out)

    def restart(self):
        """Restart only this run's container; Docker may assign a new random port."""
        if self.container_id is None:
            raise ThrowawayRefused('Start the throwaway container first.')
        check_container_name(self.name)
        code, _, _ = _docker(['docker', 'restart', self.container_id])
        if code != 0:
            raise ThrowawayRefused('docker restart failed.')
        self.port = None
        self._read_port()

    async def wait_ready(self):
        """Connection retries are the readiness check after a restart."""
        await (await self._connect(self.role, self.app_password, self.database)).close()

    async def set_marker(self, marker):
        """Superuser-only marker change (None removes it) for refusal tests."""
        if self.port is None or (marker is not None and type(marker) is not str):
            raise ThrowawayRefused('A started container and a text marker or None are required.')
        admin = await self._connect('postgres', self._admin_password, 'postgres')
        try:
            await admin.execute(await admin.fetchval("SELECT format('COMMENT ON DATABASE %I IS %L', $1::text, $2::text)",
                                                     check_database_name(self.database), marker))
        finally:
            await admin.close()

    async def _connect(self, user, password, database, *, attempts=40):
        import asyncio
        import asyncpg
        for attempt in range(attempts):
            try:
                return await asyncpg.connect(host='127.0.0.1', port=self.port, user=user, password=password,
                                             database=database, ssl=False, timeout=5)
            except (OSError, asyncpg.CannotConnectNowError, asyncpg.ConnectionDoesNotExistError):
                if attempt == attempts - 1:
                    raise ThrowawayRefused('Could not connect to the throwaway server.') from None
                await asyncio.sleep(0.5)

    async def provision(self, marker):
        """Superuser only creates the role, the owned database and the marker comment."""
        if self.port is None:
            raise ThrowawayRefused('Start the throwaway container first.')
        admin = await self._connect('postgres', self._admin_password, 'postgres')
        try:
            statements = await admin.fetchrow(
                "SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', $1::text, $2::text) AS role_sql,"
                " format('CREATE DATABASE %I OWNER %I', $3::text, $1::text) AS database_sql,"
                " format('COMMENT ON DATABASE %I IS %L', $3::text, $4::text) AS comment_sql",
                self.role, self.app_password, self.database, marker)
            for key in ('role_sql', 'database_sql', 'comment_sql'):
                await admin.execute(statements[key])
        finally:
            await admin.close()
        app = await self._connect(self.role, self.app_password, self.database)
        try:
            check_identity(await app.fetchrow(IDENTITY_SQL), self.database, marker)
        finally:
            await app.close()

    async def app_pool(self, marker, *, max_size=8):
        import asyncpg
        pool = await asyncpg.create_pool(host='127.0.0.1', port=self.port, user=self.role,
                                         password=self.app_password, database=self.database,
                                         ssl=False, min_size=1, max_size=max_size)
        async with pool.acquire() as con:
            row = await con.fetchrow(IDENTITY_SQL)
        try:
            check_identity(row, self.database, marker)
        except ThrowawayRefused:
            await pool.close()
            raise
        return pool

    def close(self):
        """Remove only the exact container this object created; True when removed."""
        if self.container_id is None:
            return True
        check_container_name(self.name)
        code, _, _ = _docker(['docker', 'rm', '-f', self.container_id])
        if code == 0:
            self.container_id = None
        return code == 0
