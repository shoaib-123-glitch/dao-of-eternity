"""Fresh PostgreSQL operator workflow. Every live stage requires an explicit gate.

prepare/check are offline. verify/provision use only pinned container-local psql.
run delegates to the existing production-backed suite after validating receipts.
All local artifacts are exclusive writes; historical recovery is never accessed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import integration_runner as runner
import retirement_operator as safety


PROJECT = Path(__file__).resolve().parent
DEFAULT_ROOT = PROJECT / 'fresh_integration'
OWNER_OID = 16389
RETIRED_OID = 16390
# These files define the setup, cleanup, inventory, scenarios and process policy.
SOURCE_FILES = ('fresh_integration.py', 'retirement_operator.py', 'integration_runner.py',
                'integration_support.py', 'integration_catalog.py', 'integration_manifest.py',
                'integration_recovery.py', 'integration_diagnostics.py', 'inventory_fetch_diagnostics.py', 'postgres_cases.py')
PRODUCTION_FILES = ('storage.py', 'combat_store.py', 'combat.py', 'beast_v022.py',
                    'bonds_v022.py', 'sect_halls.py', 'sect_v022.py', 'inventory.py',
                    'living_dao.py', 'game.py', 'engine.py', 'heavens.py', 'eternal.py',
                    'shattered_heavens_v023.py', 'sect_activities_v023.py', 'private_playtest.py',
                    'combat_journey_v024.py', 'companion_bonds_v024.py', 'living_world_v024.py',
                    'sect_projects_v024.py', 'onboarding_v024.py',
                    'living_world_v025.py', 'living_world_ui_v025.py',
                    'candidate_policy_v025.py', 'security_observer_v025.py',
                    'dev_console_v025.py', 'dev_console_ui_v025.py')


def _oid(value):
    if type(value) is not int or not 16384 <= value < 2**32:
        safety.reject('OID')
    return value


def _assert_sql(expression, label):
    return (f"SELECT COALESCE(({expression}),false) AS safe\n"
            "\\gset dao_check_\n\\if :dao_check_safe\n\\else\n"
            f" DO $$ BEGIN RAISE EXCEPTION '{label}'; END; $$;\n\\endif\n")


def _identity_sql():
    return """current_database() = 'dao_eternity_test'
 AND (SELECT oid FROM pg_database WHERE datname=current_database()) = :'dao_database_oid'::oid
 AND current_user = session_user AND session_user::text = :'dao_admin_role'
 AND session_user <> 'dao_test_user'
 AND current_setting('default_transaction_read_only') = 'on'
 AND (SELECT system_identifier::text FROM pg_control_system()) = '7699864005918920747'
 AND current_setting('server_version_num')::int = 180006
 AND host(inet_server_addr()) = '172.17.0.2' AND inet_server_port() = 5432
 AND NOT pg_is_in_recovery()
 AND has_database_privilege(session_user,current_database(),'CREATE')
 AND pg_has_role(session_user,'dao_test_user','SET')
 AND EXISTS (SELECT 1 FROM pg_roles r WHERE r.oid=16389 AND r.rolname='dao_test_user'
   AND r.rolcanlogin AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
     OR r.rolreplication OR r.rolbypassrls)
   AND NOT EXISTS (SELECT 1 FROM pg_roles o WHERE o.oid<>r.oid AND pg_has_role(r.oid,o.oid,'MEMBER')))
 AND has_database_privilege('dao_test_user',current_database(),'CONNECT')
 AND NOT has_database_privilege('dao_test_user',current_database(),'CREATE')
 AND NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtenabled<>'D')
 AND NOT EXISTS (SELECT 1 FROM pg_stat_activity
   WHERE datname=current_database() AND usename='dao_test_user')
 AND NOT EXISTS (SELECT 1 FROM pg_namespace
   WHERE nspname='dao_it_ea92ae2483dd4ac494729f5628b429e2' OR oid=16390)"""


def _policy_sql(schema, *, present):
    # Reuse the COMPLETE canonical policy, including the narrow pg_settings rule.
    # Before CREATE there is no excluded namespace; afterward use its actual OID.
    expression = f"(SELECT oid FROM pg_namespace WHERE nspname='{schema}')" if present else '0'
    text = safety.policy_guard().replace('16390', expression)
    if not present:
        text = text.replace(f" AND pg_catalog.has_schema_privilege(current_user,{expression}::oid,'USAGE')\n", '')
        text = text.replace(f" AND pg_catalog.has_schema_privilege(current_user,{expression}::oid,'CREATE')\n", '')
    return text.replace('RETIREMENT_EFFECTIVE_PRIVILEGES_REJECTED', 'FRESH_EFFECTIVE_PRIVILEGES_REJECTED')


def _empty_sql(schema):
    return f"""EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname='{schema}'
 AND n.oid=:'dao_actual_oid'::oid AND n.oid>=16384 AND n.oid<>16390 AND n.nspowner=16389
 AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) x
   WHERE x.grantee<>n.nspowner)
 AND has_schema_privilege('dao_test_user',n.oid,'USAGE')
 AND has_schema_privilege('dao_test_user',n.oid,'CREATE')
 AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relnamespace=n.oid)
 AND NOT EXISTS (SELECT 1 FROM pg_depend WHERE refclassid='pg_namespace'::regclass AND refobjid=n.oid)
 AND NOT EXISTS (SELECT 1 FROM pg_extension WHERE extnamespace=n.oid))"""


def _receipt_sql(schema, phase):
    oid = 'NULL' if phase == 'verified_absent' else ":'dao_actual_oid'::bigint"
    return f"""SELECT jsonb_build_object('version',1,'phase','{phase}',
 'schema','{schema}','namespace_oid',{oid},'owner_oid',16389,
 'database','dao_eternity_test','database_oid',:'dao_database_oid'::bigint,
 'role','dao_test_user','container_id',:'dao_container_id',
 'cluster_identifier','7699864005918920747','checks_passed',true);
