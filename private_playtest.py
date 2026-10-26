"""Explicit private-gameplay startup. Importing this module performs no I/O.

Only the separately authorized entry point imports the bot. There is no dotenv,
provisioning, integration-runner, schema cleanup or fallback connection path.
"""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import importlib
from importlib import metadata
import json
import os
import re
import sys


class StartupStage(str, Enum):
    RUNTIME = 'RUNTIME'
    CONFIGURATION = 'CONFIGURATION'
    IMPORT_DISCORD = 'IMPORT_DISCORD'
    IMPORT_ASYNCPG = 'IMPORT_ASYNCPG'
    IMPORT_DOTENV = 'IMPORT_DOTENV'
    IMPORT_PIL = 'IMPORT_PIL'
    BOT_IMPORT = 'BOT_IMPORT'
    DISCORD_LOGIN = 'DISCORD_LOGIN'
    DISCORD_APPLICATION_CHECK = 'DISCORD_APPLICATION_CHECK'
    DISCORD_GUILD_FETCH = 'DISCORD_GUILD_FETCH'
    DISCORD_GUILD_CHECK = 'DISCORD_GUILD_CHECK'
    DATABASE_CONNECT = 'DATABASE_CONNECT'
    DATABASE_IDENTITY_QUERY = 'DATABASE_IDENTITY_QUERY'
    DATABASE_IDENTITY_CHECK = 'DATABASE_IDENTITY_CHECK'
    DATABASE_CORE_SCHEMA = 'DATABASE_CORE_SCHEMA'
    SETUP_COMBAT = 'SETUP_COMBAT'
    SETUP_LIVING_DAO = 'SETUP_LIVING_DAO'
    SETUP_SECT_HALLS = 'SETUP_SECT_HALLS'
    SETUP_SECT_V022 = 'SETUP_SECT_V022'
    SETUP_BONDS_V022 = 'SETUP_BONDS_V022'
    SETUP_GOLDEN_SANDBOX = 'SETUP_GOLDEN_SANDBOX'
    GUILD_COMMAND_COPY = 'GUILD_COMMAND_COPY'
    GUILD_COMMAND_SYNC = 'GUILD_COMMAND_SYNC'
    ANNOUNCER_START = 'ANNOUNCER_START'
    GATEWAY_CONNECT = 'GATEWAY_CONNECT'
    CLEANUP = 'CLEANUP'


REASONS = frozenset('''PRIVATE_PLAYTEST_AUTHORIZATION_REQUIRED
PRIVATE_PLAYTEST_CONFIGURATION_REQUIRED PRIVATE_PLAYTEST_TARGET_REJECTED
IDENTITY_UNAVAILABLE DATABASE_NAME_MISMATCH ROLE_NAME_MISMATCH SESSION_ROLE_MISMATCH
DATABASE_OWNER_MISMATCH CLUSTER_ID_MISMATCH READ_ONLY_MISMATCH SEARCH_PATH_MISMATCH
SCHEMA_MISMATCH VERSION_REJECTED SCHEMA_CREATE_REQUIRED ELEVATED_ROLE
ROLE_MEMBERSHIP EVENT_TRIGGERS_PRESENT APPLICATION_UNAVAILABLE APPLICATION_ID_MISMATCH
PUBLIC_BOT_ENABLED AUTHENTICATED_USER_NOT_BOT GUILD_ID_MISMATCH
READ_ONLY_CONFIG_REQUIRED WRITE_CONFIG_REQUIRED SAFETY_REJECTED'''.split())


class PlaytestRejected(RuntimeError):
    """Fixed, public rejection reason; never include secrets or driver messages."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in REASONS else 'SAFETY_REJECTED'
        super().__init__(self.code)


class StartupFailure(RuntimeError):
    """One boundary's sanitized metadata, preserved across nested async calls."""
    def __init__(self, stage, error):
        super().__init__('PRIVATE_PLAYTEST_STOPPED')
        self.details = failure_details(stage, error)


