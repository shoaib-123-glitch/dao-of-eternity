# Controlled retirement and a fresh PostgreSQL integration reservation

Prepared offline for review. None of the commands below has been executed.
Schema retirement, fresh provisioning and database-backed tests each require
separate operator authorization. No retries or old-reservation reuse are proposed.

## Current observation and the historical failure

The operator reports EMPTY_SCHEMA_OBSERVED for
`dao_it_ea92ae2483dd4ac494729f5628b429e2`, namespace OID 16390, owner OID 16389.
The cluster, owner, private ACL, schema USAGE/CREATE, three emptiness checks and
absence of other guard sessions passed; inspection cleanup was acknowledged and
the four evidence files were unchanged. The original journal remains
`creating_tables` / `rollback_unverified`, with no table OIDs or inventory digest.

Setup adopts the exact empty namespace, checks it again inside the setup
transaction, records creating_tables, establishes the search path, issues all
27 manifest CREATE statements, fetches the catalog inventory, validates it, then
records the digest and table OIDs before requesting commit. There is no durable
table-by-table inventory checkpoint in the old journal. Thus its empty list can
describe failure before any CREATE, during any CREATE, or after all 27 CREATEs.
A failed journal checkpoint can also leave an older phase on disk. Connection
loss, timeout, cancellation, search-path rejection, schema permission/identity
change and a transaction error remain historical possibilities. None is proven
by the old journal, and its missing diagnostics must not be reconstructed.