"""


def batch_text(plan):
    runner.require_fresh_plan(plan)
    schema = plan.schema
    initial = _assert_sql(_identity_sql(), 'FRESH_IDENTITY_REJECTED')
    absent = _assert_sql(f"NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='{schema}')",
                         'FRESH_RESERVATION_ALREADY_EXISTS')
    before = initial + absent + _policy_sql(schema, present=False)
    after = initial + _assert_sql(_empty_sql(schema), 'FRESH_SCHEMA_STATE_UNKNOWN') + _policy_sql(schema, present=True)
    return ("""-- Sealed fresh reservation. Default action is read-only verification.
-- No recovery/drop path, grants, revokes or role/database permission changes.
\\set ON_ERROR_STOP on
\\set ON_ERROR_ROLLBACK off
\\set ECHO none
\\set VERBOSITY terse
\\pset pager off
\\pset format unaligned
\\pset tuples_only on
\\if :{?dao_action}
\\else
 \\set dao_action verify
\\endif
""" + _assert_sql(""" :'dao_action' IN ('verify','provision')
 AND :'dao_admin_role' ~ '^[a-z_][a-z0-9_]{0,62}$'
 AND :'dao_admin_role'<>'dao_test_user'
 AND :'dao_database_oid' ~ '^[1-9][0-9]{0,9}$'
 AND :'dao_database_oid'::bigint BETWEEN 16384 AND 4294967295
 AND :'dao_container_id' ~ '^[0-9a-f]{64}$'""", 'FRESH_INPUT_REJECTED') + """
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
SET LOCAL statement_timeout = '10s';
""" + before + """COMMIT;
SELECT :'dao_action'='verify' AS verifying
\\gset dao_mode_
\\if :dao_mode_verifying
 \\o :dao_receipt_path
""" + _receipt_sql(schema, 'verified_absent') + """ \\o
 \\quit
