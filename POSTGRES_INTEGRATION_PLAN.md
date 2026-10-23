# PostgreSQL integration safeguards: offline hardening stage

Base: `develop` at `6bb30d7`, including persistence fix `17a34c4`.
Branch: `feature/postgres-integration-safeguards`.

This update implements safeguards and mocked security tests. It does not add a
gameplay integration runner or database-writing test cases. No PostgreSQL
connection, bot startup, production access, grant changes, or database writes
were performed. Neither environment file was opened. Connecting and performing
database writes remain separate approval stages.

## Follow-up to the independent review of 851ad0a

All four findings were reproduced with offline regressions before changing the
implementation (two assertion failures and two safety rejections). The update:

| Finding | Resolution | Regression evidence |
| --- | --- | --- |
| Normal PUBLIC UPDATE on pg_settings rejected every standard server | Keep the broad catalog-write gate; inspect its matching objects and effective ACL provenance; allow only the built-in view's standard PUBLIC UPDATE | Normal metadata passes; other objects, ownership, non-UPDATE rights, explicit column UPDATE, additional effective UPDATE grants, missing metadata and parameter privileges reject |
| PostgreSQL 18 NOT NULL catalog rows rejected | Validate structured constraint types and column numbers against a reviewed column/PK/CHECK manifest; require PG18 NOT NULL rows and retain them in fingerprints | Fixtures for PG15-18; missing/duplicate/wrong-column/unenforced/foreign constraints reject; NOT NULL drift prevents DROP |
| Checkout yielded after another session failed during SET | Recheck ready state after awaited write enabling, with no await before yield | An event-controlled SET pause lets a peer mark the run failed; the checkout rejects, closes and releases bookkeeping |
| Failed close left the control connection open | Separate shared connection disposal from schema cleanup; failed close disconnects and reports that objects remain | Simultaneous shutdown/cancellation, termination fallback/failure, preserved recovery metadata and zero SQL during disposal |

These are offline control-flow and metadata-fixture results. Real PostgreSQL 18
catalog execution and transaction behavior still need separately approved tests.

## Seven findings and their remedies

| Review finding | Hardening | Offline regression |
| --- | --- | --- |
| Effective PUBLIC/direct/inherited access was not checked | Fixed catalog queries inspect effective permissions, applicable default ACLs, and created-object ACLs; unknown results reject the run | Effective-access flags, missing/NULL metadata, catalog errors, unsafe created ACLs and dependencies all reject |
| Raw read-only connection could be repurposed | `preflight_catalog` executes only fixed catalog SELECTs, returns an immutable sanitized report, and closes its connection | No public raw-connection API; report fields, read-only startup, closure on success/failure/cancellation |
| Cleanup could race with acquisition or another cleanup | State becomes `closing` before the first await; acquisitions refuse it; callers join one worker | Paused cleanup refuses acquisitions; simultaneous callers perform one cleanup |
| Cancellation could leak a checkout or lose evidence | Finalizers release accounting; interrupted close terminates synchronously; shared cleanup is drained before caller cancellation is re-raised | Body/setup/connect/close/worker cancellation, repeated cancellation, late connections, failed termination |
| Permissive SQL matching admitted executable statements | Static reviewed manifest of 27 table bodies; literal source hashes must match before connecting; game modules are not imported for DDL | CTAS, SELECT INTO, external foreign keys, function defaults, INHERITS, DO, extra statements and nonliteral source reject |
| Partial setup could lose ownership of committed objects | Exclusive local journal precedes connecting; schema/OIDs/owner/digest and conservative transaction outcomes survive failures | DDL failure, uncertain commit, post-commit reset failure, journal failures before connection and after commit |
| Cleanup omitted automatic dependents | Inventory includes object addresses, definitions, ACLs and incoming dependencies; compare again after locks | Replaced tables, extra indexes/triggers/rules/defaults/columns/types/policies/statistics, ACL/dependency drift and partial cleanup failure refuse further drops |

The seven review regression tests were first run against the previous
implementation: all seven failed. They now pass. These results verify Python
control flow against mocks, not PostgreSQL permissions or DDL behavior.

## Explicit configuration; no production fallback

`integration_support.py` does no I/O on import. It does not import `config` or
`bot`, load dotenv, call `Repository.connect`, or invoke schema/backfill hooks.
The environment reader is called explicitly and requires all five variables:

| Variable | Required value |
| --- | --- |
| `DAO_TEST_HOST` | Exactly `localhost` or `127.0.0.1` |
| `DAO_TEST_PORT` | Explicit decimal TCP port, 1-65535 |
| `DAO_TEST_DATABASE` | Exactly `dao_eternity_test` |
| `DAO_TEST_USER` | Exactly `dao_test_user` |
| `DAO_TEST_PASSWORD` | Explicit nonempty password supplied securely outside chat/logs |

`DATABASE_URL`, `PG*`, game configuration and environment files are not fallback
sources. Driver fields are explicit; no DSN is assembled. `localhost` is pinned
to `127.0.0.1`, with `ssl=False`. Actual server address and port must match.
IPv6, sockets and remote addresses reject. This trusts the local server and OS;
loopback and matching names do not authenticate against a local attacker.

Config representations redact values. Reports contain only approved identity,
loopback/port, read-only status, privilege-verification status and schema-CREATE
capability. Driver/catalog errors use fixed messages. Journals contain identifiers
and status, never passwords, source SQL, definitions or original error text. Do
not log driver arguments, the full config via dataclass conversion, or process memory.

## Catalog-only preflight and verification policy

The public read-only entry point is
`await preflight_catalog(config, allow_connection=True)`. It opens one connection,
performs fixed catalog SELECTs, returns an immutable `PreflightReport`, and always
attempts to close. It returns no connection, callback or arbitrary SQL executor.
`allow_connection` defaults to false. No database CLI is added. The former
`open_readonly_connection` API is removed.

Connections start with `default_transaction_read_only=on` and
`search_path=pg_catalog`, plus connect/statement/lock/idle-transaction timeouts.
The preflight never lifts read-only mode. PostgreSQL read-only mode alone is not
a sandbox for arbitrary client SQL; restricting the API to catalog queries is
part of the boundary.

Before future test writes, verification requires:

- Actual database `dao_eternity_test`; session and effective role `dao_test_user`;
  matching loopback address and explicit port.
- Transaction and session default both read-only, a primary server, no temporary
  namespace, and a recognized PostgreSQL 15-18 catalog version. Other versions
  reject; the gate is not a claim that these versions have been tested.
- LOGIN without superuser, CREATEDB, CREATEROLE, replication or BYPASSRLS; no
  direct/indirect role memberships; no enabled database event triggers.
- No effective CONNECT/CREATE/TEMPORARY or ownership on any other connectable
  database, including `postgres` and `template1`. Catalogs cannot inventory objects
  inside other databases; rejecting all such access avoids assuming that gap safe.
- No schema CREATE or ownership outside the verified run namespace, including
  system schemas. Read-only USAGE on an otherwise inaccessible public schema is
  not itself rejected; object access is checked regardless of USAGE.
- No effective access/ownership on non-system tables, views, materialized views,
  foreign tables, columns or sequences outside the run. Table checks include
  MAINTAIN on PostgreSQL 17+. Non-bootstrap relations are checked even in system
  schemas; modification privileges on built-in catalog relations also reject,
  except for the narrowly inspected pg_settings UPDATE described below.
- No owned functions or executable non-system/non-bootstrap functions; no executable
  SECURITY DEFINER or selected server-file functions. Non-bootstrap object OIDs
  (16384+) are treated conservatively even in system/run schemas. Built-in system
  objects and their integrity are a trust base.
- No effective foreign-server/wrapper access, matching user mappings, large-object
  access, tablespace CREATE, or explicit parameter grants to PUBLIC/self/inherited
  roles. Large-object ACL inspection avoids a newer privilege function.
- No applicable explicit default ACL granting anyone other than the creator rights
  on new objects (global, and run-schema scope once it exists). Membership is
  forbidden, so another role cannot be used as creator. The manifest creates no
  functions, sequences, domains or arbitrary types.