A confirmed compatibility defect would reject a correct post-CREATE inventory:
INVENTORY_SQL embeds c.relnamespace and t.typelem (both PostgreSQL oid values) in
JSONB. PostgreSQL emits those nested values as canonical decimal strings, whereas
top-level asyncpg OID fields decode as integers. The previous namespace comparison
therefore classified a valid ordinary table as an unapproved relation kind; fixing
that alone would expose the array-element comparison failure. PostgreSQL's
[JSON type categorization](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/jsonfuncs.c#L5643)
and [JSONB scalar encoding](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/jsonb.c#L764)
establish this representation. Two independent offline regressions reproduced
the failures before the correction. This is a plausible original failure path,
not proof that the historical attempt reached the inventory query.

The correction decodes only the two consumed nested OID fields for comparisons.
Only exact integers or canonical ASCII decimal strings in 0..4294967295 are
accepted. Nulls, Booleans, floats, whitespace, signs, leading zeros, Unicode digits
and overflow reject. Namespace and composite-type identities must still match
exactly. Raw JSON definitions are retained unchanged for setup/cleanup fingerprints;
no query, manifest DDL, source hash, plan digest, grant, journal phase, transaction,
rollback or connection-cleanup implementation was changed.

## Static review of all 27 declarations

Each generated statement is CREATE TABLE followed by the validated, quoted private
schema and a fixed, quoted manifest table name. All sources still match their
reviewed hashes. The manifest digest remains
`9c1af28dec0ea7eeb13641a08f48500a0ffcdc6b54056937dd1b11d0b0260c38`.

| Ordinal | Table | Columns | Primary key |
| --- | --- | --- | --- |
| 1 | qi_event_settings | 2 | guild_id |
| 2 | qi_event_announcements | 2 | guild_id, event_id |
| 3 | fast_sandboxes | 4 | guild_id, user_id |
| 4 | fast_sandbox_actions | 4 | guild_id, user_id, event_id |
| 5 | characters | 4 | guild_id, user_id |
| 6 | trial_sandboxes | 4 | guild_id, user_id |
| 7 | sandbox_actions | 4 | guild_id, user_id, event_id |
| 8 | character_origins | 5 | guild_id, user_id |
| 9 | processed_actions | 4 | guild_id, user_id, event_id |
| 10 | combat_sessions | 4 | guild_id, user_id |
| 11 | combat_actions | 4 | guild_id, user_id, event_id |
| 12 | player_inventory | 6 | guild_id, user_id |
| 13 | living_dao_players | 3 | guild_id, user_id |
| 14 | living_dao_actions | 4 | guild_id, user_id, event_id |
| 15 | living_dao_sects | 4 | guild_id, name |
| 16 | sect_hall_state | 3 | guild_id, sect_name |
| 17 | sect_hall_actions | 4 | guild_id, user_id, event_id |
| 18 | sect_member_roles | 4 | guild_id, sect_name, user_id |
| 19 | sect_profiles_v022 | 3 | guild_id, sect_name |
| 20 | sect_raid_v022 | 3 | guild_id, sect_name |
| 21 | sect_action_v022 | 4 | guild_id, user_id, event_id |
| 22 | sect_member_join_v022 | 4 | guild_id, user_id |
| 23 | sect_gathering_history_v022 | 4 | guild_id, sect_name, effective_at |
| 24 | dao_households | 3 | guild_id, user_id |
| 25 | dao_household_actions | 4 | guild_id, user_id, event_id |
| 26 | dao_player_bonds | 7 | guild_id, proposer, recipient |
| 27 | dao_dual_requests | 5 | guild_id, proposer, recipient |

All 106 columns are NOT NULL, including the inline primary key in statement 1.
Types are built-in BIGINT, INT, TEXT, JSONB and TIMESTAMPTZ. Defaults are typed
JSONB empty objects, BIGINT zero, TEXT literals or built-in NOW(); the two CHECKs
are proposer <> recipient on statements 26 and 27. No declaration needs a foreign
key, sequence, extension, external schema, user function, inheritance, table copy
or application startup. No incompatible PostgreSQL 18.6 syntax was identified.
The offline grammar check consumes each whole declaration and compares column
order, nullability, default type, PK order and CHECK columns against TABLE_SHAPES.
It is a bounded static review, not a PostgreSQL backend parse or execution.
See the [PostgreSQL 18 CREATE TABLE grammar](https://www.postgresql.org/docs/18/sql-createtable.html).

The inventory expects 27 PKs, two CHECKs and 106 PostgreSQL 18 NOT NULL constraints,
with their column addresses and enforced/validated/local metadata. PostgreSQL 18's
[constraint catalog](https://www.postgresql.org/docs/18/catalog-pg-constraint.html)
includes contype n, conenforced and int2[] conkey. Those keys remain JSON numbers;
Boolean flags remain exact Booleans. Each table has one composite type and its
array type; owned TOAST relations/indexes are inventoried separately. Every object
must retain the approved owner, ACL and dependency policy. Cleanup fingerprints
include the raw object definitions, so later catalog drift still refuses drops.

Actual asyncpg.Record fixtures now exercise both the inventory validator and
mocked setup/cleanup. Native OID/class/parent/owner fields are integers, subids
are integers and definition is text (namespace ACL definition may be NULL).
The inventory uses named access and .get(), not iteration as field names. The
inspector's separate .keys() correction remains in place. Record/dictionary
fingerprints match for the same raw fields. See the
[documented Record interface](https://magicstack.github.io/asyncpg/current/api/index.html#record-objects).

Existing role permissions are sufficient for setup: CONNECT on this database and
USAGE/CREATE on its privately owned schema. Database-level CREATE stays disabled.
Automatic PK indexes, table row/array types and TOAST storage require no additional
administrator grant to the test role. Event triggers and unsafe default ACLs must
still be rejected by the existing preflight policy.

## Complete operator procedure

Use [POSTGRES_RETIREMENT_OPERATOR.md](POSTGRES_RETIREMENT_OPERATOR.md) and the
retirement_operator.ps1 entry point. That complete, independently tested workflow
replaces the earlier partial variable/staging/receipt examples. INSPECT is separately
authorized and cannot reach writable BEGIN. Future retirement needs additional
authorization, a completed inspection proof and a durable single-use attempt.
The SQL now repeats the full canonical effective-privilege guard under the test
role, including the original narrow pg_settings exception. Original evidence and
the existing provisioning recovery refusal remain intact.

## Fresh plan, fresh schema, separately approved tests

After confirmed retirement/absence, retain the old four files permanently. The
old inspector expects the namespace to exist, so its absence rejection is not a
post-retirement failure; use the dedicated administrator absence check instead.
Reserve a new local plan with the existing offline-only prepare action:

```powershell
python -B -E -s .\integration_runner.py prepare --recovery-directory '.\integration_recovery'
if ($LASTEXITCODE -ne 0) { throw 'Fresh offline plan reservation failed; stop for review.' }
```

This command is proposed only; it was not run here. It creates one unique plan with
exclusive file creation, no connection or SQL. Record the new generated dao_it_
name. A collision must fail; do not retry by deleting history. Verify its manifest
digest and all TARGET pins. No new name or OID has been reserved by this review.

The original provision_disposable_schema.psql and its Markdown commands are
hardcoded to the old schema. They must not be rerun unchanged. Prepare and review
a separate fresh provisioning batch and command copy with ONLY the new approved
schema/staging/receipt names substituted consistently; retain the old originals.
Repeat strict Docker/control-file and catalog checks. Only after separate
provisioning approval, create exactly one empty private schema owned by the same
test role, revoke PUBLIC schema access, and independently record its ACTUAL new
OID plus new precommit/final receipts in new exclusive files. Do not guess or
reuse 16390. Keep database CREATE disabled and never use an existing schema.

Before any separately authorized suite run, revalidate the fresh plan, new OID,
owner/private ACL, emptiness and all cluster/role/network/privilege gates. Do not
create a fake runner journal to reuse the old inspector: before first adoption use
the fresh provisioning batch's read-only catalog inspection. The suite run must
use the new plan/OID, explicit --allow-database-writes and secure test-role password
prompt. Its journal must be new. Preserve all five production-backed scenarios,
the deterministic concurrency schedules and no automatic retries. The rollback/
retry demonstration does not establish production serialization retries.

## What the observations establish

| Evidence/check | Establishes | Does not establish |
| --- | --- | --- |
| No pg_class rows for the pinned namespace | No visible relations in that catalog snapshot | Absence of routines/types, past DDL, rollback success, or future emptiness |
| No namespace pg_depend references and no extension placement, together with no pg_class rows | Current schema emptiness under the verified identity and catalog policy | Cause or exact point of the old setup failure |
| Namespace name/OID/owner, private effective ACL and cluster/database/role pins | Current target identity and access policy | Historical identity at every instant of the failed run |
| No guard sessions | No matching application-name session at observation time | Absence of other clients or later concurrent DDL |
| Retirement's no-test-role-session check plus exclusive operator window | A stronger prerequisite for controlled removal | Protection against a hostile concurrent administrator |
| ROLLBACK_ACKNOWLEDGED / connection cleanup ACKNOWLEDGED | Driver calls completed for the relevant session | Independent proof of the historical setup rollback |
| EMPTY_SCHEMA_OBSERVED or later absence | Current catalog state | Whether tables were never created, were transactionally rolled back, or were removed later |

The original historical rollback_verification stays UNKNOWN even after retirement.
Static review and offline doubles cannot certify live catalog output, permissions,
network binding, password behavior, transaction durability or actual concurrent
PostgreSQL scheduling. No live action is authorized by this document.

## Offline validation

Initial next-step review: 630 Python unittests passed (609 previous and 21 new), with zero failures,
errors, skips or audit-guard violations. The guarded python -B -E -s run blocked
original-evidence/environment-file access, real driver connections, external
network use and subprocess launches. Both modified/new Python files passed
AST/compile checks without bytecode writes. The existing PowerShell offline
checks passed 60 binding fixtures and 28 static checks; all three proposed
PowerShell blocks above parsed successfully without invocation. Git whitespace
checks passed for tracked changes and all four files in this update. The new
retirement batch received static checks only, not a PostgreSQL or psql execution.
No existing recovery evidence was opened or modified; no Docker command, database
connection, SQL execution, live inspector or database-backed case was run.

Complete operator-workflow update: 668 Python tests passed, including 38 additional
regressions, with zero audit-guard violations. Windows PowerShell passed 12 native
argument round trips; the existing 60 binding fixtures and 28 static checks also
passed. Syntax and Git whitespace checks passed. See
[POSTGRES_RETIREMENT_OPERATOR.md](POSTGRES_RETIREMENT_OPERATOR.md) for the complete
single-session INSPECT procedure, separate future retirement gates, record formats,
permission-failure behavior, validation scope and readiness statement.
