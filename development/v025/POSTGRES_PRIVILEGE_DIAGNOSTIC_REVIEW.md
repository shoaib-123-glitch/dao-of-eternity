# PostgreSQL 18.6 preflight: privilege rejection review

Prepared on `feature/postgres-integration-safeguards`. The reported rejection is
`PREFLIGHT REJECTED [PRIVILEGES]`. No live preflight, PostgreSQL connection, SQL
execution, Docker command, environment-file access, server/grant/schema/table
change, bot startup, commit, push, merge or deployment was performed here.
Existing work and every existing rejection predicate were preserved.

## Suspected cause and limits

A plausible cause is `other_database_access`: this policy rejects ownership or
**any** effective CONNECT, CREATE or TEMPORARY right on another database that
allows connections. A denied connection to `postgres`/`template1` does not rule
out a residual TEMPORARY grant, including PostgreSQL's default PUBLIC grant.
This is an inference, not a diagnosis of this instance. The supplied checks on
the target database do not establish the remaining flags.

The library checks more than database-level privileges. Target CONNECT is
exercised by connection establishment; target CREATE is reported as
`can_create_schema` and is not a mandatory read-only preflight gate. Target TEMP
is not independently required by this read-only verifier. None of these is a
substitute for the other-database, role, schema, object, ACL or catalog checks.

Other plausible causes include database ownership (implicit membership in
`pg_database_owner`), other role memberships, schema CREATE access, accessible
extension/user routines, or permission denial during a metadata query. There is
no evidence yet selecting one. In particular, `PRIVILEGES` does not prove that
authentication, cluster verification or all catalog queries completed.

## Every PRIVILEGES rejection boundary

`integration_support.py` produces this category at the following boundaries:

| Diagnostic stage | Condition producing PRIVILEGES |
| --- | --- |
| `CONNECTION_STARTUP` | Driver exception class has SQLSTATE 42501 during connection/session startup |
| `SESSION_METADATA` | SQLSTATE 42501 while reading/processing the fixed session identity metadata |
| `ROLE_POLICY` | `can_login` is false, `elevated` is true, `other_memberships` is true, or `enabled_event_triggers` is true |
| `SERVER_IDENTITY` | SQLSTATE 42501 while reading the fixed cluster identifier, when an expected server pin is supplied |
| `EFFECTIVE_PRIVILEGES_QUERY` | SQLSTATE 42501 while reading/processing effective privilege metadata |
| `EFFECTIVE_PRIVILEGES_POLICY` | Any effective privilege flag below is true, except `system_catalog_writes`, which has a separate narrow policy |
| `CATALOG_WRITES_QUERY` | SQLSTATE 42501 while reading/processing the existing catalog-write inspection |
| `CATALOG_WRITES_POLICY` | The catalog-write result is not exactly one fully validated standard pg_settings PUBLIC UPDATE candidate |

The role policy uses these exact Booleans:

| Flag | Required value and rejection meaning |
| --- | --- |
| `can_login` | True; false rejects even if authentication had previously succeeded |
| `elevated` | False; OR of SUPERUSER, CREATEDB, CREATEROLE, REPLICATION and BYPASSRLS |
| `other_memberships` | False; direct or indirect MEMBER of any other role, independent of whether its privileges are currently inherited |
| `enabled_event_triggers` | False; any event trigger in the target database with enable state other than disabled rejects |

The twelve `PRIVILEGES_SQL` flags are:

| Flag | What makes it true |
| --- | --- |
| `other_database_access` | Another `datallowconn` database is owned by this role, or any effective CONNECT, CREATE or TEMPORARY right exists there |
| `outside_schema_create` | Effective CREATE in any schema other than the separately verified run namespace |
| `outside_schema_ownership` | Direct ownership of any schema other than that namespace |
| `outside_table_access` | Direct ownership or effective table/column access to a table, partitioned table, view, materialized view or foreign table outside the run, in a non-system schema or with an OID at/above 16384; table rights include MAINTAIN on PG17+ |
| `system_catalog_writes` | Ownership or effective modifying table/column rights on a built-in system relation outside the run; true requires the separate pg_settings inspection and is not itself a rejection |
| `outside_sequence_access` | Ownership or effective USAGE, SELECT or UPDATE on any sequence outside the run |
| `outside_function_access` | Ownership of any routine, or effective EXECUTE on a routine in a non-system schema, a SECURITY DEFINER routine, a routine with OID at/above 16384, or specified pg_catalog file/directory operations |
| `unsafe_defaults` | A global/run-namespace default ACL for this role grants rights to a grantee other than the role itself |
| `foreign_access` | Effective USAGE on a foreign server or wrapper, or a user mapping for this role or PUBLIC |
| `large_object_access` | Ownership or an effective/PUBLIC ACL entry on a large object |
| `unsafe_parameter_access` | Any applicable PUBLIC or inherited parameter ACL entry, including SET or ALTER SYSTEM rights |
| `tablespace_access` | Effective CREATE on any tablespace |

For catalog writes, `_standard_pg_settings_update()` still requires all of:

- Exactly one candidate with an integer OID greater than zero and below 16384.
- Exact schema `pg_catalog`, name `pg_settings`, relation kind view, and
  `is_pg_settings=true` confirmed through the built-in regclass identity.
- `owned=false`, `other_table_access=false`, `other_column_access=false`.
- `table_update=true`, `update_acl_safe=true`, `explicit_column_update=false`.

The ACL safety predicate requires the owner's ordinary non-grantable PUBLIC
UPDATE and forbids any additional applicable UPDATE grant/provenance. Wrong
types or absent candidate fields do not qualify for the exception. Zero or
multiple candidates reject. Unreadable or malformed query results can instead
produce CATALOG_COMPATIBILITY; missing/unknown role or main privilege metadata
also stays CATALOG_COMPATIBILITY. Identity, read-only, version and disposal
failures retain their own categories. Database CREATE capability being false
does not itself produce PRIVILEGES.

## PostgreSQL 18.6 compatibility and possible false positives

Source/documentation review found no demonstrated PG18.6-only query
incompatibility. PostgreSQL 18 supports the referenced privilege inquiry
functions, MEMBER/USAGE role checks, ACL expansion and table MAINTAIN rights.
The existing version gate accepts integer `180006` and reports major 18. An
offline fixture verifies that version and the narrow normal pg_settings case;
it does not validate live SQL parsing or permissions.

Relevant compatibility/interpretation traps:

