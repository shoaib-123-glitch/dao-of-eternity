# Fresh PostgreSQL integration workflow

Prepared offline on 2026-10-24 on `feature/postgres-integration-safeguards`.
The previous schema `dao_it_ea92ae2483dd4ac494729f5628b429e2`, OID 16390,
is permanently retired. The operator reported ABSENCE_OBSERVED, exit 0 and
unchanged evidence. Historical rollback verification remains UNKNOWN. The
workflow records that report as an operator report, without rewriting history
or claiming a new live observation. Original recovery files, provisioning
receipts, inspection/retirement records, SQL batches and procedures are retained.

The new reservation and sealed batch are local files only. No fresh database
schema or actual OID exists as a result of preparation. No Docker, PostgreSQL,
SQL, permission changes, bot startup, commit, push, merge or deployment is part
of this offline preparation. Neither environment file is read.

Fresh schema reservation: `dao_it_8f601c52b5494c36adac957d4ad1454a`.
Bundle: `fresh_integration/operation-b2ffea0be1c24872a4338dfe5580f6bf`.
The bundle contains its new `.plan.json`, `seal.json` and `provision.psql`.
The actual namespace OID remains unassigned until authorized provisioning.

## Commands

Run these commands from the bot project directory. Each stage is one command.
The paths below select this exact sealed reservation.

