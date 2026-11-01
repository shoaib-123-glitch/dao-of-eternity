# Inventory fetch review and diagnostic improvement

The original second-attempt root cause remains **unresolved**. No defect in the
current `INVENTORY_SQL` has been demonstrated offline. Its inventory, ownership,
ACL, dependency and isolation requirements are unchanged. There is no basis to
skip an inventory branch, permit an unknown object or retry the consumed attempt.

## Evidence and execution path

`IsolatedTestDatabase._setup_tables` sets the verified session path, executes the
27 reviewed CREATE statements, records `INVENTORY_QUERY`, then awaits
`_control.fetch(INVENTORY_SQL, namespace_oid)`. It enters `INVENTORY_VALIDATION`
only after that await returns. The reported ordinal `0` and null table are the
expected inventory-stage values. The old `SAFETY` fallback did not retain enough
information to distinguish query rejection, decoding or a Python exception.

The separately authorized inspection observed the reserved namespace empty and
private with its pinned OID. That is a current-state observation. It neither
reconstructs the original exception nor changes the historical rollback
verification from `UNKNOWN`. The consumed schema, suite attempt, 21 original
bundle files and inspection records remain historical evidence. This update
does not reseal, reset or reuse them.

## Static findings

- PostgreSQL 18.6's `pg_get_constraintdef` explicitly handles
  `CONSTRAINT_NOTNULL`. NOT NULL constraint inventory is therefore not, by
  itself, evidence of an unsupported deparser call. See the pinned
  [PostgreSQL 18.6 implementation](https://raw.githubusercontent.com/postgres/postgres/REL_18_6/src/backend/utils/adt/ruleutils.c)
  and [PostgreSQL 18 constraint catalog](https://www.postgresql.org/docs/18/catalog-pg-constraint.html).
- The UNION's final fields use ordinary built-in text, OID, integer and Boolean
  results. asyncpg normally decodes these as Python strings, integers and
  Booleans. A runtime codec or catalog error remains possible; the offline
  review does not establish that one occurred.
- Ownership, ACL and dependency rejection happens after a successful fetch.
  Those Python checks cannot explain an exception retained specifically at
  `INVENTORY_QUERY`. Session settings, transaction state, privileges, server
  catalog access and timeouts can affect a real fetch; the old diagnostic
  does not identify which, if any, caused the failure.
- asyncpg can raise `DataError` locally from parameter encoding. The exception
  class's default SQLSTATE is insufficient evidence of a server rejection.
  Its ErrorResponse factory instead stores the server SQLSTATE on the exception
  instance. See [asyncpg 0.32.0 exception construction](https://raw.githubusercontent.com/MagicStack/asyncpg/v0.32.0/asyncpg/exceptions/_base.py)
  and [parameter encoding / row decoding](https://raw.githubusercontent.com/MagicStack/asyncpg/v0.32.0/asyncpg/protocol/prepared_stmt.pyx).

## What changed

Only the initial inventory-fetch await captures the optional `inventory_fetch`
diagnostic. Existing stage/category fields and legacy diagnostic shapes remain
compatible. New records emit four fixed fields: `family`, `sqlstate_class`,
`sqlstate_code` and `sqlstate_source`. The durable reader validates the same
allowlist as the writer.

Server SQLSTATE instance fields distinguish rejection, cancellation, connection
termination and specific server timeouts. A server `57014` is cancellation; it
does not prove a timeout. Explicit timeout codes include `25P03`, `25P04` and
`57P05`. The codes follow the
[PostgreSQL 18 error-code catalog](https://www.postgresql.org/docs/18/errcodes-appendix.html).
Class defaults are marked `POSTGRES_UNATTRIBUTED`, with `CLASS_DEFAULT`
provenance. Unknown exact codes are suppressed; an allowlisted class can remain.

`OutdatedSchemaCacheError` is a reliable driver decode signal. `ProtocolError`,
`InternalClientError` and `InterfaceError` have separate fixed families; an
interface error does not automatically prove a broken connection. Python
timeouts, connection errors, other OS errors, cancellation and other Python
exceptions remain distinct. A `BufferError` or `UnicodeDecodeError` alone does
not prove a driver decode failure, so it stays a Python error.

No exception message, SQL, parameters, credentials, records, arbitrary class
names or stringified exceptions are included. Synthetic regressions cover all
fixed families, malformed metadata, hostile attributes, legacy/new journal
round trips and an adoption failure through rollback and connection cleanup.
The adoption regression verifies one fetch, no retry or DROP, and replay refusal.

## Optional compatibility probe and limits

A separately approved catalog-only probe could prepare/describe the fixed
inventory query, or use non-ANALYZE EXPLAIN, to check parse, name resolution and
result types under the exact PostgreSQL/asyncpg versions, role, pinned cluster
and a read-only transaction. Fetching the inventory of a separately approved
empty namespace could also exercise basic result decoding. This is a review
proposal; no new live probe or live operation is implemented or executed here.

Such a probe would require an independent explicit read-only connection gate,
fixed SQL, exact identity checks, bounded timeouts and a new append-only result.
It must not use the failed schema or consumed attempt. PREPARE changes session
state; EXPLAIN without ANALYZE does not run the query's data-dependent branches.
An empty namespace cannot exercise the relation, column, constraint, default,
trigger, rule, policy, statistics, row-type and dependency inventory generated
by the 27 real tables. Success would establish limited compatibility, not a
passing integration suite or a historical rollback verdict.

The next practical step is review of this diagnostic change alongside gameplay
work. If live validation is later authorized, allocate a new unique plan,
reservation, schema and single-use attempt with explicit database-write consent.
Existing sealed bundles should reject current-source drift; do not update their
seals to make them reusable. Until that validation, the original live integration
failure remains a release blocker for claiming PostgreSQL integration success.