1. Comma-separated privilege lists mean **any** listed right. PostgreSQL grants
   database CONNECT and TEMPORARY to PUBLIC by default. Residual TEMPORARY can
   trigger this conservative policy even when the account cannot actually
   establish a connection to that database. This is stronger policy than
   “cannot log in elsewhere”; it is not evidence of an erroneous privilege API.
   Sources: [privilege inquiry functions](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-INFO-ACCESS-TABLE),
   [default privileges](https://www.postgresql.org/docs/18/ddl-priv.html).
2. A database owner is implicitly a member of `pg_database_owner`, which initially
   owns public. MEMBER includes indirect membership and ignores whether rights
   are currently inherited. The current no-membership policy intentionally
   rejects this owner case; NOINHERIT does not make it pass. Sources:
   [predefined roles](https://www.postgresql.org/docs/18/predefined-roles.html),
   [role inquiry semantics](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-INFO-ACCESS-TABLE).
3. PG18.6 still supplies ordinary PUBLIC UPDATE on pg_settings. The broad
   `system_catalog_writes` flag can therefore be true on a normal installation.
   The previously reviewed narrow exception is retained; the flag alone must
   not be described as an unsafe grant. Source:
   [REL_18_6 system view definitions](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/catalog/system_views.sql).
4. Schema CREATE and accessible/owned user or extension routines can violate the
   policy despite correct database-level permissions. Object privilege flags
   conservatively inspect ACLs without requiring schema USAGE; an object may
   be flagged even if ordinary name resolution would block its use. No such
   rule is weakened by this diagnostic.
5. Permission denial from a metadata SELECT uses the same broad category as a
   rejected grant. A `*_QUERY`, SESSION_METADATA or SERVER_IDENTITY stage with
   empty flags means the query did not produce a usable Boolean result. It is
   not a report of excessive privileges or a reason to grant more rights.

[PG18.6 release notes](https://www.postgresql.org/docs/release/18.6/) and the
tagged source were consulted. Actual target permissions and query behavior
remain unverified here; no live-server conclusion is inferred from mocks.

## Proposed minimal diagnostic

The new `-PrivilegeDiagnostics` switch adds one sanitized JSON line only when
the existing run rejects with PRIVILEGES. It reuses metadata already read at
that failure boundary. **All existing SQL strings and rejection predicates are
unchanged; no extra query, connection, retry or bypass is added.**

Example of a possible result, not observed output from this container:

```text
PREFLIGHT REJECTED [PRIVILEGES]: connection or safety verification failed.
{"privilege_diagnostic":{"stage":"ROLE_POLICY","flags":{"can_login":true,"elevated":false,"other_memberships":true,"enabled_event_triggers":false}}}
```

Stages are exact enum values; output keys come from a fixed local allowlist and
values must be exact Booleans. Unknown keys and non-Boolean values are omitted,
never coerced to false. Exception attributes are revalidated at display time.
No exception message, SQLSTATE, query, DSN, password, catalog name, OID, cluster
identifier or unrestricted row becomes diagnostic output. Missing/unrecognized
stage metadata is shown only as `UNAVAILABLE` with empty flags. Candidate-count
and object-identity tests are exposed as Boolean predicates, not names or counts.

The diagnostic describes the first failed block, not all conditions on the
server. Later blocks have not necessarily run. In particular ROLE_POLICY is
before the cluster query; that result does not certify the cluster pin. A failed
metadata query has no known flags. Failed connection disposal overrides a prior
privilege rejection and suppresses its stale detail. Success output and exit
codes are unchanged; a diagnostic rejection remains exit 1, never PASSED.

## Prepared procedure, not executed

After separate review, the exact invocation from any PowerShell directory is:

```powershell
powershell.exe -NoProfile -File 'C:\Users\Mr. Qureshi\Downloads\dao_of_eternity_v022_golden_bonds\dao_of_eternity_v022\dao_of_eternity_bot\catalog_preflight.ps1' -DockerForwarding -PrivilegeDiagnostics
$LASTEXITCODE
```

The existing launcher requires a trusted existing virtual environment and
securely prompts for the test-user password. It loads no environment files.
The client remains `127.0.0.1:5433`, server pin `172.17.0.2:5432`, database
`dao_eternity_test`, effective/session role `dao_test_user`, and cluster pin
`7699864005918920747`. Read-only startup, fixed SELECTs, full identity/privilege
gates and mandatory connection disposal remain in force.

Review only the fixed category, stage and Boolean flags from a future attempt.
Do not dump catalog rows or retry with another identity. Investigate the reported
stage before proposing any permission or policy change. No live rerun is part
of this task.

## Offline validation scope

Result: **420 tests passed**, including **22 new privilege-detail/launcher
regressions**; the focused preflight run passed 96 tests. PowerShell syntax
parsing, whitespace checks and SHA-256 comparison of all seven existing SQL
strings passed. The SQL strings are byte-for-byte identical to the inspected
baseline. The launcher was parsed and its embedded Python tested with mocks;
it was not launched against PostgreSQL.

`test_preflight_privilege_details.py` covers every role/effective privilege
rejection, catalog candidate predicates, permission denial at all four query
boundaries and startup, redaction and mutated exception metadata, PG18.6 normal
metadata, missing values and unconfirmed disposal. Additional launcher tests
verify explicit opt-in, pinned forwarding with the real preflight and a mocked
driver, fixed output, no extra queries/retries, and unchanged success behavior.
The complete existing suite is run with environment-file access, bot/config
imports, external network access and external processes blocked. These are
control-flow/metadata fixtures, not live PostgreSQL permission tests.