def failure_details(stage, error):
    # No str/repr(error), args, arbitrary class names, request/response bodies,
    # headers, filenames, URLs or traceback frames are inspected or emitted.
    result = dict(stage=stage.value, family='PYTHON_ERROR', reason=None,
                  http_status=None, sqlstate_class=None, sqlstate_code=None,
                  sqlstate_source='UNAVAILABLE')
    if type(error) is PlaytestRejected:
        result.update(family='SAFETY', reason=error.code)
    elif isinstance(error, ImportError):
        result['family'] = 'DEPENDENCY_IMPORT'
    elif isinstance(error, SyntaxError):
        result['family'] = 'PYTHON_SYNTAX'
    else:
        discord = sys.modules.get('discord')
        if discord is not None and isinstance(error, discord.LoginFailure):
            result['family'] = 'DISCORD_AUTHENTICATION'
        elif discord is not None and isinstance(error, discord.HTTPException):
            result['family'] = ('DISCORD_FORBIDDEN' if isinstance(error, discord.Forbidden)
                else 'DISCORD_NOT_FOUND' if isinstance(error, discord.NotFound) else 'DISCORD_HTTP')
            status = error.status
            if type(status) is int and status in (400, 401, 403, 404, 405, 429, 500, 502, 503, 504):
                result['http_status'] = status
        elif 'asyncpg' in sys.modules:
            from inventory_fetch_diagnostics import classify_fetch_failure, safe_fetch_diagnostic
            result.update(safe_fetch_diagnostic(classify_fetch_failure(error)))
        elif isinstance(error, TimeoutError):
            result['family'] = 'TIMEOUT'
        elif isinstance(error, ConnectionError):
            result['family'] = 'CONNECTION'
        elif isinstance(error, OSError):
            result['family'] = 'OS_ERROR'
    return result


@contextmanager
def startup_stage(stage):
    try:
        yield
    except StartupFailure:
        raise  # Keep the innermost failed stage, including pool init tasks.
    except Exception as error:
        raise StartupFailure(stage, error) from None


def runtime_info():
    versions = {}
    for package in ('discord.py', 'asyncpg', 'python-dotenv', 'Pillow'):
        try:
            value = metadata.version(package)
        except Exception:
            value = None
        versions[package] = value if type(value) is str and re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}(?:[a-z][0-9]{0,3})?', value) else None
    return dict(python_version='.'.join(str(n) for n in sys.version_info[:3]),
                virtual_environment=sys.prefix != sys.base_prefix, dependencies=versions)


def check_dependencies():
    for stage, module in ((StartupStage.IMPORT_DISCORD, 'discord'),
            (StartupStage.IMPORT_ASYNCPG, 'asyncpg'), (StartupStage.IMPORT_DOTENV, 'dotenv'),
            (StartupStage.IMPORT_PIL, 'PIL.Image')):
        with startup_stage(stage):
            importlib.import_module(module)


@dataclass(frozen=True)
class PrivatePlaytestConfig:
    host: str
    port: int
    database: str
    user: str
    system_identifier: int
    application_id: int
    guild_id: int
    token: str = field(repr=False)
    password: str = field(repr=False)
    readonly: bool = False

    @classmethod
    def from_environment(cls, env=None, *, readonly=False):
        env = os.environ if env is None else env
        authorization = 'read-only-and-discord' if readonly else 'writes-and-discord'
        if (env.get('APP_ENV') != 'private-playtest'
                or env.get('DAO_PLAYTEST_AUTHORIZED') != authorization):
            raise PlaytestRejected('PRIVATE_PLAYTEST_AUTHORIZATION_REQUIRED')
        try:
            def number(name):
                raw = env['DAO_PLAYTEST_' + name]
                if type(raw) is not str or not re.fullmatch('[0-9]+', raw):
                    raise ValueError
                return int(raw)
            value = cls(host=env['DAO_PLAYTEST_HOST'], port=number('PORT'),
                database=env['DAO_PLAYTEST_DATABASE'], user=env['DAO_PLAYTEST_USER'],
                system_identifier=number('SYSTEM_IDENTIFIER'),
                application_id=number('APPLICATION_ID'), guild_id=number('GUILD_ID'),
                token=env['DAO_PLAYTEST_TOKEN'], password=env['DAO_PLAYTEST_PASSWORD'], readonly=readonly)
        except (KeyError, TypeError, ValueError):
            raise PlaytestRejected('PRIVATE_PLAYTEST_CONFIGURATION_REQUIRED') from None
        value.validate()
        return value

    def validate(self):
        valid = (type(self.host) is str and self.host == '127.0.0.1'
            and type(self.port) is int and 1024 <= self.port <= 65535 and self.port != 5432
            and type(self.database) is str
            and re.fullmatch('dao_playtest_v023_[a-z0-9_]{1,24}', self.database)
            and type(self.user) is str
            and re.fullmatch('dao_playtest_[a-z0-9_]{1,32}', self.user)
            and type(self.system_identifier) is int and 0 < self.system_identifier < 2**63
            and type(self.application_id) is int and 0 < self.application_id < 2**64
            and type(self.guild_id) is int and 0 < self.guild_id < 2**64
            and type(self.token) is str and bool(self.token.strip())
            and type(self.password) is str and bool(self.password) and type(self.readonly) is bool)
        if not valid:
            raise PlaytestRejected('PRIVATE_PLAYTEST_TARGET_REJECTED')

    def connection_kwargs(self):
        self.validate()
        settings = {'search_path': 'public, pg_catalog',
            'application_name': 'dao-private-playtest-v023',
            'statement_timeout': '30000', 'lock_timeout': '5000'}
        if self.readonly:
            settings['default_transaction_read_only'] = 'on'
        return dict(host=self.host, port=self.port, database=self.database,
            user=self.user, password=self.password, ssl=False, timeout=10,
            command_timeout=30, server_settings=settings)


