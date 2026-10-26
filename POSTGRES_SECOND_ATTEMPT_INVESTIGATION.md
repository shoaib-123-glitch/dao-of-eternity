# Second PostgreSQL integration attempt: offline investigation

2026-10-24, branch `feature/postgres-integration-safeguards`.

The saved fresh journal confirms the operator's diagnostic for
`dao_it_8f601c52b5494c36adac957d4ad1454a`, namespace OID 24576, owner OID 16389:
SAFETY / INVENTORY_QUERY, ordinal 0, table null, rollback ACKNOWLEDGED,
rollback_verification UNKNOWN and connection_cleanup ACKNOWLEDGED. Its phase is
creating_tables, outcome rollback_unverified, table_oids empty and inventory_digest
null. The suite attempt is consumed and its result is UNKNOWN with runner exit 1.
Provisioning receipts agree on database OID 16388 and the pinned original container.
The original retired schema/OID 16390 is outside this recovery procedure.

## Finding and smallest supported change

The failure boundary is the awaited inventory fetch, before inventory validation.
There is no explicit application safety predicate at that fetch. SAFETY is also
the diagnostic's catchall for unclassified server and Python exceptions. The
saved diagnostic does not retain the underlying exception class, SQLSTATE, server
message or traceback, so the database failure's root cause cannot be identified
from these files alone. Do not interpret SAFETY as proof of rejected ownership,
dependencies or a search-path check.

No inventory SQL, manifest, result normalization, ownership, ACL, dependency,
transaction or isolation change is justified by the available evidence. Those
implementations remain unchanged, as do their seals and all historical records.
No additional diagnostic fields have been retroactively invented for this attempt.

A separate, reproducible tooling defect was confirmed: the original inspector
requires `<schema>.precommit.json` and `<schema>.provisioning.json` in the old
receipt format. The fresh workflow instead writes `provision.precommit.json`,
`provision.receipt.json`, new result proofs and a source seal. Running the original
inspector against this bundle fails FILE_MISSING before connecting. The smallest
repair is a separate strict fresh-evidence reader and reuse of the existing fixed
read-only catalog engine. No receipts are renamed, translated, copied or resealed.

## Exact execution path and diagnostic boundaries

`integration_runner.run_suite` awaits `IsolatedTestDatabase.adopt_empty` before
entering the case loop. A failed adoption invokes neither production-backed cases
nor ready-run DROP cleanup.

1. `adopt_empty` checks explicit write authorization, exact config, independent
   cluster identity, schema/OID, and reviewed manifest. It exclusively creates
   a NEW recovery journal; an existing journal blocks replay before connecting.
2. `_connect` uses explicit loopback host/port, database, role and prompted password.
   Startup search_path is pg_catalog; default_transaction_read_only is on.
3. `_verify`, `_check_namespace` and `_require_empty_namespace` verify identity,
   effective privileges, exact fresh schema/OID/owner/ACL and emptiness.
4. The control session enables writes, `_setup_transaction` begins a driver
   transaction, then namespace and emptiness checks repeat inside that transaction.
   The journal is set to creating_tables / rollback_unverified.
5. `_setup_tables` at `integration_support.py:681` records SEARCH_PATH_SETUP,
   calls `_set_path` and requires current_schema to equal the private schema,
   current_schemas(false) to contain only that schema, and no temporary namespace.
6. Each of the 27 fixed CREATE TABLE calls uses the quoted fresh schema and table
   name, with its own CREATE_TABLE ordinal/name checkpoint.
7. `_setup_stage(INVENTORY_QUERY)` clears the previous table label to ordinal 0
   and null and saves that checkpoint. Then, at `integration_support.py:688`,
   exactly `await self._control.fetch(INVENTORY_SQL, self._namespace_oid)` runs.
8. Only after fetch returns does `_setup_stage(INVENTORY_VALIDATION)` save its
   checkpoint and `_validate_initial_inventory` run. Only after validation does
   `_fingerprint` produce a digest and table OIDs get recorded before commit.
9. The setup transaction captures the first failure, requests driver rollback,
   records its acknowledgement, and re-raises. Failed adoption disposes of the
   connection and appends the connection-cleanup result to the diagnostic.

With these intact source seals and no concurrent journal/source mutation, the
saved INVENTORY_QUERY stage means all 27 CREATE calls returned before the fetch
failed; it does not establish that any table committed or remains present. The
empty journal inventory is expected before a successful validated checkpoint.
An error saving either metadata-stage checkpoint is classified at
JOURNAL_PERSISTENCE, not INVENTORY_QUERY. A row-validation or fingerprint failure
would be recorded at INVENTORY_VALIDATION.

## Every route to SAFETY at this boundary

`integration_diagnostics.setup_category` and
`IsolatedTestDatabase._capture_setup_failure` determine the category. Their full
decision tree at INVENTORY_QUERY is:

