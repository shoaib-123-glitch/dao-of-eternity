"""Reviewed operator workflow. Native Docker calls require explicit authorization.

No asyncpg connection, SQL execution API, environment-file reader or evidence
writer exists here. psql runs only inside the independently pinned container.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import uuid4


SCHEMA = 'dao_it_ea92ae2483dd4ac494729f5628b429e2'
PROJECT = Path(__file__).resolve().parent
# Normalized source seal; raw staged bytes must also match their own SHA-256.
BATCH_SOURCE_SHA256 = '202c1410d86a5fe1f87295abe6764ee733b7c83ab94c677f31945e4dd72bbf4e'
INSPECT_MARKER = 'RETIREMENT_INSPECT_PASSED'
RETIRE_MARKER = 'RETIREMENT_ABSENCE_OBSERVED_PRESERVE_ALL_ORIGINAL_EVIDENCE'


class OperatorRejected(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__('Operator procedure rejected; details suppressed.')


def reject(category):
    raise OperatorRejected(category) from None


def policy_guard():
    """Static build/check only. Reuse every canonical preflight privilege flag."""
    from integration_catalog import PRIVILEGES_SQL, PRIVILEGE_FLAGS, CATALOG_WRITES_SQL
    flags = PRIVILEGES_SQL.strip().replace('$1', '16390')
    writes = CATALOG_WRITES_SQL.strip().replace('$1', '16390')
    denied = '\n AND '.join('f.' + name + ' IS FALSE' for name in PRIVILEGE_FLAGS if name != 'system_catalog_writes')
    return """-- BEGIN DAO EFFECTIVE POLICY
SET LOCAL ROLE dao_test_user;
WITH flags AS (
%s
), writes AS (
%s
)
SELECT COALESCE((SELECT
 current_user = 'dao_test_user'
 AND pg_catalog.has_schema_privilege(current_user,16390::oid,'USAGE')
 AND pg_catalog.has_schema_privilege(current_user,16390::oid,'CREATE')
 AND %s
 AND CASE WHEN f.system_catalog_writes IS FALSE THEN true
  WHEN f.system_catalog_writes IS TRUE THEN
   (SELECT count(*) = 1 AND COALESCE(bool_and(
    w.oid > 0 AND w.oid < 16384 AND w.schema = 'pg_catalog'
    AND w.name = 'pg_settings' AND w.kind = 'v'
    AND w.is_pg_settings IS TRUE AND w.owned IS FALSE
    AND w.other_table_access IS FALSE AND w.other_column_access IS FALSE
    AND w.table_update IS TRUE AND w.update_acl_safe IS TRUE
    AND w.explicit_column_update IS FALSE),false) FROM writes w)
  ELSE false END FROM flags f),false) AS safe
\\gset dao_effective_
\\if :dao_effective_safe
\\else
  DO $$ BEGIN RAISE EXCEPTION 'RETIREMENT_EFFECTIVE_PRIVILEGES_REJECTED'; END; $$;