def sanitize_environment():
    """Private mode cannot inherit a service/passfile/URL or production bot ID."""
    for name in tuple(os.environ):
        if name.upper().startswith('PG') or name in {
                'DATABASE_URL', 'DISCORD_TOKEN', 'DISCORD_GUILD_ID', 'DEV_MODE', 'DEV_USER_ID'}:
            os.environ.pop(name, None)


# Qualified fixed metadata only. No player records, parameterized SQL or DDL.
# An inaccessible control-file function is a rejection, never grounds for grants.
IDENTITY_SQL = '''
SELECT pg_catalog.current_database()::text AS database_name,
 current_user::text AS role_name, session_user::text AS session_role,
 pg_catalog.pg_get_userbyid(d.datdba)::text AS database_owner,
 c.system_identifier::text AS system_identifier,
 pg_catalog.current_setting('server_version_num')::int AS version,
 pg_catalog.current_setting('transaction_read_only') AS read_only,
 pg_catalog.current_setting('search_path') AS search_path,
 pg_catalog.current_schema()::text AS schema_name,
 pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE') AS can_create,
 (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls) AS elevated,
 EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid) AS memberships,
 EXISTS (SELECT 1 FROM pg_catalog.pg_event_trigger WHERE evtenabled <> 'D') AS event_triggers
FROM pg_catalog.pg_database d
JOIN pg_catalog.pg_roles r ON r.rolname=current_user
CROSS JOIN pg_catalog.pg_control_system() c
WHERE d.datname=pg_catalog.current_database()
'''


async def verify_connection(con, config):
    config.validate()
    with startup_stage(StartupStage.DATABASE_IDENTITY_QUERY):
        async with con.transaction(readonly=True):
            row = await con.fetchrow(IDENTITY_SQL)
            with startup_stage(StartupStage.DATABASE_IDENTITY_CHECK):
                if row is None:
                    raise PlaytestRejected('IDENTITY_UNAVAILABLE')
                expected = (('database_name', config.database, 'DATABASE_NAME_MISMATCH'),
                    ('role_name', config.user, 'ROLE_NAME_MISMATCH'),
                    ('session_role', config.user, 'SESSION_ROLE_MISMATCH'),
                    ('database_owner', config.user, 'DATABASE_OWNER_MISMATCH'),
                    ('system_identifier', str(config.system_identifier), 'CLUSTER_ID_MISMATCH'),
                    ('read_only', 'on', 'READ_ONLY_MISMATCH'),
                    ('search_path', 'public, pg_catalog', 'SEARCH_PATH_MISMATCH'),
                    ('schema_name', 'public', 'SCHEMA_MISMATCH'))
                for key, value, reason in expected:
                    if row[key] != value:
                        raise PlaytestRejected(reason)
                if type(row['version']) is not int or not 180000 <= row['version'] < 190000:
                    raise PlaytestRejected('VERSION_REJECTED')
                for key, value, reason in (('can_create', True, 'SCHEMA_CREATE_REQUIRED'),
                        ('elevated', False, 'ELEVATED_ROLE'), ('memberships', False, 'ROLE_MEMBERSHIP'),
                        ('event_triggers', False, 'EVENT_TRIGGERS_PRESENT')):
                    if row[key] is not value:
                        raise PlaytestRejected(reason)


async def create_private_pool(config):
    import asyncpg
    config.validate()
    if config.readonly:
        raise PlaytestRejected('WRITE_CONFIG_REQUIRED')
    sanitize_environment()
    async def guard(con):
        await verify_connection(con, config)
    # init runs before each new connection can be borrowed, including reconnects.
    with startup_stage(StartupStage.DATABASE_CONNECT):
        return await asyncpg.create_pool(min_size=1, max_size=5, init=guard,
                                        **config.connection_kwargs())