Effective privilege functions account for PUBLIC and inherited rights. Default
ACLs are not existing-object grants; new schema/table/column/type ACLs are checked
separately after creation. Required negative flags must be exactly false, except
the catalog-write flag may trigger the additional fixed inspection below.
Missing/NULL results, catalog permission errors and unexpected metadata reject.
References: [privilege inquiry functions](https://www.postgresql.org/docs/15/functions-info.html),
[default ACL catalog](https://www.postgresql.org/docs/15/catalog-pg-default-acl.html).

For the approved composite/array row types, a NULL type ACL retains standard
PUBLIC USAGE; it does not grant access to stored rows. This is an explicit policy
exception behind the verified private schema/table ACLs. Explicit grants to
other grantees and non-manifest initial types reject. The harness does not claim
that every implicit PostgreSQL privilege is absent.

### Narrow pg_settings exception

PostgreSQL normally grants PUBLIC UPDATE on `pg_catalog.pg_settings`. Its built-in
rules implement session parameter changes; this permission does not itself grant
application-data modification. See the [PostgreSQL 18 source](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/catalog/system_views.sql)
and [pg_settings behavior](https://www.postgresql.org/docs/18/view-pg-settings.html).

When the broad catalog-modification flag is true, `CATALOG_WRITES_SQL` must return
exactly one eligible object. It must be the actual `pg_catalog.pg_settings`
regclass, with a bootstrap OID below 16384, correct name/schema and view kind,
and no ownership by the test role. Only PUBLIC UPDATE granted by that view's owner
without grant option is allowed. Additional effective UPDATE grants, explicit
column UPDATE grants and every other modification right reject. All other
catalog relations remain protected; MAINTAIN (PG17+) and ownership also reject.
Unknown metadata or an unreadable inspection query rejects the connection.

Parameter-level ACL checks, elevated-role/membership checks and every other
preflight gate run unchanged. The preflight still performs only catalog SELECTs
and returns no connection. As before, built-in catalog/view/rule integrity is a
trust assumption; this is not protection against a malicious DBA. No server grant
is changed to implement the exception.

This intentionally rejects a normal shared cluster with PUBLIC CONNECT on other
databases or PUBLIC access to extension functions. The harness never changes
existing grants. Use a separately isolated test instance or obtain a separately
reviewed privilege plan; do not revoke production grants as a workaround. A report
is a point-in-time observation, not protection against later DBA/grant changes.

## Separate, disabled write-capable API

Future `IsolatedTestDatabase.create` requires `allow_database_writes=True` and an
explicit existing local `recovery_directory`. Neither has a permissive default.
Caller flags are not an approval system: do not invoke this API against a database
until approved. Current test invocations use mocked transports only.

1. Validate configuration and reviewed DDL before connecting. Read SCHEMA
   assignments as AST string literals from seven fixed files without importing
   them. Require exact SHA-256 matches, normalizing only CRLF and outer whitespace.
   Execute independent static manifest bodies, never parsed source text. Any
   source change requires explicit manifest review. There is no regex SQL allowlist.
2. Generate `dao_it_<32 hex>` internally and exclusively create its journal.
   Refuse namespace reuse/collision. SQL identifiers come from the generated name
   and fixed table names; catalog lookup values use bind parameters.
3. Verify identity/privileges while read-only. Require database CREATE for the write
   API (not for the report). Only then enable writes. In one transaction create the
   schema, verify OID/owner and owner-only ACL, set/verify the private path, and
   create 27 qualified tables. Unsafe default grants reject; no GRANT/REVOKE runs.
4. Inventory schema, relations including indexes and TOAST, columns, constraints,
   defaults, triggers, rules, RLS policies, extended statistics and composite/array
   types. Verify ownership/ACLs; reject outside incoming dependencies. Initial
   objects must fit the manifest, including PK indexes, two CHECK constraints,
   and (on PostgreSQL 18) one NOT NULL constraint for each reviewed column;
   executable dependents and extra types/indexes reject. Persist OIDs and digest
   before commit; restore the control connection's read-only default afterward.
5. Register checkouts before awaiting. Each opens a fresh read-only connection and
   repeats identity/privilege, namespace OID/owner/ACL and exclusive-path checks
   before enabling writes. Limit to six, holding slots through disposal. The path
   contains only the run schema, excluding public, $user and temporary fallbacks.
   Built-ins resolve through PostgreSQL's implicit pg_catalog lookup.

Column names, attribute numbers, order and nullability must match the independent
`TABLE_SHAPES` manifest. Every currently reviewed column is NOT NULL, including
implicit primary-key nullability. Constraints use `contype` and `conkey`, rather
than counting textual PRIMARY KEY/CHECK prefixes. Validate exact primary-key
column order, CHECK column sets, and single-column NOT NULL identities; require
validated, enforced, local, non-deferred/non-deferrable constraints with no
inheritance. Duplicates, missing rows, unknown types/columns and foreign keys
reject. PG15-17 must have their older constraint layout; PG18 must have the new
NOT NULL rows. PostgreSQL's `conenforced` field is obtained from row JSON only
where available, preserving older catalog layouts. Definitions, types, column
addresses and status/inheritance flags are retained in cleanup fingerprints.
Reference: [PostgreSQL 18 constraint catalog](https://www.postgresql.org/docs/18/catalog-pg-constraint.html).

After the awaited write-enabling SET, `acquire()` checks readiness again
immediately before yielding. A peer's intervening failure causes rejection and
connection disposal, even if the SET already completed. Checkout accounting
still releases its slot/count through cancellation and disposal errors.

The write API yields a real connection for future reviewed game SQL. It is not a
sandbox against malicious test code, same-role external clients, superusers,
concurrent administrative DDL, or a compromised server. No production schema or
player row is used as a fixture or copied.

## Cleanup, cancellation and precise guarantees

For a ready run, `close()` refuses active/queued checkouts. It sets `closing` before yielding and
starts one worker, which all callers join. New acquisitions refuse closing state.
A cancelled waiter drains the worker through repeated cancellation, then re-raises
cancellation. Other callers can observe the shared result. Database timeouts bound
normal waits; a broken driver that ignores cancellation can still stall.

The worker rechecks identity, privileges and namespace; locks known tables in
ACCESS EXCLUSIVE mode; then compares full inventory including OIDs, definitions,
owner, ACLs and incoming dependencies. It drops only qualified manifest tables
and the empty schema with RESTRICT in one transaction. There is no CASCADE,
prefix scan/drop, broad retry, TRUNCATE or row-deletion cleanup. Drift rejects
before the first DROP. Failure partway through drops requests rollback and closes
the connection; actual transaction status is not asserted without server evidence.

`DROP TABLE RESTRICT` does **not** preserve the table's own indexes, triggers,
rules or constraints: PostgreSQL removes them with the table. It blocks
dependencies requiring CASCADE, such as referencing views or foreign keys from
other tables. RESTRICT alone cannot guarantee that only table objects are deleted;
dependent inventory is essential. Automatic/internal dependents are acceptable
only when included in unchanged run inventory. References:
[DROP TABLE](https://www.postgresql.org/docs/15/sql-droptable.html),
[dependency tracking](https://www.postgresql.org/docs/15/catalog-pg-depend.html).
Unexpected standalone objects cause DROP SCHEMA RESTRICT to fail and the cleanup
transaction to roll back; they are never removed through CASCADE.

Checkout accounting runs in a finalizer even if close is cancelled. Interrupted
graceful closes attempt synchronous termination; late results from cancelled
connects are drained and terminated. If termination cannot be confirmed, the run
fails and records `connection_close_unconfirmed`; no further acquisition or
destructive retry is allowed. Cancellation remains `CancelledError`, annotated
with recovery metadata. Safety exceptions carry `.recovery`. Cleanup errors do
not become successful test results.

Control-connection disposal has its own shared worker. Setup failure, cleanup
finalization and failed-run shutdown use the same disposal path. For an already
failed run, `close()` attempts connection disposal without SQL, then raises a
safety error explaining that database objects were not removed. Concurrent calls
join the disposal worker; they cannot initiate a destructive retry.

`disconnect()` is explicitly non-destructive: it marks a ready run failed before
awaiting, blocks acquisitions, and closes/terminates the control session without
catalog queries, SET, DROP or journal-driven object deletion. It refuses to
interrupt a running cleanup worker, which already owns final disposal. Existing
checkouts own independent connections; their callers must still await or cancel
them. Disposal does not erase schema/OID/outcome recovery evidence or claim that
objects were cleaned. Cancellation is re-raised after disposal is drained; failed
termination retains `connection_close_unconfirmed` and requires manual review.

## Recovery after partial setup or cleanup

Choose a private, existing local directory for future journals; the suggested
`integration_recovery/` location is Git-ignored. No directory is automatically
created. Retain records until the run is accounted for. Files are created
exclusively, fsynced and updated through atomic replacement; changed content or
symlinks reject updates. This does not defend against a hostile filesystem owner,
power-loss/durability failures, or loss of the recovery directory.

Records include schema, approved database/role, phase, conservative outcome, known
namespace/owner/table OIDs and inventory digest. Write `commit_unknown` before
attempting setup commit, then `committed` afterward; cleanup has analogous states.
DDL/rollback failures retain unverified outcomes. If a journal update fails, stop;
in-memory metadata is attached to the exception and previous disk evidence remains
conservative. A crash before OID capture still leaves exact schema and phase.

After setup/cancellation/cleanup failure:

1. Retain `.recovery` and the exact journal. Do not retry cleanup automatically,
   use CASCADE, or delete every schema with a matching prefix.
2. After separately authorized catalog-only access, recheck database/session
   identity, any remaining `dao_integration_guard` sessions, and exact schema name,
   namespace OID, owner and table OIDs. Name alone does not prove ownership of
   surviving objects. Resolve unconfirmed sessions before considering removal.
3. Treat unknown/rollback-unverified outcomes as unknown until server evidence
   confirms them. Reconcile disk/in-memory metadata; a Python transaction exit
   does not prove rollback and a commit exception does not prove no commit.
4. Obtain a separate concrete cleanup review for surviving objects. No destructive
   journal-driven recovery command is provided.

## Offline validation

From `dao_of_eternity_bot`, with installed dependencies:

```powershell
python -B -m unittest test_integration_safety test_integration_review_regressions test_integration_manifest_recovery test_integration_followup -v
python -B -m unittest discover -v
git diff --check
```

No DAO_TEST variables are needed. Every safeguard driver call is mocked; SQL is
never executed. Journals use temporary local directories and synthetic metadata.
AST syntax checks do not import/start game code. Whitespace checks also cover
untracked files, which ordinary `git diff --check` excludes.

Final validation: **321 tests passed**, comprising the 233 existing game/persistence
tests and **88 safeguard tests** (the previous 70 plus 18 follow-up tests). The four
new review regressions failed against 851ad0a and now pass. AST syntax checks
passed for all **52 Python files**, and Git plus explicit whitespace checks
passed for all **six changed/new files**, including the untracked new test module.
No test failed or was skipped. No database test is added to discovery or GitHub
Actions.

## Later authorized database plan and limitations

First authorize **catalog-only connection testing**, review its report, and
validate actual catalog queries and rejection paths using a dedicated disposable
instance. Do not authorize writes merely because mocks pass. Validate catalog
visibility/version compatibility, ACL/default-ACL behavior, implicit dependencies
and type/TOAST inventory on the real server. Administrative changes can race a
catalog snapshot; use a dedicated instance without competing clients.

Specifically for PostgreSQL 18, verify the actual pg_settings identity/ACL grantor
and rule behavior, rejection of additional catalog/parameter grants, NOT NULL
constraint `conkey`/enforcement metadata including implicit PK nullability, and
the complete type/TOAST/dependency fingerprint. Fixtures are synthetic metadata;
they do not establish that these SQL queries execute or that rollback and
connection termination complete on a real server. Do not weaken rejection paths
or change production grants to work around an unexpected result.

Only after a separate write-stage review should a separate runner implement:

| Case | Required database evidence |
| --- | --- |
| Combat-state locking | Held lock blocks another action; distinct turns persist; replay cannot duplicate rewards |
| Companion updates | Overlapped battle startup/advance/training/exploration loses no progress or inventory |
| Partnership acceptance | Competing/opposite invitations activate one valid partnership, with no partial state |
| Dual cultivation | Opposite concurrent accepts avoid deadlock and apply boosts/cooldowns once |
| Transaction rollback | Failure between state/inventory and action persistence rolls back all changes; retry pays once |
| Isolation and cleanup | Synthetic rows only in run schema; denied outside access; sentinels unchanged; drift refuses cleanup |
| Failure recovery | Interrupted setup/commit/cleanup reconciles exactly with journals and server state |

Real locking/MVCC, rollback, connection cancellation, privilege semantics, catalog
query execution and transactional DDL cleanup remain unverified offline. No schema
migration or gameplay-balance change is included.
