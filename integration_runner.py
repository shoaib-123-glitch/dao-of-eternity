"""Explicit PostgreSQL suite runner. Default invocation reads no task inputs.

prepare writes a local plan only. run requires independent schema provisioning,
an exact namespace OID, explicit write opt-in and a securely prompted password.
No environment files, DSNs, admin connections, grants or bot startup are used.
"""
import argparse
import asyncio
from dataclasses import asdict, dataclass
import getpass
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
import warnings

from integration_support import (
    ExpectedServerIdentity, IntegrationSafetyError, IsolatedTestDatabase,
    SCHEMA_PATTERN, TestDatabaseConfig, _require_config, _table_definitions,
    setup_failure_diagnostic,
)


TARGET = {
    'host': '127.0.0.1', 'port': 5433, 'database': 'dao_eternity_test',
    'role': 'dao_test_user', 'server_address': '172.17.0.2', 'server_port': 5432,
    'system_identifier': 7699864005918920747,
}
CASE_NAMES = ('combat_companion', 'partnership_acceptance', 'opposing_dual',
              'rollback_retry', 'gathering_hall')
RETIRED_SCHEMAS = frozenset({'dao_it_ea92ae2483dd4ac494729f5628b429e2'})


@dataclass(frozen=True)
class IntegrationPlan:
    schema: str
    manifest_digest: str


def manifest_digest():
    definitions = _table_definitions()
    return hashlib.sha256(json.dumps(sorted(definitions.items())).encode()).hexdigest()


def validate_plan(plan):
    if (type(plan) is not IntegrationPlan or type(plan.schema) is not str
            or not SCHEMA_PATTERN.fullmatch(plan.schema)
            or type(plan.manifest_digest) is not str or plan.manifest_digest != manifest_digest()):
        raise IntegrationSafetyError('An intact offline plan and reviewed manifest are required.')


def require_fresh_plan(plan):
    # Historical inspectors still validate old plan structure and fingerprints.
    # Preparing/adopting a reservation is a separate, stricter operation.
    validate_plan(plan)
    if plan.schema in RETIRED_SCHEMAS:
        raise IntegrationSafetyError('This reservation is permanently retired.')


def prepare_plan(directory):
    """Offline only: reserve a random name; never create a database namespace."""
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise IntegrationSafetyError('An existing recovery directory is required.')
    plan = IntegrationPlan('dao_it_' + uuid4().hex, manifest_digest())
    require_fresh_plan(plan)
    path = root / (plan.schema + '.plan.json')
    payload = {'version': 1, **asdict(plan), 'target': dict(TARGET)}
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, sort_keys=True, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return path


def read_plan(path):
    path = Path(path)
    # Reject environment files and arbitrary names before opening anything.
    schema = path.name.removesuffix('.plan.json')
    if (path.name != schema + '.plan.json' or not SCHEMA_PATTERN.fullmatch(schema)
            or path.is_symlink() or not path.is_file() or path.stat().st_size > 16384):
        raise IntegrationSafetyError('A regular generated plan file is required.')
    payload = json.loads(path.read_text(encoding='utf-8'))
    if (type(payload) is not dict or set(payload) != {
            'version', 'schema', 'manifest_digest', 'target'}
            or type(payload['version']) is not int or payload['version'] != 1
            or type(payload['target']) is not dict or set(payload['target']) != set(TARGET)
            or any(type(payload['target'][key]) is not type(value)
                   or payload['target'][key] != value for key, value in TARGET.items())
            or payload['schema'] != schema):
        raise IntegrationSafetyError('Plan identity or Docker target changed.')
    plan = IntegrationPlan(payload['schema'], payload['manifest_digest'])
    validate_plan(plan)
    return plan


def require_target(config):
    _require_config(config)
    if (config.host, config.port, config.database, config.user) != (
            TARGET['host'], TARGET['port'], TARGET['database'], TARGET['role']):
        raise IntegrationSafetyError('Only the independently pinned Docker client target is allowed.')