async def verify_discord(client, config):
    """login populates application info before setup_hook; no sync or DB yet."""
    config.validate()
    with startup_stage(StartupStage.DISCORD_APPLICATION_CHECK):
        app = client.application
        if app is None:
            raise PlaytestRejected('APPLICATION_UNAVAILABLE')
        if app.id != config.application_id:
            raise PlaytestRejected('APPLICATION_ID_MISMATCH')
        if app.bot_public is not False:
            raise PlaytestRejected('PUBLIC_BOT_ENABLED')
        if client.user is None or client.user.bot is not True:
            raise PlaytestRejected('AUTHENTICATED_USER_NOT_BOT')
    with startup_stage(StartupStage.DISCORD_GUILD_FETCH):
        guild = await client.fetch_guild(config.guild_id)
    with startup_stage(StartupStage.DISCORD_GUILD_CHECK):
        if guild.id != config.guild_id:
            raise PlaytestRejected('GUILD_ID_MISMATCH')


async def start_private_client(client, token, *, reconnect=True):
    # Same login/connect ordering as discord.Client.start, with separate boundaries.
    with startup_stage(StartupStage.DISCORD_LOGIN):
        await client.login(token)
    with startup_stage(StartupStage.GATEWAY_CONNECT):
        await client.connect(reconnect=reconnect)


async def diagnose_read_only(config):
    """REST login/guild read + fixed catalog read. No bot import or gateway."""
    import discord
    import asyncpg
    config.validate()
    if not config.readonly:
        raise PlaytestRejected('READ_ONLY_CONFIG_REQUIRED')
    sanitize_environment()
    client, con = None, None
    try:
        with startup_stage(StartupStage.DISCORD_LOGIN):
            client = discord.Client(intents=discord.Intents.none())
            # Plain Client.setup_hook is empty; no game startup callback exists.
            await client.login(config.token)
        await verify_discord(client, config)
        with startup_stage(StartupStage.DATABASE_CONNECT):
            con = await asyncpg.connect(**config.connection_kwargs())
        await verify_connection(con, config)
    finally:
        try:
            if con is not None:
                with startup_stage(StartupStage.CLEANUP):
                    try:
                        await con.close(timeout=5)
                    except BaseException:
                        con.terminate()
                        raise
        finally:
            if client is not None:
                with startup_stage(StartupStage.CLEANUP):
                    await client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Private v0.23 gameplay launch; writes and Discord login.')
    parser.add_argument('--authorize-database-writes', action='store_true')
    parser.add_argument('--authorize-discord-login', action='store_true')
    parser.add_argument('--diagnose-only', action='store_true')
    parser.add_argument('--authorize-read-only-connection', action='store_true')
    args = parser.parse_args(argv)
    permitted = (args.authorize_discord_login and (
        args.authorize_read_only_connection and not args.authorize_database_writes
        if args.diagnose_only else args.authorize_database_writes and not args.authorize_read_only_connection))
    if not permitted:
        print('PRIVATE_PLAYTEST_AUTHORIZATION_REQUIRED')
        return 2
    os.environ['APP_ENV'] = 'private-playtest'
    os.environ['DAO_PLAYTEST_AUTHORIZED'] = 'read-only-and-discord' if args.diagnose_only else 'writes-and-discord'
    try:
        print('PRIVATE_PLAYTEST_RUNTIME ' + json.dumps(runtime_info(), sort_keys=True))
        with startup_stage(StartupStage.CONFIGURATION):
            config = PrivatePlaytestConfig.from_environment(readonly=args.diagnose_only)
        sanitize_environment()
        check_dependencies()
        if args.diagnose_only:
            with startup_stage(StartupStage.RUNTIME):
                asyncio.run(diagnose_read_only(config))
            print('PRIVATE_PLAYTEST_DIAGNOSTIC_PASSED: REST and catalog identity checks passed; connections closed; no game startup.')
        else:
            with startup_stage(StartupStage.BOT_IMPORT):
                bot = importlib.import_module('bot')
            with startup_stage(StartupStage.RUNTIME):
                bot.bot.run(bot.TOKEN, log_handler=None)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        details = error.details if type(error) is StartupFailure else failure_details(StartupStage.RUNTIME, error)
        print('PRIVATE_PLAYTEST_STOPPED ' + json.dumps(details, sort_keys=True))
        return 1
    finally:
        os.environ.pop('DAO_PLAYTEST_AUTHORIZED', None)
    return 0


if __name__ == '__main__':
    # Bot/config/storage imports use this canonical module and exception identity.
    from private_playtest import main as run_main
    raise SystemExit(run_main())
