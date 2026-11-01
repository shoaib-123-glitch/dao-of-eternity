# First PostgreSQL integration suite: design for review

The user reports a passing catalog-only preflight with `can_create_schema=false`.
This preparation performs no PostgreSQL connection, SQL execution, grants,
environment-file reads, bot startup or Git publication. Live execution remains
disabled by default. Database-backed cases are implemented but not run here.

## Permission model chosen before implementation

The current self-provisioning `IsolatedTestDatabase.create()` requires database
CREATE. It cannot work with the passing account's current privileges. Database
CREATE is broader than CREATE within one schema; database ownership or a
SECURITY DEFINER helper would also enlarge the authority under test.

Recommendation: retain **no database CREATE**, no elevated attributes or role
memberships, no external database/object access and all existing negative
privilege checks. Use a separately reviewed administrator of the identified
Docker container to provision exactly one fresh random schema owned by
`dao_test_user`. This is an out-of-band operation, not part of the runner; no
administrator connection or grant automation is implemented.

An offline plan reserves a `dao_it_<32 hex>` name and reviewed manifest digest.
The operator independently records the provisioned schema OID. The runner must
match that exact name/OID, role ownership, private ACL, empty namespace, Docker
endpoint and cluster identifier before writing any test table. A pre-existing
populated schema, changed OID, extra privileges or unexpected schema is rejected.
This uses the existing privilege policy's one verified run-namespace exception;
it does not ignore other owned schemas or object access. Only one run for this
role can be active: an unrelated leftover schema remains a safety rejection.

Schema ownership permits creation of the reviewed tables and removal of those
tables and that schema, without database CREATE. The runner keeps checking that
database CREATE remains absent and the server is PostgreSQL 18. Runtime requires
CONNECT to this database and ownership of this private schema; these cases need
no additional TEMP, role, parameter or other-database grant. Existing permissions
are not changed automatically. Schema setup and cleanup are necessarily
write-capable phases, separated from the catalog-only preflight. The existing
preflight is unchanged and still rejects owned run schemas outside a scoped run.
After verified cleanup, the original global policy can pass again.