\\endif
""" + _assert_sql(f" :'dao_authorize_schema'='{schema}'", 'FRESH_WRITES_NOT_AUTHORIZED') + """
BEGIN READ WRITE;
SET LOCAL search_path = pg_catalog;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '10s';
SET LOCAL idle_in_transaction_session_timeout = '30s';
""" + before + f'CREATE SCHEMA "{schema}" AUTHORIZATION dao_test_user;\n' + f"""
SELECT oid AS oid FROM pg_namespace WHERE nspname='{schema}'
\\gset dao_actual_
""" + after + """\\o :dao_precommit_path
""" + _receipt_sql(schema, 'commit_unknown') + """\\o
COMMIT;
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
SET LOCAL statement_timeout = '10s';
""" + after + """\\o :dao_receipt_path
""" + _receipt_sql(schema, 'provisioned') + """\\o
COMMIT;
""")


def _sources():
    return {name: safety._sha(safety._read(PROJECT / name)) for name in SOURCE_FILES + PRODUCTION_FILES}


def _bundle_path(path):
    path = safety._plain_path(path)
    historical = PROJECT / 'integration_recovery'
    if path == historical or historical in path.parents:
        safety.reject('HISTORICAL_DIRECTORY')
    return path


def _retirement_report():
    return {'source': 'operator_report_2026-10-24', 'status': 'ABSENCE_OBSERVED',
            'exit_code': 0, 'evidence_unchanged': True,
            'historical_rollback_verification': 'UNKNOWN',
            'schema': next(iter(runner.RETIRED_SCHEMAS)), 'namespace_oid': RETIRED_OID}


def prepare(root=DEFAULT_ROOT):
    root = _bundle_path(root)
    root.mkdir(exist_ok=True)
    bundle = safety._new_run(root)
    path = runner.prepare_plan(bundle)
    plan = runner.read_plan(path)
    batch = batch_text(plan).encode()
    with (bundle / 'provision.psql').open('xb') as stream:
        stream.write(batch)
        stream.flush()
        safety.os.fsync(stream.fileno())
    seal = {'version': 1, 'schema': plan.schema, 'plan_sha256': safety._sha(safety._read(path)),
            'batch_sha256': safety._sha(batch), 'sources': _sources(),
            'cases': list(runner.CASE_NAMES), 'retirement_report': _retirement_report()}
    safety._write_new(bundle / 'seal.json', seal)
    check(bundle)
    return {'status': 'PREPARED_OFFLINE', 'schema': plan.schema, 'bundle': str(bundle)}


def check(bundle):
    bundle = _bundle_path(bundle)
    seal = safety._json(safety._read(bundle / 'seal.json').decode())
    if type(seal) is not dict or set(seal) != {'version', 'schema', 'plan_sha256', 'batch_sha256',
                                             'sources', 'cases', 'retirement_report'}:
        safety.reject('SEAL_FORMAT')
    schema = seal['schema']
    if type(schema) is not str or not runner.SCHEMA_PATTERN.fullmatch(schema) or schema in runner.RETIRED_SCHEMAS:
        safety.reject('RESERVATION')
    path = bundle / (schema + '.plan.json')
    plan = runner.read_plan(path)
    batch = safety._read(bundle / 'provision.psql')
    if (type(seal['version']) is not int or seal['version'] != 1
            or seal['plan_sha256'] != safety._sha(safety._read(path))
            or seal['batch_sha256'] != safety._sha(batch)
            or batch != batch_text(plan).encode() or seal['sources'] != _sources()
            or seal['cases'] != list(runner.CASE_NAMES)
            or seal['retirement_report'] != _retirement_report()):
        safety.reject('SEAL_DRIFT')
    return bundle, plan, seal


def _pins(container_id, database_oid, admin_role):
    _oid(database_oid)
    if (type(container_id) is not str or not re.fullmatch('[0-9a-f]{64}', container_id)
            or type(admin_role) is not str or not re.fullmatch('[a-z_][a-z0-9_]{0,62}', admin_role)
            or admin_role == 'dao_test_user'):
        safety.reject('INPUT_PINS')
    return {'container_id': container_id, 'database_oid': database_oid, 'admin_role': admin_role}


def _receipt(record, phase, plan, pins):
    expected = dict(version=1, phase=phase, schema=plan.schema, owner_oid=OWNER_OID,
                    database='dao_eternity_test', database_oid=pins['database_oid'],
                    role='dao_test_user', container_id=pins['container_id'],
                    cluster_identifier='7699864005918920747', checks_passed=True)
    if type(record) is not dict or set(record) != set(expected) | {'namespace_oid'}:
        safety.reject('RECEIPT_FORMAT')
    for key, value in expected.items():
        if type(record[key]) is not type(value) or record[key] != value:
            safety.reject('RECEIPT_IDENTITY')
    if phase == 'verified_absent':
        if record['namespace_oid'] is not None:
            safety.reject('RECEIPT_OID')
    elif _oid(record['namespace_oid']) == RETIRED_OID:
        safety.reject('RETIRED_OID')
    return record


def _proof(bundle, action, seal):
    record = safety._json(safety._read(bundle / (action + '.result.json')).decode())
    if (type(record) is not dict or set(record) != {'version', 'status', 'seal_sha256', 'pins', 'receipt_sha256'}
            or type(record['version']) is not int or record['version'] != 1
            or record['status'] != ('VERIFIED_ABSENT' if action == 'verify' else 'PROVISIONED')
            or record['seal_sha256'] != safety._sha(safety._read(bundle / 'seal.json'))):
        safety.reject('PRIOR_PROOF')
    pins = record['pins']
    if type(pins) is not dict or set(pins) != {'container_id', 'database_oid', 'admin_role'}:
        safety.reject('PRIOR_PINS')
    _pins(**pins)
    data = safety._read(bundle / (action + '.receipt.json'))
    if record['receipt_sha256'] != safety._sha(data):
        safety.reject('RECEIPT_DRIFT')
    plan = runner.IntegrationPlan(seal['schema'], runner.manifest_digest())
    receipt = _receipt(safety._json(data.decode()), 'verified_absent' if action == 'verify' else 'provisioned', plan, pins)
    return pins, receipt


def _psql_args(pins, stage, action, schema):
    return ['exec', '--interactive', '--tty', '--user', 'postgres', pins['container_id'],
        'env', '-u', 'PGSERVICE', '-u', 'PGSERVICEFILE', '-u', 'PGSYSCONFDIR', '-u', 'PGOPTIONS',
        '-u', 'PGPASSWORD', '-u', 'PGPASSFILE', 'PGHOSTADDR=172.17.0.2',
        'PGOPTIONS=-c default_transaction_read_only=on',
        'psql', '-X', '-W', '-q', '--host=172.17.0.2', '--port=5432',
        '--username=' + pins['admin_role'], '--dbname=dao_eternity_test',
        '--set=VERBOSITY=terse', '--set=SHOW_CONTEXT=never',
        '--set=dao_admin_role=' + pins['admin_role'], '--set=dao_database_oid=' + str(pins['database_oid']),
        '--set=dao_container_id=' + pins['container_id'], '--set=dao_action=' + action,
        '--set=dao_authorize_schema=' + (schema if action == 'provision' else 'DISABLED'),
        '--set=dao_receipt_path=' + stage + '/receipt.json',
        '--set=dao_precommit_path=' + stage + '/precommit.json', '--file=' + stage + '/provision.psql']


def operate(bundle, *, action='verify', allow_read_only_connection=False,
            allow_database_writes=False, container_id='', database_oid=None, admin_role=''):
    # Authorization precedes even local task input/evidence reads or prompts.
    if action not in ('verify', 'provision') or allow_read_only_connection is not True:
        safety.reject('CONNECTION_DISABLED')
    if action == 'provision' and allow_database_writes is not True:
        safety.reject('WRITES_DISABLED')
    bundle, plan, seal = check(bundle)
    if action == 'provision':
        pins, _ = _proof(bundle, 'verify', seal)
    else:
        pins = _pins(container_id, database_oid, admin_role)
    # Durable, exclusive single-use attempt BEFORE any Docker call. Never retry.
    safety._write_new(bundle / (action + '.attempt.json'), {
        'version': 1, 'outcome': 'UNKNOWN', 'pins': pins,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'seal_sha256': safety._sha(safety._read(bundle / 'seal.json'))})
    stage = '/tmp/' + plan.schema + '.' + action + '-' + safety.uuid4().hex
    result_path = bundle / (action + '.result.json')
    try:
        safety._docker_identity(pins['container_id'])
        safety._native(['exec', '--user', 'postgres', pins['container_id'], 'mkdir', '-m', '0700', stage])
        safety._native(['cp', '--', str(bundle / 'provision.psql'), pins['container_id'] + ':' + stage + '/provision.psql'])
        digest = safety._native(['exec', '--user', 'postgres', pins['container_id'], 'sha256sum', stage + '/provision.psql'])
        if digest.rstrip('\r\n') != seal['batch_sha256'] + '  ' + stage + '/provision.psql':
            safety.reject('STAGED_HASH')
        safety._write_new(bundle / (action + '.staging.json'), {'directory': stage, 'pins': pins})
        check(bundle)
        safety._docker_identity(pins['container_id'])
        exit_code = safety._native(_psql_args(pins, stage, action, plan.schema), interactive=True)
        safety._write_new(bundle / (action + '.exit.json'), {'exit_code': exit_code})
        # Preserve even partial/uncertain receipts, BEFORE interpreting exit status.
        # cat is read-only; raw bytes get a new exclusive local file, never a cp overwrite.
        captured = {}
        for name in (('precommit', 'receipt') if action == 'provision' else ('receipt',)):
            try:
                data = safety._native(['exec', '--user', 'postgres', pins['container_id'], 'cat', '--', stage + '/' + name + '.json'])
            except safety.OperatorRejected:
                continue
            target = bundle / (action + '.' + name + '.raw.json')
            with target.open('xb') as stream:
                stream.write(data.encode())
                stream.flush()
                safety.os.fsync(stream.fileno())
            captured[name] = data
        if exit_code != 0:
            safety.reject('PSQL_EXIT_UNKNOWN')
        receipt = _receipt(safety._json(captured.get('receipt', '')), 'verified_absent' if action == 'verify' else 'provisioned', plan, pins)
        if action == 'provision':
            precommit = _receipt(safety._json(captured.get('precommit', '')), 'commit_unknown', plan, pins)
            if precommit['namespace_oid'] != receipt['namespace_oid']:
                safety.reject('PRECOMMIT_OID_MISMATCH')
            safety._write_new(bundle / 'provision.precommit.json', precommit)
        check(bundle)
        receipt_hash = safety._write_new(bundle / (action + '.receipt.json'), receipt)
        result = {'version': 1, 'status': 'VERIFIED_ABSENT' if action == 'verify' else 'PROVISIONED',
                  'seal_sha256': safety._sha(safety._read(bundle / 'seal.json')),
                  'pins': pins, 'receipt_sha256': receipt_hash}
        safety._write_new(result_path, result)
        return {'status': result['status'], 'schema': plan.schema, 'namespace_oid': receipt['namespace_oid']}
    except BaseException:
        if not result_path.exists():
            safety._write_new(result_path, {'version': 1, 'status': 'UNKNOWN',
                'container_directory': stage, 'instruction': 'Preserve all files; separately inspect; do not retry.'})
        raise


def run(bundle, *, allow_database_writes=False):
    if allow_database_writes is not True:
        safety.reject('WRITES_DISABLED')
    bundle, plan, seal = check(bundle)
    pins, receipt = _proof(bundle, 'provision', seal)
    verified_pins, _ = _proof(bundle, 'verify', seal)
    if pins != verified_pins:
        safety.reject('PRIOR_PINS')
    # An existing runner journal also forbids reuse. No evidence is repaired.
    if (bundle / (plan.schema + '.json')).exists():
        safety.reject('RUNNER_JOURNAL_EXISTS')
    safety._write_new(bundle / 'suite.attempt.json', {'version': 1, 'outcome': 'UNKNOWN',
        'schema': plan.schema, 'namespace_oid': receipt['namespace_oid'],
        'seal_sha256': safety._sha(safety._read(bundle / 'seal.json'))})
    code = runner.main(['run', '--plan', str(bundle / (plan.schema + '.plan.json')),
                        '--namespace-oid', str(receipt['namespace_oid']), '--allow-database-writes'])
    safety._write_new(bundle / 'suite.result.json', {'version': 1,
        'status': 'PASSED' if code == 0 else 'UNKNOWN', 'runner_exit_code': code})
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'verify', 'provision', 'run'), nargs='?')
    parser.add_argument('--bundle')
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--allow-read-only-connection', action='store_true')
    parser.add_argument('--allow-database-writes', action='store_true')
    parser.add_argument('--container-id')
    parser.add_argument('--database-oid', type=int)
    parser.add_argument('--admin-role')
    args = parser.parse_args(argv)
    if (args.action is None or args.action in ('verify', 'provision') and not args.allow_read_only_connection
            or args.action in ('provision', 'run') and not args.allow_database_writes):
        print('FRESH INTEGRATION DISABLED [EXPLICIT_AUTHORIZATION_REQUIRED]')
        return 2
    try:
        if args.action == 'prepare':
            result = prepare(args.root)
        else:
            if args.bundle is None:
                safety.reject('BUNDLE_REQUIRED')
            if args.action == 'check':
                _, plan, _ = check(args.bundle)
                result = {'status': 'SEALED_OFFLINE', 'schema': plan.schema}
            elif args.action == 'run':
                return run(args.bundle, allow_database_writes=True)
            else:
                inputs = {}
                if args.action == 'verify':
                    # Read public identity pins only after the gate; no password in argv.
                    inputs = dict(container_id=args.container_id or input('Independently recorded full container ID: '),
                                  database_oid=args.database_oid or int(input('Independently recorded database OID: ')),
                                  admin_role=args.admin_role or input('Existing administrator role: '))
                result = operate(args.bundle, action=args.action, allow_read_only_connection=True,
                                 allow_database_writes=args.allow_database_writes, **inputs)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyboardInterrupt, EOFError):
        print('FRESH INTEGRATION CANCELLED [UNKNOWN]; preserve files; do not retry.')
        return 130
    except Exception as error:
        category = error.category if isinstance(error, safety.OperatorRejected) else 'LOCAL_OR_PROCESS_FAILURE'
        print(json.dumps({'status': 'REJECTED', 'category': category,
                          'instruction': 'Preserve files; review any attempt before another operation.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