| Exception condition | Result |
| --- | --- |
| asyncio cancellation | CANCELLED |
| Python timeout | TIMEOUT |
| PostgreSQL SQLSTATE 42501 | PRIVILEGES |
| PostgreSQL SQLSTATE 25006 | READ_ONLY |
| PostgreSQL SQLSTATE 57014 or 55P03 | TIMEOUT |
| PostgreSQL SQLSTATE beginning 42 | SQL_COMPATIBILITY |
| PostgreSQL SQLSTATE beginning 25 or 40 | TRANSACTION |
| PostgreSQL SQLSTATE beginning 28 | AUTHENTICATION |
| PostgreSQL SQLSTATE beginning 08 or 57, except 57014 handled above | CONNECTION |
| OSError or asyncpg.InterfaceError | CONNECTION |
| Any remaining PostgreSQL error, including missing/non-string class SQLSTATE | SAFETY fallback |
| Any remaining ordinary Python exception | SAFETY fallback |
| IntegrationSafetyError | Its allowlisted FailureCategory overrides the generic result; absent/invalid category becomes SAFETY |

Consequently XX000/internal errors, 0A000/unsupported features, class 22/data
errors, 3F000/invalid schema name, 3D000/invalid catalog name, resource errors such
as 53300, and Python RuntimeError/TypeError/etc. can all produce SAFETY here.
These are possibilities, not diagnoses of this attempt. The separate connection
and recovery-inspection classifiers have different mappings; they must not be
substituted for setup_category when interpreting this journal. Recognized syntax,
permission, read-only and aborted-transaction failures normally have the distinct
categories above. SQLSTATE terminology follows the
[PostgreSQL 18 error-code appendix](https://www.postgresql.org/docs/18/errcodes-appendix.html).

No ownership, ACL, dependency, row-count or table-shape Boolean is evaluated in
Python during the awaited fetch. SQL computes such metadata; false values cause
rejection later, after INVENTORY_VALIDATION is recorded. SQL evaluation itself
can still raise, and result decoding/transport errors occur inside fetch too.

## Review of the proposed causes

| Area | Evidence and implication |
| --- | --- |
| Session search_path | `_set_path` has already passed or the stage would be SEARCH_PATH_SETUP. Inventory relations/functions are explicitly pg_catalog-qualified; SQL constructs such as COALESCE need no schema qualification. pg_catalog remains implicitly searched before the private schema, including for built-in casts such as aclitem[]. A later session/catalog change is not independently ruled out. |
| Transaction state | Setup is already in its write transaction; 27 CREATE calls returned. An ordinary aborted/read-only transaction maps to TRANSACTION/READ_ONLY. The driver acknowledged rollback; durable historical rollback is still UNKNOWN. |
| Inventory SQL | The single OID parameter selects namespace 24576. CTEs enumerate namespace, ordinary/TOAST relations and indexes, columns, constraints, defaults, triggers, rules, policies, statistics and namespace row/array types. Incoming dependencies from outside that set return dependencies_safe=false; checks remain intact. No static syntax incompatibility was established. |
| PostgreSQL 18 constraints | pg_constraint includes not-null constraints; the query reads contype/conkey plus conenforced when present. The validator expects 27 PKs, 2 CHECKs and 106 NOT NULL constraints on the 27 manifest tables. PostgreSQL 18.6's pg_get_constraintdef explicitly handles CONSTRAINT_NOTNULL. |
| asyncpg result types | UNION outputs are text, oid, oid, integer, text, oid, oid, text, Boolean, Boolean. Native OIDs decode as integers. Inventory uses named access/.get, supported by real asyncpg.Record fixtures. The two consumed nested JSON OIDs retain the existing bounded canonical-string/int correction; their malformed values would reject at INVENTORY_VALIDATION. Driver decode errors during fetch remain possible. |
| Ownership and ACLs | `_validate_inventory_access` runs after fetch and rejects wrong owner, unsafe ACL, false/unknown dependency flags or no rows. It cannot directly explain the recorded query stage. A catalog privilege/error raised while computing SQL metadata remains a different possibility. |
| Dependencies and strict validation | Dependent objects, relation/index kinds, manifest membership, columns/nullability, constraint signatures/flags and composite/array identities are all checked after fetch. They are not bypassed. Any later inventory-policy incompatibility would require its own validated evidence. |
| Manifest | All seven SCHEMA literals still match their reviewed hashes. The 27-table digest is unchanged: 9c1af28dec0ea7eeb13641a08f48500a0ffcdc6b54056937dd1b11d0b0260c38. A manifest-source mismatch would reject before connecting. |

Primary references checked during review:
[PostgreSQL search-path rules](https://www.postgresql.org/docs/18/ddl-schemas.html#DDL-SCHEMAS-PATH),
[PostgreSQL 18 pg_constraint](https://www.postgresql.org/docs/18/catalog-pg-constraint.html),
[PostgreSQL 18.6 NOT NULL deparser](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/utils/adt/ruleutils.c#L2395),
and [asyncpg Record interface](https://magicstack.github.io/asyncpg/current/api/index.html#record-objects).
These sources support compatibility review, not a live reproduction of this failure.

## Separate read-only recovery procedure

From the bot project directory, validate this exact archival bundle offline:

```powershell
python -B -E -s .\fresh_recovery_inspector.py check
```

This has been run offline. It verifies all 21 original files, the source/plan/batch
seals, provisioning result proofs, matching raw/normalized receipts and precommit
OID, the consumed suite attempt/result, and the exact reported journal state.
It does not create an observation record, prompt for a password or connect.

Only after separate approval to connect for strictly read-only recovery inspection:

```powershell
python -B -E -s .\fresh_recovery_inspector.py inspect --allow-read-only-connection
```

This command is PREPARED ONLY and has not been executed. It securely prompts for
the dao_test_user password; it never reads an environment file, starts Docker,
uses an administrator connection or accepts arbitrary SQL. It pins loopback
127.0.0.1:5433, database dao_eternity_test/OID 16388, role dao_test_user/OID 16389,
PostgreSQL 18.6, server 172.17.0.2:5432 and cluster 7699864005918920747.
Its archival container-ID check binds the recorded full ID from provisioning;
the live check verifies the endpoint/cluster via the existing catalog queries,
without invoking Docker. This is not a new independent runtime Docker attestation.

The reused engine opens a READ ONLY, REPEATABLE READ transaction and exposes only
the existing SELECT allowlist: IDENTITY_SQL, SERVER_IDENTITY_SQL, PRIVILEGES_SQL,
CATALOG_WRITES_SQL, NAMESPACE_SQL and RECOVERY_OBSERVATION_SQL. Parameters are fixed
to this fresh schema/OID, owner and database; the retired namespace/OID is never
queried as the inspection target. It rechecks identity, private ACLs, full effective
privilege policy, no database CREATE, schema USAGE/CREATE, catalog emptiness and
other guard sessions. There is no execute/write API, writable transaction,
inventory replay, DDL, journal save, grant or retry.

New observation files go exclusively to a new
`fresh_inspection_records/operation-<uuid>/` directory, outside both archives.
attempt.json is flushed before a connection; evidence.hashes.json records all
21 hashes; result.json holds only sanitized results. These are exclusive writes,
never replacements. Partial/failed records remain in place. Each authorized
inspection makes one connection attempt; it does not retry on failure. Repeating
an inspection requires a new explicit invocation and creates a new operation.
The consumed suite attempt, journal, receipts and source seal are never modified.

An EMPTY_SCHEMA_OBSERVED result establishes only catalog emptiness in the new
observation. Objects/sessions, identity mismatch, or cleanup uncertainty require
review. Historical rollback_verification remains UNKNOWN in every result.
Even an empty schema cannot reproduce the previous inventory query over 27
uncommitted tables: a new connection cannot recover their old catalog snapshot,
old session search_path or original SQLSTATE. Do not rerun provisioning or the
suite, reset attempt files, or create tables to reproduce the query under this
authorization. Any additional live diagnosis needs separate review.

## Offline validation and preserved evidence

Run the complete offline validation in one command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\validate_offline.ps1
```

Validation includes all previous regressions, real asyncpg.Record fixtures,
query-stage classification/error boundaries, fresh receipt incompatibility and
the new reader, exact OID pins, all-21-file change detection, isolated new records,
read-only transaction/allowlist enforcement, failure handling, no retries and
continued suite single use. The guard now denies access to BOTH historical
directories during tests; fixtures are temporary synthetic files only.
PowerShell syntax, operator command parsing, existing binding/native-argument
fixtures, AST compilation and whitespace checks are also included.

The 21 fresh archive files were SHA-256 snapshotted before editing. They are
rechecked after the investigation. The restricted retired archive is neither
opened nor permission-modified. The four requested implementation files
integration_support.py, integration_catalog.py, integration_manifest.py and
integration_diagnostics.py were inspected and are unchanged in this update.
No Docker command, database connection, SQL execution, database-backed test,
schema/role/grant change, bot startup, environment-file open, commit, push, merge
or deployment has been performed.

Final validation passed: 731 Python tests (707 previous plus 24 new), zero
failures/errors/skips and zero offline-guard violations. PowerShell passed 60
binding fixtures, 28 static consistency checks and 12 native argument fixtures;
script/command parsing, AST compilation and whitespace checks passed too.
All 21 fresh archive files match their pre-edit SHA-256 snapshots byte for byte.
The offline reader's aggregate evidence digest is
87bd34a88e2370fba89b569b616fbc37cc93ee2770d81d6185f9c4d90f2efd90.
No new live observation record was created.

Files changed in this investigation: `.gitignore`, `integration_inspector.py`,
`fresh_recovery_inspector.py`, `test_fresh_recovery_inspector.py`,
`test_inventory_query_investigation.py`, `validate_offline.py`,
`validate_offline.ps1`, and this report. The original fresh provisioning guide,
provisioning batch/seal and source files covered by that seal are unchanged.