Sources: [CREATE SCHEMA privileges](https://www.postgresql.org/docs/18/sql-createschema.html),
[schemas and privileges](https://www.postgresql.org/docs/18/ddl-schemas.html#DDL-SCHEMAS-PRIV).

The fixed target remains client `127.0.0.1:5433`, server `172.17.0.2:5432`, database
`dao_eternity_test`, session/effective role `dao_test_user`, and cluster identifier
`7699864005918920747`. Every setup, checkout and cleanup verifies that pin. No
production DSN, PG environment fallback, role switching or server discovery is
used. The password is prompted securely only after explicit write opt-in.

## Execution and recovery contract

No arguments means disabled: no credentials, plan files, network or SQL are touched.
An explicit offline `prepare` action creates only a local immutable plan.
A later `run` requires that plan, a positive independently recorded namespace
OID and explicit `--allow-database-writes`. No run is performed in this task.

A fresh exclusive recovery journal precedes connecting. Setup adopts only an
empty namespace, creates only manifest tables in that namespace and records OIDs
and dependent-object fingerprints. Each checkout starts read-only and verifies
identity, privileges, namespace, and exclusive search path before enabling
writes. It never calls Repository.connect(), app setup/backfills or bot startup.

Cleanup requires no active checkouts, exact owner/name/OID/ACL, the unchanged
object/dependency inventory and table locks. It drops only the recorded tables
and schema with RESTRICT. There is no CASCADE, prefix cleanup, retry of uncertain
DDL commits, reuse of a populated schema, or automatic recovery command. Failed
setup leaves the administrator-provisioned namespace and a journal for review;
uncertain disposal or drift suppresses destructive cleanup. The plan and journal
must be retained together for any manual recovery review.

Setup now reports fixed, sanitized operation diagnostics and separates driver
rollback acknowledgement from verification of server state. Existing journals
are not migrated. The separate disabled-by-default, catalog-only recovery
inspector and proposed operator command are documented in
[POSTGRES_RECOVERY_INSPECTION_REVIEW.md](POSTGRES_RECOVERY_INSPECTION_REVIEW.md).
That inspector performs no setup, cleanup, retries or evidence updates.

## Database-backed scenarios

Cases will call real gameplay handlers through verified harness acquisitions,
with independent PostgreSQL connections. Instrumentation can pause around real
queries, but it does not replace database locks, transactions or query results.
Blocking is established using pg_blocking_pids, with bounded deadlines rather
than sleep duration as evidence. All case-created data stays inside manifest
tables in the one scoped namespace.

1. Combat locking and companion progression: retain actual companion contention
   during battle setup. An independent transaction then holds the combat-session
   row while production actions reach their locking reads. Prove each action is
   blocked before releasing that transaction, then check persisted progression,
   concurrent turns, victory state and exactly-once duplicate victory rewards.
2. Partnership acceptance: shared proposer, shared recipient, opposing invites,
   and absent/pre-existing household rows; exactly one active partner and action
   record, with saved response on duplicate interaction. Record and assert the
   complete ascending household locking-read order for both requests.
3. Opposing dual sessions: prove household contention, one accepted request,
   one cooldown rejection, one boon per player and consistent persisted cooldowns;
   both requests must complete their household locks in global ID order.
4. Rollback/retry: fail after a real inventory update, prove transaction rollback
   of resources/actions, explicitly call the real handler again with the same
   interaction and verify idempotency. Separately demonstrate a PostgreSQL
   serializable conflict and a test-controlled complete-transaction retry using
   fixture SQL. That demonstration does not exercise production retry machinery.
5. Gathering Hall: real upgrade history and repository settlement across upgrade
   and join boundaries, fractional Qi, overflow and replay/zero-period behavior.
   Only the Python clock is fixed for deterministic economy expectations.

Neither the gameplay handlers nor the runner implement automatic serialization
retries. The handler retry above is explicitly initiated by the test. An unexpected
database error, deadlock, timeout or incomplete request fails its case. PostgreSQL
requires retrying the complete transaction after
a serialization failure: [serialization failure handling](https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html).

Offline tests validate the opt-in gates, plan integrity, prepared-namespace
adoption, forwarding pins, refusal paths, journal and cleanup behavior. All
existing offline tests remain required. They do not establish that the new
database-backed cases pass against PostgreSQL.

## Prepared operator workflow; no live steps performed

Use an existing trusted Python environment with the project's dependencies.
Run from `dao_of_eternity_bot`. The following are review templates, not steps
executed in this task. The recovery directory must already exist and should be
private; the existing Git-ignored `integration_recovery/` location is suitable.

```powershell
$daoPython = 'C:\path\to\existing\venv\Scripts\python.exe'
$daoRecovery = 'C:\path\to\existing\private\integration_recovery'

# Disabled by default; exit 2, with no plan/credential/database access.
& $daoPython -B -E -s .\integration_runner.py

# Explicit OFFLINE action: writes one local .plan.json file, no PostgreSQL access.
& $daoPython -B -E -s .\integration_runner.py prepare --recovery-directory $daoRecovery
```

The plan contains only the reserved random name, version, reviewed DDL digest and
fixed nonsecret Docker target. It contains no password, DSN, DDL or SQL. No plan
or recovery directory is silently created by default invocation.

After a separate review of administrator provisioning, the operator uses the
identified `dao-postgres18-test` container to provision **only** the reserved
empty schema with AUTHORIZATION dao_test_user. This is not a database ownership
change and does not require granting database CREATE to the test role. Confirm
the schema's exact independent OID, ownership and private ACL in the correct
database/cluster. Do not use public, reuse a populated namespace, add memberships,
introduce SECURITY DEFINER helpers, change grants or disable any safety gate.
No administrative tool, account or SQL command is automated by the runner.

Only after that provisioning and write execution are separately authorized,
set `$daoPlan` to the generated plan's absolute path and `$daoNamespaceOid` to
the independently verified OID, then use the explicit writing command:

```powershell
& $daoPython -B -E -s .\integration_runner.py run --plan $daoPlan --namespace-oid $daoNamespaceOid --allow-database-writes
$LASTEXITCODE
```

Without the final opt-in flag, run returns disabled before reading the plan or
prompting for credentials. A securely masked test-user password is requested
only for the enabled run; an unavailable no-echo console is rejected rather
than falling back to echoed input. No environment file or inherited password is
used. Exact client destination, internal listener and cluster pins cannot be
overridden by command-line options.

Successful output contains only fixed case labels, Boolean results, a fixed
status and `cleanup_verified=true`; no rows, driver/assertion errors or secrets
are printed. A case failure stops subsequent cases, attempts only the original
verified cleanup and returns failure. For run, exit 0 means all five cases passed
and cleanup completed; 1 is rejection/case/setup/disposal failure, 2 is disabled
or invalid command usage, 130 is cancellation. Successful offline prepare also
returns 0, with PLAN_PREPARED rather than a suite result. A cleanup error never
becomes a successful result. Keep
both plan and journal after any failure; do not replay or perform prefix cleanup.

## Implementation and offline validation

Changed files for this stage:

| File | Change |
| --- | --- |
| `integration_catalog.py` | One fixed empty-namespace SELECT for prepared setup; existing seven SQL strings unchanged |
| `integration_support.py` | Explicit empty-schema adoption without database CREATE; pinned verification through checkout/cleanup; existing manifest, inventory, recovery and RESTRICT cleanup reused |
| `integration_runner.py` | Offline plan preparation, strict target/plan/OID gates, secure password prompt, explicit write opt-in, fixed case execution and sanitized results |
| `postgres_cases.py` | Five real PostgreSQL cases; real handler calls and transactions, observed blocking, actual rollback/serialization failures and deterministic Hall clock |
| `test_postgres_suite_safety.py` | Thirty offline regressions; database-backed case bodies are not executed |
| `POSTGRES_INTEGRATION_PLAN.md` | Updated current status and link to this review |
| `POSTGRES_INTEGRATION_SUITE_REVIEW.md` | Permission model, execution boundary, coverage and operator procedure |

Initial suite validation: **450 offline tests passed**, including **30 new tests**, with zero
failures or skips. Changed Python files passed AST parsing; Git and explicit
whitespace checks passed. Environment-file reads, bot/config imports, external
network access and subprocess launches were blocked during the offline suite.
Only asyncio's synchronous internal socket-pair setup was permitted. No live
writing case, PostgreSQL connection, SQL or preflight was executed.

Live behavior remains unverified: schema/table setup and inventory compatibility,
actual lock observation, transaction behavior and the five gameplay cases must
be reviewed and then validated in the isolated container. The passing catalog-only
report is not evidence that these write-capable cases have passed. The model
trusts the local OS/Docker administrator and excludes concurrent administrative
DDL; it does not sandbox malicious Python code or a malicious database owner.

## Concurrency review corrections

This focused update changes only `postgres_cases.py`, the new
`test_postgres_concurrency_schedules.py`, and this review document. The five case
names, production gameplay files, runner authorization, cluster pins, privilege
checks, prepared-schema policy and cleanup infrastructure remain intact.

Both relationship schedules now record each household locking read only after
the underlying production query completes. Every prefix and the final sequence
must match the required ascending user IDs, separately for the winner and the
loser. A loser rejected before acquiring both locks cannot pass. Memory-only
actor-first mutations of either request fail for partnership and dual acceptance;
the old accidental serialization can no longer hide the reversed order.

Combat schedules use an independent verified connection and transaction to lock
the existing session row. Before/after hooks require every production action to
enter its exact locking read without completing it. A separately verified
observer must establish a blocking path to that holder before release. The proof
allows only the known competing action sessions as intermediate queue waiters:
PostgreSQL can report a waiter ahead in the queue as a blocker, as described in
the [pg_blocking_pids documentation](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-INFO-SESSION).
An unrelated blocker or a cycle without the holder is insufficient. Blocking
only at a later UPDATE, a missing lock query, or an already completed locking
read is rejected. Poll intervals do not establish success; actual lock-state
evidence does. Failure and cancellation drain owned workers before returning.

After release, the live case checks companion stage/bond, combat turn state,
unchanged cultivation state, two preserved turns, persisted victory, duplicate
responses, one action record and exactly-once loot. The original companion
contention test and all existing safety regressions are retained.

Current validation: **475 offline unittest tests passed**, comprising the existing
450 and **25 new concurrency-schedule tests**, with zero failures, errors or skips.
New tests exercise the real production handlers against Python transaction-lock
doubles; no database-backed case body is invoked. They cover both request orders,
removed/bypassed combat locks, later-UPDATE false evidence, valid waiter queues,
unrelated/cyclic blockers, deadlocks, deadlines, incomplete requests and cancellation.
Both changed Python files passed AST parsing and compilation. Git whitespace
checks cover tracked changes and the three untracked update files explicitly.

The offline run blocked real PostgreSQL driver calls, environment-file access,
bot/config imports, network access and subprocess launches, with zero guard
violations. Only synchronous internal socket-pair setup was permitted. Windows
platform introspection was supplied locally to prevent its shell fallback during
dependency import. No PostgreSQL connection, SQL execution, schema creation,
database-backed test, bot startup or Git publication was performed.

Remaining limitations: lock evidence is synthetic in the offline tests. Actual
PostgreSQL 18.6 queueing, statement deadlines, setup/inventory compatibility,
cleanup and all five database-backed scenarios still require separately reviewed
execution in the pinned Docker cluster. Automatic production serialization retry
remains unimplemented and unverified. Stop for review before any live operation.