Run every offline regression, Python syntax/manifest check, existing PowerShell
binding/argument check, new procedure parse and whitespace check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\validate_offline.ps1
```

`ExecutionPolicy Bypass` applies only to that validation process. It does not
change machine policy or database permissions.

Check the local seals at any time; this command makes no connection:

```powershell
python -B -E -s .\fresh_integration.py check --bundle '.\fresh_integration\operation-b2ffea0be1c24872a4338dfe5580f6bf'
```

After separate approval for read-only Docker/PostgreSQL verification:

```powershell
python -B -E -s .\fresh_integration.py verify --bundle '.\fresh_integration\operation-b2ffea0be1c24872a4338dfe5580f6bf' --allow-read-only-connection
```

This prompts for the independently recorded full original 64-character container
ID, database OID and existing administrator role. Use those existing identity
records; do not substitute the retired namespace OID for the database OID.
No container ID, database OID or administrator name is guessed during preparation.
psql prompts privately for the administrator password inside the container.
Verification stages a sealed file in a new private container `/tmp` directory,
then executes READ ONLY verification. It never reaches CREATE SCHEMA.

After verification succeeds, obtain separate approval for fresh provisioning:

```powershell
python -B -E -s .\fresh_integration.py provision --bundle '.\fresh_integration\operation-b2ffea0be1c24872a4338dfe5580f6bf' --allow-read-only-connection --allow-database-writes
```

This automatically reuses the verified identity pins, rechecks absence and all
privileges, creates exactly the reserved empty private schema, and records the
actual OID from PostgreSQL in new precommit and final receipts. It changes no
role, database or schema ACLs: unexpected default/public access fails and rolls
back. Database-level CREATE remains denied to `dao_test_user` throughout.
There is no DROP/recovery branch, IF NOT EXISTS, grant, revoke or automatic retry.

After provisioning reports PROVISIONED, obtain separate approval for the suite:

```powershell
python -B -E -s .\fresh_integration.py run --bundle '.\fresh_integration\operation-b2ffea0be1c24872a4338dfe5580f6bf' --allow-database-writes
```

This derives the actual namespace OID from the validated final receipt; no manual
OID transcription is needed. The test-role password is securely prompted by the
existing runner. The runner independently verifies the pinned forwarded target,
cluster, empty schema/OID, owner, ACLs and effective privileges before creating
the reviewed 27 tables. It runs exactly five production-backed cases:

| Case | Production behavior exercised |
| --- | --- |
| combat_companion | Combat action persistence, companion effects and duplicate handling |
| partnership_acceptance | Concurrent acceptance and reciprocal household/bond state |
| opposing_dual | Opposing requests, lock serialization and duplicate handling |
| rollback_retry | Real transaction rollback and explicit case retry demonstration |
| gathering_hall | Gathering history and sect hall actions under competing transactions |

The existing deterministic lock schedules remain intact. The rollback/retry case
does not establish production-wide automatic retry support. Setup and cleanup use
the corrected PostgreSQL 18 inventory handling: native OIDs are integers; consumed
nested JSONB OIDs also accept canonical decimal strings, while invalid values
still fail closed. Raw definitions remain unchanged for cleanup fingerprints.
Successful cleanup removes only the verified run's exact objects/schema using
the existing restrictive cleanup checks. The suite itself therefore needs write
approval even after empty-schema provisioning.

## Identity, sealing and record handling

Both verification and provisioning independently check the full container ID,
`/dao-postgres18-test`, running state, exactly one PostgreSQL TCP binding at
`127.0.0.1:5433`, internal `172.17.0.2:5432`, PostgreSQL 18.6, and control-file
cluster identifier `7699864005918920747`. They repeat identity checks immediately
before psql. SQL checks the same cluster/server pins, independently supplied
database OID, owner role OID 16389, no role elevation/memberships, CONNECT allowed,
database CREATE denied, no enabled event triggers or test-role sessions, and
continued absence of BOTH the retired name and retired OID.

The full canonical effective-privilege policy is applied under `dao_test_user`
before creation and afterward with the newly observed namespace OID. Its narrow
standard `pg_settings` exception remains identical to the established preflight.
Emptiness checks cover pg_class, namespace dependencies and extension placement.
Provisioning repeats these checks in its write transaction and after commit.
Use an exclusive operator window; checks cannot prevent a concurrent hostile
administrator from changing catalog state between observations.

The batch bytes must match their SHA-256 seal AND regenerated reviewed SQL.
The plan, manifest, scenario list, process/setup/cleanup code and production
handler source hashes must match the offline seal at every stage. Source drift
requires a separately reviewed new preparation, never editing a sealed bundle.

All operator records use new files with exclusive creation and fsync. An attempt
record is durable before the first Docker command or suite connection. The host
never copies over receipts: it captures raw container output into new local
files, then separately validates and records it. Even a nonzero psql exit attempts
to preserve precommit/final raw output before recording UNKNOWN. Lost responses,
missing/mismatched receipts, interrupted connections or failed local flushes do
not authorize a suite. Every stage is single-use, including failed attempts.

If a stage fails, preserve the bundle and its container directory. Do not delete
attempt files, rerun the stage, reseal receipts or recreate the schema. The
`*.staging.json` record identifies the exact container directory for a separately
approved read-only investigation. The workflow intentionally provides no automatic
repair or retry command. It never accesses `integration_recovery`; historical
directory permissions are left intact.

The bundle directory is Git-ignored because it will hold future local operator
evidence. Share source/procedure files independently of operator artifacts.

## Offline validation scope

Python validation discovers every test with network/process/database/startup
guards installed. Historical evidence and environment-file opens are blocked.
Only Windows asyncio's exact standard-library loopback socketpair is admitted
for internal event-loop wakeups; database and external connections remain denied.
New workflow tests use temporary synthetic files and mocked Docker/psql, covering
seals, retired-name refusal, archival compatibility, explicit authorization,
actual OID capture, receipt identity, uncertain failures, durability and single use.

SQL receives static policy, control-flow, statement and transaction-order checks;
it has not been parsed by PostgreSQL or executed. Actual Docker, TTY/password,
catalog and transaction behavior awaits the separately authorized stages above.

Validation result: 707 Python tests passed (668 existing and 39 fresh-workflow
regressions), without failures, errors, skips or guard violations. Existing
PowerShell validation passed 60 binding fixtures, 28 static consistency checks
and 12 native argument fixtures. The combined validation command also checks all
PowerShell script syntax, the commands above and tracked/new-file whitespace.