\\endif
RESET ROLE;
-- END DAO EFFECTIVE POLICY
""" % (flags, writes, denied)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            reject('JSON_FORMAT')
        result[key] = value
    return result


def _json(text):
    if type(text) is not str or not 1 <= len(text) <= 65536:
        reject('JSON_FORMAT')
    return json.loads(text, object_pairs_hook=_object, parse_constant=lambda _: reject('JSON_FORMAT'))


def _plain_path(path):
    path = Path(path).absolute()
    if any(part == '.env' or part.startswith('.env.') for part in path.parts):
        reject('FILE_FORMAT')
    for part in (path, *path.parents):
        if not part.exists():
            continue
        info = part.lstat()
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            reject('FILE_REPARSE')
    return path


def _read(path):
    path = _plain_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 65536:
        reject('FILE_FORMAT')
    with path.open('rb') as stream:
        data = stream.read(65537)
    if not 1 <= len(data) <= 65536:
        reject('FILE_FORMAT')
    return data


def _write_new(path, record):
    path = _plain_path(path)
    data = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode('utf-8')
    # No replace, truncate, chmod or unlink, even after partial writes/failures.
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha(data)


def _new_run(root):
    root = _plain_path(root)
    root.mkdir(exist_ok=True)  # Parent must exist and already permit normal writes.
    run = root / ('operation-' + uuid4().hex)
    run.mkdir()  # Exclusive, never reuse another operation's directory.
    return run


def _evidence(root, original_id):
    from integration_inspector import read_evidence, EvidenceValidationError
    try:
        evidence = read_evidence(Path(root) / (SCHEMA + '.plan.json'), 16390, 16389, original_id)
    except EvidenceValidationError as error:
        reject('EVIDENCE_' + error.preconnection_code.value)
    if evidence.phase != 'creating_tables' or evidence.outcome != 'rollback_unverified':
        reject('HISTORICAL_STATE')
    journal = _json(evidence.snapshots[1][1].decode('utf-8-sig'))
    if journal['table_oids'] != [] or journal['inventory_digest'] is not None:
        reject('HISTORICAL_STATE')
    return evidence


def _hashes(evidence):
    return {path.name: _sha(data) for path, data in evidence.snapshots}


def _batch():
    path = PROJECT / 'retire_empty_schema.psql'
    data = _read(path)
    text = data.decode('utf-8-sig').replace('\r\n', '\n')
    if _sha(text.encode()) != BATCH_SOURCE_SHA256:
        reject('BATCH_SEAL')
    if text.count(policy_guard()) != 2:
        reject('POLICY_DRIFT')
    return path, _sha(data)


def _native(args, *, interactive=False):
    if interactive:
        # Inherit the real console: psql -W reads its password without echo.
        return subprocess.run(['docker', *args], check=False).returncode
    process = subprocess.run(['docker', *args], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, check=False, timeout=20)
    if process.returncode != 0:
        reject('DOCKER_COMMAND')
    if type(process.stdout) is not str or len(process.stdout) > 65536:
        reject('DOCKER_OUTPUT')
    return process.stdout


def validate_docker_facts(text, original_id):
    facts = _json(text)
    if (type(facts) is not dict or set(facts) != {'id', 'name', 'running', 'ports', 'networks'}
            or facts['id'] != original_id or facts['name'] != '/dao-postgres18-test'
            or facts['running'] is not True):
        reject('CONTAINER_IDENTITY')
    ports = facts['ports']
    if type(ports) is not dict:
        reject('PORT_BINDING')
    for key, bindings in ports.items():
        if not re.fullmatch(r'[1-9][0-9]{0,4}/(tcp|udp|sctp)', key) or int(key.split('/')[0]) > 65535:
            reject('PORT_BINDING')
        if bindings is not None and (type(bindings) is not list or any(type(b) is not dict for b in bindings)):
            reject('PORT_BINDING')
        for binding in bindings or []:
            if (set(binding) != {'HostIp', 'HostPort'} or type(binding['HostIp']) is not str
                    or type(binding['HostPort']) is not str
                    or not re.fullmatch('[1-9][0-9]{0,4}', binding['HostPort'])
                    or int(binding['HostPort']) > 65535):
                reject('PORT_BINDING')
        if key.startswith('5432/') and key != '5432/tcp' and bindings:
            reject('PORT_BINDING')
    if ports.get('5432/tcp') != [{'HostIp': '127.0.0.1', 'HostPort': '5433'}]:
        reject('PORT_BINDING')
    networks = facts['networks']
    if type(networks) is not dict or len(networks) != 1:
        reject('INTERNAL_ADDRESS')
    network = next(iter(networks.values()))
    if (type(network) is not dict or network.get('IPAddress') != '172.17.0.2'
            or network.get('GlobalIPv6Address', '') != ''):
        reject('INTERNAL_ADDRESS')


def _docker_identity(original_id):
    # Select only noncredential facts, never .Config.Env or the full inspection.
    fmt = ('{"id":{{json .Id}},"name":{{json .Name}},"running":{{json .State.Running}},'
           '"ports":{{json .NetworkSettings.Ports}},"networks":{{json .NetworkSettings.Networks}}}')
    facts = _native(['inspect', '--type', 'container', '--format', fmt, 'dao-postgres18-test'])
    validate_docker_facts(facts, original_id)
    version = _native(['exec', '--user', 'postgres', '--env', 'LC_ALL=C', original_id, 'postgres', '--version'])
    if not re.fullmatch(r'postgres \(PostgreSQL\) 18\.6(?: \([^\r\n]*\))?\r?\n?', version):
        reject('POSTGRES_VERSION')
    control = _native(['exec', '--user', 'postgres', '--env', 'LC_ALL=C', original_id, 'pg_controldata'])
    identifiers = re.findall(r'^Database system identifier:[ \t]+([0-9]+)[ \t]*\r?$', control, re.M)
    if identifiers != ['7699864005918920747']:
        reject('CLUSTER_IDENTITY')


def _stage(original_id, batch_path, digest):
    directory = '/tmp/' + SCHEMA + '.retirement-' + uuid4().hex
    # mkdir is the atomic exclusive reservation, not test-then-copy to a reused path.
    _native(['exec', '--user', 'postgres', original_id, 'mkdir', '-m', '0700', directory])
    path = directory + '/retire_empty_schema.psql'
    _native(['cp', '--', str(batch_path), original_id + ':' + path])
    line = _native(['exec', '--user', 'postgres', original_id, 'sha256sum', path])
    if line.rstrip('\r\n') != digest + '  ' + path:
        reject('STAGED_HASH')
    return path, directory


def _psql_args(original_id, admin, database_oid, batch_path, marker_path, action):
    return ['exec', '--interactive', '--tty', '--user', 'postgres', original_id,
        'env', '-u', 'PGSERVICE', '-u', 'PGSERVICEFILE', '-u', 'PGSYSCONFDIR', '-u', 'PGOPTIONS',
        '-u', 'PGPASSWORD', '-u', 'PGPASSFILE', 'PGHOSTADDR=172.17.0.2',
        'PGOPTIONS=-c default_transaction_read_only=on',
        'psql', '-X', '-W', '-q', '--host=172.17.0.2', '--port=5432',
        '--username=' + admin, '--dbname=dao_eternity_test', '--set=VERBOSITY=terse',
        '--set=SHOW_CONTEXT=never', '--set=dao_admin_role=' + admin,
        '--set=dao_database_oid=' + str(database_oid), '--set=dao_container_id=' + original_id,
        '--set=dao_original_container_id=' + original_id, '--set=dao_action=' + action,
        '--output=' + marker_path, '--file=' + batch_path]


def _prior_inspection(path, snapshot, token):
    path = _plain_path(path)
    if path.name != 'inspect.result.json':
        reject('INSPECTION_PROOF')
    proof = _json(_read(path).decode('utf-8'))
    expected = {'version': 1, 'action': 'inspect', 'outcome': 'INSPECT_PASSED',
                'snapshot_sha256': snapshot, 'inspection_token_sha256': _sha(token.encode())}
    if (type(proof) is not dict or set(proof) != set(expected) or type(proof['version']) is not int
            or any(proof.get(k) != v for k, v in expected.items())):
        reject('INSPECTION_PROOF')
    if _sha(_read(path.parent / 'evidence.json')) != snapshot:
        reject('INSPECTION_PROOF')
    return path.parent


def operate(*, action='inspect', allow_read_only_connection=False, authorize_retirement=False,
            original_container_id='', admin_role='', recovery_directory=None,
            record_directory=None, inspection_record=None, inspection_token=None):
    if action not in ('inspect', 'retire') or allow_read_only_connection is not True:
        reject('DISABLED')
    if action == 'retire' and (authorize_retirement is not True or inspection_record is None
            or type(inspection_token) is not str or not re.fullmatch('[0-9a-f]{64}', inspection_token)):
        reject('RETIREMENT_NOT_AUTHORIZED')
    if (type(original_container_id) is not str or not re.fullmatch('[0-9a-f]{64}', original_container_id)
            or type(admin_role) is not str or not re.fullmatch('[a-z_][a-z0-9_]{0,62}', admin_role)
            or admin_role == 'dao_test_user'):
        reject('INPUT')
    stage, run, attempted = 'EVIDENCE', None, False
    snapshot = None
    try:
        evidence = _evidence(recovery_directory, original_container_id)
        batch_path, digest = _batch()
        # Deterministic immutable contents allow later inspection proof matching.
        snapshot = {'version': 1, 'schema': SCHEMA, 'namespace_oid': 16390, 'owner_oid': 16389,
            'database_oid': evidence.database_oid, 'container_id': original_container_id,
            'cluster_identifier': '7699864005918920747', 'batch_sha256': digest,
            'files': _hashes(evidence)}
        snapshot_bytes = (json.dumps(snapshot, sort_keys=True, indent=2) + '\n').encode()
        snapshot_sha = _sha(snapshot_bytes)
        prior = _prior_inspection(inspection_record, snapshot_sha, inspection_token) if action == 'retire' else None
        stage = 'RECORD_CREATION'
        run = _new_run(record_directory)
        print(json.dumps({'operator_record_directory': str(run)}), flush=True)
        _write_new(run / 'evidence.json', snapshot)
        print(json.dumps({'evidence_files_readable': 4,
                          'database_oid': evidence.database_oid, 'owner_oid': evidence.owner_oid}), flush=True)
        if prior is not None:
            _write_new(prior / 'retirement.authorization.json',
                       {'version': 1, 'operation': str(run), 'outcome': 'UNKNOWN'})
        stage = 'DOCKER_IDENTITY'
        _docker_identity(original_container_id)
        evidence.assert_unchanged()
        stage = 'STAGING'
        staged, staging_dir = _stage(original_container_id, batch_path, digest)
        marker = staging_dir + '/' + action + '.status'
        _native(['exec', '--user', 'postgres', original_container_id, 'test', '!', '-e', marker])
        # Reverify after staging and immediately before an authorized connection.
        stage = 'DOCKER_IDENTITY_RECHECK'
        _docker_identity(original_container_id)
        evidence.assert_unchanged()
        stage = 'ATTEMPT_RECORD'
        _write_new(run / (action + '.attempt.json'), {'version': 1, 'action': action,
            'outcome': 'UNKNOWN', 'created_utc': datetime.now(timezone.utc).isoformat(),
            'snapshot_sha256': snapshot_sha, 'container_batch': staged, 'container_status': marker,
            'administrator': admin_role})
        stage = 'PSQL'
        attempted = True
        exit_code = _native(_psql_args(original_container_id, admin_role, evidence.database_oid,
                                       staged, marker, action), interactive=True)
        if exit_code != 0:
            reject('PSQL_EXIT')
        stage = 'COMPLETION_MARKER'
        expected = INSPECT_MARKER if action == 'inspect' else RETIRE_MARKER
        observed = _native(['exec', '--user', 'postgres', original_container_id, 'cat', '--', marker])
        if observed not in (expected + '\n', expected + '\r\n'):
            reject('COMPLETION_MARKER')
        stage = 'EVIDENCE_RECHECK'
        evidence.assert_unchanged()
        stage = 'RESULT_RECORD'
        outcome = 'INSPECT_PASSED' if action == 'inspect' else 'ABSENCE_OBSERVED'
        result_record = {'version': 1, 'action': action, 'outcome': outcome, 'snapshot_sha256': snapshot_sha}
        token = uuid4().hex + uuid4().hex if action == 'inspect' else None
        if token is not None:
            result_record['inspection_token_sha256'] = _sha(token.encode())
        _write_new(run / (action + '.result.json'), result_record)
        result = {'status': outcome, 'evidence_unchanged': True, 'record_directory': str(run),
                  'historical_rollback_verification': 'UNKNOWN'}
        if token is not None:
            # Released only after fsync succeeds: a partially persisted success
            # record alone can never authorize a future retirement invocation.
            result['inspection_token'] = token
        return result
    except BaseException as error:
        if run is not None and stage != 'RESULT_RECORD':
            try:
                _write_new(run / (action + '.result.json'), {'version': 1, 'action': action,
                    'outcome': 'UNKNOWN', 'stage': stage, 'connection_attempted': attempted})
            except BaseException:
                pass  # Never repair/delete a partial or existing record.
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(error, OperatorRejected):
            category = error.category
        elif isinstance(error, PermissionError):
            category = 'FILE_ACCESS_DENIED'
        elif isinstance(error, FileExistsError):
            category = 'RECORD_OR_PATH_EXISTS'
        elif isinstance(error, subprocess.TimeoutExpired):
            category = 'DOCKER_TIMEOUT'
        elif isinstance(error, OSError):
            category = 'FILE_OR_PROCESS_IO'
        else:
            category = 'VALIDATION'
        failure = OperatorRejected(category)
        failure.stage = stage
        raise failure from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Disabled-by-default historical-schema operator')
    parser.add_argument('--action', choices=('inspect', 'retire'), default='inspect')
    parser.add_argument('--allow-read-only-connection', action='store_true')
    parser.add_argument('--authorize-retirement', action='store_true')
    parser.add_argument('--original-container-id', default='')
    parser.add_argument('--admin-role', default='')
    parser.add_argument('--recovery-directory', default=str(PROJECT / 'integration_recovery'))
    parser.add_argument('--record-directory', default=str(PROJECT / 'integration_recovery' / 'operator_records'))
    parser.add_argument('--inspection-record')
    parser.add_argument('--inspection-token')
    args = vars(parser.parse_args(argv))
    try:
        result = operate(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print('RETIREMENT OPERATOR CANCELLED [UNKNOWN]; preserve records; no retry.')
        return 130
    except OperatorRejected as error:
        print(json.dumps({'status': 'REJECTED', 'category': error.category,
                          'stage': getattr(error, 'stage', 'AUTHORIZATION')}))
        return 2 if error.category == 'DISABLED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
