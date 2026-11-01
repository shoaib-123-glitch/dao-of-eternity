# PostgreSQL 18 Docker catalog preflight: launch procedure

Prepared for later manual execution on `feature/postgres-integration-safeguards`.
The launcher has not been executed against PostgreSQL as part of this work.

For the user-reported PRIVILEGES rejection, a separate opt-in diagnostic is
prepared in the [privilege diagnostic review](POSTGRES_PRIVILEGE_DIAGNOSTIC_REVIEW.md).
It adds fixed stage/Boolean output from existing checks without extra SQL or a
weaker policy. The corresponding future invocation adds `-PrivilegeDiagnostics`
to the Docker command below. It has not been run live as part of this update.

Offline validation: **398 tests passed**, including seven new Docker launcher
regressions for the complete pin, client configuration, mode arguments, exact
internal address, report confirmation, successful forwarding and unexpected
server identity. Real preflight paths were exercised with mocked driver calls
only. Environment-file reads, runtime bot/config imports and external network
access were blocked. PowerShell syntax parsing and whitespace checks also passed.
No SQL, server/permission changes, bot startup or Git publication occurred.

## Reviewed target

| Setting | Pinned value |
| --- | --- |
| Intended Docker container | `dao-postgres18-test` |
| Client destination | `127.0.0.1:5433` |
| Internal PostgreSQL listener port | `5432` |
| Database | `dao_eternity_test` |
| Effective and session role | `dao_test_user` |
| PostgreSQL cluster system identifier | `7699864005918920747` (exact integer) |
| Internal server IPv4 address | `172.17.0.2`, independently obtained by the user using Docker inspection |

The container name identifies the intended target for the operator. The launcher
does not discover a container or trust its name as server identity. It uses the
existing strict `ExpectedServerIdentity` policy to check the exact internal
address, port and independently pinned cluster identifier returned by PostgreSQL.
The client destination and actual database, effective role and session role
remain strictly checked. No server setting or permission is changed.

## Exact PowerShell invocation

From any PowerShell directory, after review and when execution is intended:

```powershell
powershell.exe -NoProfile -File 'C:\Users\Mr. Qureshi\Downloads\dao_of_eternity_v022_golden_bonds\dao_of_eternity_v022\dao_of_eternity_bot\catalog_preflight.ps1' -DockerForwarding
$LASTEXITCODE
```

Run the script as shown; do not dot-source it. The `-DockerForwarding` switch is
required to select this reviewed mapping. Without it, the existing direct
connection policy remains in effect and still rejects `5433 -> 5432`.

The script's prompts, in order, are:

1. **Existing virtual environment directory**, only if `VIRTUAL_ENV` is unset.
   Enter the absolute path to an existing trusted environment containing
   `pyvenv.cfg`, `Scripts\python.exe`, and the project's installed dependencies.
   Do not enter surrounding quote characters at this prompt. The script verifies
   both the files and Python's virtual-environment identity. It does not activate
   an environment, install packages, create an environment, or fall back to system
   Python.
2. **Password for dao_test_user in Docker container dao-postgres18-test**.
   Enter the test-user password at this masked SecureString prompt. Do not put
   it in the command, a file, shell history or chat. The script requires a nonempty
   password and never reuses an inherited `DAO_TEST_PASSWORD` as a substitute.

There is no address or port prompt in Docker mode. The internal address, client
port, internal port, database, user and cluster identifier are fixed to the
reviewed values. The identifier is
an integer literal in embedded Python; it is never rounded through a PowerShell
floating-point conversion. Extra mode arguments or a changed client target
reject before any connection. There is no internal address, port or cluster-pin
override argument. Recreated containers or changed network/volume identities
require independent review and an explicit pin update; there is no automatic
fallback or discovery.

## Behavior and expected result

The launcher supplies all five DAO_TEST connection variables only in its child
process environment. It never loads `.env` or `.env.test`. The secure password is
converted only for the driver's required process environment; it is excluded
from arguments and output. The existing `finally` restores/removes the temporary
variables, zeroes the unmanaged password buffer, disposes the SecureString and
restores the working directory. Plaintext necessarily exists briefly in process
memory/environment for authentication.

Only `preflight_catalog(config, allow_connection=True, expected_server=...)`
is called. That API starts read-only, issues fixed catalog/control metadata
SELECTs, verifies the reviewed endpoint and cluster plus all existing safety
gates, returns a sanitized report, and closes the connection. No arbitrary SQL,
schema creation, cleanup, integration-test writes or Discord bot are invoked.
The PostgreSQL 18 check runs after connection closure. Docker mode additionally
requires the completed report to confirm client port `5433`, server port `5432`
and a verified cluster pin before printing success.

A successful JSON report includes:

```json
{
  "status": "PASSED",
  "server_major": 18,
  "database": "dao_eternity_test",
  "role": "dao_test_user",
  "host": "127.0.0.1",
  "port": 5433,
  "server_port": 5432,
  "cluster_identity_verified": true,
  "read_only": true,
  "effective_privileges_verified": true
}
```

The report also contains the verified `can_create_schema` Boolean; reporting it
does not create a schema. Cluster identifiers, raw metadata, passwords and driver
errors are excluded from the output.

`$LASTEXITCODE` is authoritative: `0` passed, `1` rejected, `2` setup or local
disposal failure, `130` cancelled. Any rejection stops this attempt without an
alternate endpoint, role, server identity or permission change. Permission
denial on the cluster metadata read remains a rejection; this procedure never
grants access or retries as another role.

Stop before executing the command until this procedure has been reviewed.