async def run_suite(config, plan, namespace_oid, *, recovery_directory,
                    allow_database_writes=False):
    """No network, plan/manifest access or cases before the explicit write gate."""
    if allow_database_writes is not True:
        raise IntegrationSafetyError('Database-backed tests are disabled without explicit write opt-in.')
    require_target(config)
    require_fresh_plan(plan)
    if type(namespace_oid) is not int or not 16384 <= namespace_oid < 2**32:
        raise IntegrationSafetyError('An independent non-system namespace OID is required.')
    expected = ExpectedServerIdentity(TARGET['server_address'], TARGET['server_port'],
                                      TARGET['system_identifier'])
    # Importing these modules calls no app startup or setup/backfill hooks.
    from postgres_cases import CASES
    if tuple(name for name, _ in CASES) != CASE_NAMES:
        raise IntegrationSafetyError('The reviewed database case manifest changed.')
    db = await IsolatedTestDatabase.adopt_empty(
        config, plan.schema, namespace_oid, expected_server=expected,
        allow_database_writes=True, recovery_directory=recovery_directory)
    results = []
    try:
        for name, case in CASES:
            try:
                async with asyncio.timeout(90):
                    await case(db)
            except asyncio.CancelledError:
                raise
            except Exception:
                # SQL/driver/assertion text can contain data. Retain only a fixed
                # case label and stop; never retry a failing case silently.
                results.append({'case': name, 'passed': False})
                break
            else:
                results.append({'case': name, 'passed': True})
    finally:
        await db.close()
    return {'status': 'PASSED' if len(results) == len(CASE_NAMES) and all(
                result['passed'] for result in results) else 'FAILED',
            'cases': results, 'cleanup_verified': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Disabled-by-default isolated PostgreSQL suite')
    subcommands = parser.add_subparsers(dest='action')
    prepare = subcommands.add_parser('prepare', help='Offline local plan only; no database access')
    prepare.add_argument('--recovery-directory', required=True)
    run = subcommands.add_parser('run', help='Explicitly authorized prepared-schema writes only')
    run.add_argument('--plan', required=True)
    run.add_argument('--namespace-oid', required=True, type=int)
    run.add_argument('--allow-database-writes', action='store_true')
    args = parser.parse_args(argv)
    if args.action is None or (args.action == 'run' and not args.allow_database_writes):
        print('INTEGRATION DISABLED [WRITES_DISABLED]')
        return 2
    try:
        if args.action == 'prepare':
            path = prepare_plan(args.recovery_directory)
            print(json.dumps({'status': 'PLAN_PREPARED', 'schema': path.name.removesuffix('.plan.json')}))
            return 0
        if not 16384 <= args.namespace_oid < 2**32:
            raise IntegrationSafetyError('An independent non-system namespace OID is required.')
        plan = read_plan(args.plan)
        require_fresh_plan(plan)
        with warnings.catch_warnings():
            # getpass may otherwise fall back to echoed input on a non-console
            # host. Refuse that fallback instead of exposing a test credential.
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('Password for dao_test_user in dao-postgres18-test: ')
        config = TestDatabaseConfig(TARGET['host'], TARGET['port'], TARGET['database'],
                                    TARGET['role'], password)
        result = asyncio.run(run_suite(config, plan, args.namespace_oid,
                            recovery_directory=Path(args.plan).resolve().parent,
                            allow_database_writes=True))
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'PASSED' else 1
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        print('INTEGRATION CANCELLED [CANCELLED]: review the plan and recovery journal.')
        diagnostic = setup_failure_diagnostic(error)
        if diagnostic is not None:
            print(json.dumps({'setup_diagnostic': diagnostic}, sort_keys=True))
        return 130
    except Exception as error:
        print('INTEGRATION REJECTED [ISOLATION_OR_CLEANUP]: review the plan and recovery journal.')
        diagnostic = setup_failure_diagnostic(error)
        if diagnostic is not None:
            print(json.dumps({'setup_diagnostic': diagnostic}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
