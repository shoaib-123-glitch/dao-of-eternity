# Recovery diagnostics and read-only inspection: review before execution

Prepared offline only. No Docker command, PostgreSQL connection, SQL execution or
database-backed case was run. Initial implementation used synthetic evidence only.
Subsequent preconnection triage read bounded local plan metadata; opening the journal
and both receipts returned access denied in the agent context. No evidence was
changed. Tests continue to use synthetic evidence in temporary directories.

The failed reservation remains `dao_it_ea92ae2483dd4ac494729f5628b429e2`, namespace
OID `16390`, owner OID `16389`, `creating_tables` / `rollback_unverified`. Its empty
table-OID list does not establish whether any CREATE statement was attempted.

## Diagnostics for future attempts

The existing suite remains disabled without explicit write authorization. This
change does not authorize another write attempt or permit replay of this reservation.
Both setup paths save fixed diagnostic stages before search-path setup, each of
the 27 manifest CREATE statements, inventory fetch/validation and commit. Connection,
identity, namespace, write enablement and journal persistence have separate stages.
Only fixed categories, a manifest table name and ordinal 1..27 can be reported.
Unknown/untrusted metadata is suppressed; no exception messages or SQL are copied.

New journals add an optional `diagnostic` field. No old journal is migrated.
`rollback` distinguishes `NOT_ATTEMPTED`, `UNKNOWN`, `ACKNOWLEDGED` and `RAISED`.
`rollback_verification` remains `UNKNOWN`: a returned driver call is not independent
verification of server state. Unknown commit outcomes remain unknown; no retries
or destructive rollback cleanup are introduced. Original cancellation is preserved.
Connection close acknowledgement, termination fallback and unconfirmed closure are
recorded separately. If a journal checkpoint fails, later journal writes stop;
the error retains sanitized in-memory metadata and the prior disk evidence remains.
The runner prints this sanitized metadata even when the checkpoint could not be saved.

## Read-only inspector

`integration_inspector.py` is separate from setup and cleanup. Default invocation
does not read evidence, prompt or connect. There is no write-authorization option.
The API also requires the exact Boolean `allow_read_only_connection=True`.

Before connection, the inspector validates the plan, existing journal, original
precommit receipt and final provisioning receipt. All four must share one directory
and approved schema name. It rejects duplicate JSON keys, unknown fields/diagnostics,
missing/oversized files, symlink/reparse paths, mismatched OIDs, receipt identities,
manifest digest, database/role, cluster identifier or independently supplied full
container ID. Legacy journals without diagnostics are accepted without rewriting;
their historical rollback/cleanup status is reported as `UNKNOWN`.

The receipt writer passes PostgreSQL `oid` values to `jsonb_build_object`, which
encodes them as decimal strings. Receipt OIDs accept either JSON integers or that
canonical ASCII decimal representation, always within 16384..4294967295 and with
exact identity matching. Whitespace, signs, leading zeros, Unicode digits, floats,
Booleans and mismatched values reject. Journal OIDs and independent CLI/API OIDs
remain strictly integers. This normalization occurs in memory; original receipt
bytes are retained and compared unchanged. UTF-8 BOM text is read without rewriting;
other invalid encodings reject. Receipt keys, phases and all five true flags remain
mandatory. See PostgreSQL's [type categorization](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/jsonfuncs.c#L5643)
and [JSONB scalar encoding](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/jsonb.c#L764).

Connection fields are fixed to `127.0.0.1:5433`, `dao_eternity_test`, `dao_test_user`.
Every successful inspection verifies server `172.17.0.2:5432`, PostgreSQL 18.6 and
cluster identifier `7699864005918920747`, plus the existing effective privilege
policy with only the exact namespace exception. Database CREATE must remain absent.
The connection starts read-only and enters a repeatable-read, READ ONLY transaction.
A reader with no execute API permits only fixed catalog SELECTs and exact arguments.
The inspector never sets a writable search path, invokes gameplay or constructs a
RecoveryJournal. Connection disposal is mandatory on success, error and cancellation.

The output contains fixed status labels and Boolean flags for database OID,
namespace/owner OIDs, effective schema USAGE/CREATE, private ACL, absence of relations,
namespace dependencies, extension placement and other guard sessions. It returns
no catalog definitions, player rows, SQL text or server exception messages.
All input bytes are compared again before returning; nothing is repaired or saved.

- `EMPTY_SCHEMA_OBSERVED`: all three emptiness checks pass and no other matching
  guard session was observed. This is not authorization to reuse or drop the schema.
- `OBJECTS_OR_SESSIONS_PRESENT`: review surviving objects or sessions manually;
  no actions or retries follow.
- `RECOVERY INSPECTION REJECTED [...]`: identity, privileges, evidence, metadata,
  transaction or connection cleanup could not be established. Missing/replaced
  namespaces reject. Preserve the evidence and stop.

## Proposed operator invocation, only after separate authorization

First independently reverify the ORIGINAL full container ID (not only its name),
the sole loopback mapping `127.0.0.1:5433 -> 5432/tcp`, the internal endpoint and
cluster pin. Compare that ID with both retained receipts. The inspector verifies
the cluster over PostgreSQL but cannot attest Docker's current runtime ID itself.
Do not rerun fresh provisioning/staging commands or remove the runner journal.

From the project directory in the approved existing Python environment:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$daoOriginalContainerId = Read-Host 'Independently reverified ORIGINAL full 64-character container ID'
if ($daoOriginalContainerId -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Full original container ID required.' }
python -B -E -s .\integration_inspector.py inspect `
    --allow-read-only-connection `
    --plan '.\integration_recovery\dao_it_ea92ae2483dd4ac494729f5628b429e2.plan.json' `
    --namespace-oid 16390 --owner-oid 16389 `
    --container-id $daoOriginalContainerId
if ($LASTEXITCODE -ne 0) { throw 'Read-only recovery inspection did not complete; retain evidence.' }
```

The password is requested securely with getpass; echoed-input fallback rejects.
No environment files or credential environment variables are loaded. The command
does not change the four input files or produce replacement receipts. Preserve its
sanitized output separately using the operator's normal review process.

## Limits

The old journal cannot reveal which operation originally failed. Catalog reads
observe visible state at one snapshot; uncommitted DDL, concurrent activity, future
changes and the historical rollback result cannot be disproved by an empty result.
Other guard sessions prevent the empty status, but application names alone cannot
identify every possible administrative actor. Keep administrative DDL and test
writers stopped while interpreting the observation. No cleanup/reuse decision is
made by this tool. File checks do not defend against a malicious filesystem owner.
Server execution, privileges and SQL compatibility remain unverified offline.

## Preconnection rejection triage

`creating_tables` / `rollback_unverified`, an empty table-OID list and a null
inventory digest are accepted for historical inspection. They do not require a
verified rollback or a newly added diagnostic field. The original generic SAFETY
output hid which local check failed; it also covered refusal of getpass's echoed
input fallback, which can fail before a visible password prompt.

Preconnection evidence failures now report only an allowlisted category and source
label, for example `RECOVERY INSPECTION REJECTED [FILE_ACCESS_DENIED] source=JOURNAL`.
Sources are INPUT, PLAN, JOURNAL, PRECOMMIT, RECEIPT, MANIFEST, CREDENTIALS or UNKNOWN.
No path, container-ID value, catalog content, JSON value, SQL, password or exception
message is printed.

| Category | Rejection condition |
| --- | --- |
| ARGUMENTS / EVIDENCE_PATH | OID arguments or full lowercase 64-hex container ID invalid; plan name/path not approved |
| FILE_MISSING / FILE_ACCESS_DENIED / FILE_IO | An evidence file or its parent cannot be inspected/opened; access denied is distinct from missing or other I/O errors |
| FILE_TYPE_OR_SIZE / FILE_REPARSE | Not a regular file, empty, above 65536 bytes, symlink or Windows reparse path |
| FILE_ENCODING / FILE_JSON / DUPLICATE_JSON_KEY | Invalid UTF-8, malformed JSON or duplicate keys |
| PLAN_FORMAT / PLAN_TARGET | Plan keys, version or schema rejected; target fields/types differ from the pinned Docker target |
| MANIFEST_REVIEW | Manifest digest/source hashes drifted, SCHEMA literals cannot be read/parsed, or current manifest cannot be established |
| JOURNAL_FORMAT / JOURNAL_IDENTITY | Journal keys or database/role/schema/numeric OIDs differ from the approved identity |
| JOURNAL_STATE | Phase or outcome is outside the reviewed allowlists; the reported historical state is on both lists |
| JOURNAL_INVENTORY / JOURNAL_DIAGNOSTIC | Invalid table list/OIDs, digest or optional sanitized diagnostic metadata |
| RECEIPT_FORMAT | Missing/extra fields, wrong precommit/final phase, or an unsupported receipt structure |
| RECEIPT_OID_FORMAT / RECEIPT_IDENTITY | Unsupported OID representation/range, or schema/container/cluster/database/role/namespace/owner mismatch |
| RECEIPT_DATABASE_MISMATCH / RECEIPT_FLAGS | Receipts disagree on database OID, or a required final receipt flag is not Boolean true |
| EVIDENCE_CHANGED | Evidence bytes differ from the initial read; nothing is repaired or overwritten |
| CREDENTIAL_PROMPT | Secure password prompting was unavailable, including refused echoed-input fallback |
| VALIDATION_UNAVAILABLE | An unexpected local validation failure; its exception text is suppressed |

Both receipt files are required in the same directory as the plan and journal.
The reviewed writer emits the expected field names; the confirmed format defect
was its string OIDs versus the initial reader's integer-only assumption. Initial
fixtures used integers and missed that mismatch. Current tests pin the writer's
fields/casts and exercise both legitimate receipt representations.

The agent-context access denials demonstrate another possible pre-prompt blocker;
they do not establish permissions in the operator's separate shell. No permission
changes, elevation or another inspector run were performed to investigate further.
Because those three files could not be read here, their actual format, values and
the exact original operator-side rejection remain unconfirmed.

Python `-B -E -s` disables bytecode writes, ignores PYTHON* environment variables
and excludes user-site packages. It does not alter file ACLs, JSON types or the
allowlisted journal states. Missing dependencies caused by selecting a different
environment would normally fail during module import, before this custom rejection
message. Keep the reviewed flags and use the approved interpreter; see the
[Python command-line documentation](https://docs.python.org/3/using/cmdline.html).

## Post-prompt rejection triage

The later operator report confirms that all four files were readable and the
secure password prompt was reached. Prior agent-context file-access denials do
not explain that new attempt. No existing evidence was read again during this
post-prompt investigation, and the live inspector was not rerun.

The historical journal state remains accepted before and after prompting. After
the initial validation, the inspector constructs the fixed test configuration and
validates the same evidence again. It never requires the old rollback to become
verified. Unknown rollback remains UNKNOWN in the observation report.

Previously, authentication, connection, cluster identity and effective-privilege
failures normally retained their existing specific categories. The inspector's
prepared-role checks, namespace checks, recovery-query failures, metadata checks,
final read-only check and uncategorized transaction failures could instead collapse
to SAFETY. A rollback exception could also replace the original rejection. Thus
the original SAFETY line cannot identify the failed check or prove authentication
succeeded. With the supplied target matching the approved pins, inspector-only
schema/catalog checks and transaction exits are the most useful remaining suspects.

Post-prompt errors now include an `inspection_diagnostic` JSON object containing
only fixed category/stage names, allowlisted failed-check labels, transaction-exit
and connection-cleanup status, and bounded secondary failures. Existing privilege
diagnostics contribute only their fixed stage and allowlisted Boolean flags. No
exception text, SQLSTATE text, SQL, passwords, DSNs, paths, actual OID values or
unrestricted rows are printed. Output fields are revalidated when printed.

| Stage | Check or exception path |
| --- | --- |
| CONFIGURATION / EVENT_LOOP | Fixed configuration construction, target validation or event-loop startup fails |
| EVIDENCE_REVALIDATION | Evidence validation fails after prompting; original fixed evidence category/source is preserved |
| CONNECTION | Authentication, transport, startup settings, startup privilege denial, timeout or cancellation |
| TRANSACTION_BEGIN | Creating/entering the explicit repeatable-read READ ONLY transaction fails |
| SESSION_IDENTITY | Database, current/session role, listener endpoint, primary/temp/read-only state, supported catalog version or initial role metadata/policy fails |
| CLUSTER_IDENTITY | Cluster metadata is unreadable, malformed or differs from the independent pin |
| EFFECTIVE_PRIVILEGES / CATALOG_WRITE_POLICY | Existing effective privilege/default-ACL checks or the narrow standard pg_settings exception reject; query failures remain distinguishable from policy through the fixed privilege stage |
| PREPARED_ROLE_POLICY | Database CREATE is present, version differs from 18.6, or role OID differs from the independently supplied owner OID |
| SCHEMA_CATALOG | Reading the exact namespace catalog row fails |
| SCHEMA_VERIFICATION | Namespace absent/replaced, owner/ACL mismatch, or malformed namespace metadata |
| RECOVERY_CATALOG | The fixed recovery observation query fails; it includes namespace and session expressions, so a query exception does not identify an individual internal expression |
| RECOVERY_METADATA | Recovery flag names/types are incomplete, unexpected or unknown |
| DATABASE_IDENTITY | Database OID differs from both verified receipts |
| RECOVERY_SCHEMA_POLICY | Namespace existence/identity or private ACL flag fails |
| SCHEMA_ACCESS_POLICY | Effective USAGE or CREATE on the private schema is absent; database-level CREATE must still remain absent |
| SESSION_INSPECTION | The guard-session Boolean alone has an unknown type; missing fields use RECOVERY_METADATA. A valid false no_other_guard_sessions flag produces OBJECTS_OR_SESSIONS_PRESENT rather than rejection |
| FINAL_READ_ONLY | Final session/default read-only metadata is missing or drifted |
| TRANSACTION_COMMIT / TRANSACTION_ROLLBACK | Read-only transaction exit raises; rollback failure is secondary when a prior check already failed |
| EVIDENCE_RECHECK | Original evidence bytes changed or cannot be reread after the observation |
| REPORT | Result construction or JSON output fails |
| CONNECTION_CLEANUP | Graceful close fails and termination cannot be confirmed; prior failure remains primary if present |
| QUERY_ALLOWLIST | An unreviewed query or argument shape is refused before a driver call |

Categories distinguish AUTHENTICATION, CONNECTION, IDENTITY, PRIVILEGES,
SCHEMA_VERIFICATION, CATALOG_COMPATIBILITY, READ_ONLY, VERSION, TRANSACTION,
TIMEOUT, CONNECTION_CLEANUP, CONFIGURATION, EVIDENCE, CANCELLED and SAFETY.
Categories describe the rejection boundary; they do not authorize permission changes.

The original failure remains primary, with at most two separately sanitized cleanup
failures. Transaction exits distinguish BEGIN_RAISED, COMMIT_ACKNOWLEDGED,
ROLLBACK_ACKNOWLEDGED, COMMIT_RAISED and ROLLBACK_RAISED. These describe driver calls
on the inspector's read-only transaction, not independent verification of the old
integration transaction. Connection cleanup distinguishes ACKNOWLEDGED, TERMINATED
and UNCONFIRMED; cancellation drains the existing disposal worker and propagates.
There is no reconnect, transaction retry, extra catalog query, writable transaction
or journal/receipt update. All previous cluster, role, network and evidence checks
remain in force.

Offline checks: run the full unittest suite, including
`test_integration_setup_diagnostics` and `test_integration_inspector`. All connections
in these tests are doubles; SQL-shaped strings are recorded, never sent to a server.

Initial implementation validation: 535 Python unittests passed (475 existing and 60 new),
with zero failures, errors, skips or audit-guard violations. The guards blocked
environment-file access, access to the original recovery directory, real driver
connections, external network use and subprocess launches. Python syntax checks
passed for all nine changed/new Python files. The existing PowerShell regressions
also passed: 60 binding fixtures and 28 static consistency checks. PowerShell
operator blocks were parsed without invocation, and Git whitespace checks passed.

Preconnection triage update validation: all 555 Python unittests passed, including
20 additional regressions (54 inspector tests in total), with zero failures, errors,
skips or audit-guard violations. Tests ran under `python -B -E -s` and prevented
original-evidence access, real database connections, external network calls and
subprocess launches. Both modified Python files passed syntax checks; Git whitespace
checks passed. No live inspector invocation or database-backed case was repeated.

Post-prompt triage update validation: 593 Python unittests passed (555 previous
and 38 new), with zero failures, errors, skips or audit-guard violations. The new
tests cover each post-prompt rejection category, stage/check sanitization, the
accepted historical journal, and cleanup/cancellation with preserved primary and
secondary failures. Tests used only synthetic evidence and driver doubles under
`python -B -E -s`; access to original evidence/environment files, real connections,
external network calls and subprocess launches was blocked. All three modified/new
Python files passed syntax checks, and Git whitespace checks passed. The catalog
queries and database privilege policy were not changed. No existing evidence was
opened or modified during this update, and no live inspector was run.

## SCHEMA_VERIFICATION metadata-shape rejection

The supplied CATALOG_COMPATIBILITY / SCHEMA_VERIFICATION diagnostic with only
metadata_shape_valid failed identifies a Python row-validation defect. With the
normal asyncpg driver, the old validator rejects a valid result unconditionally:
Record iteration yields values, whereas dictionary iteration yields keys. The
earlier dictionary doubles concealed this difference. An offline reproduction
using an actual asyncpg 0.32.0 Record confirms it; no database was contacted.
This matches the documented [asyncpg Record interface](https://magicstack.github.io/asyncpg/current/api/index.html#record-objects).

The exact query remains NAMESPACE_SQL in integration_catalog.py, passed through
_CatalogReader.fetchrow with the validated schema name as its only parameter.
The SELECT returns n.oid, n.nspowner AS owner, and NOT EXISTS over exploded schema
ACL entries AS acl_safe. COALESCE applies the default schema ACL when nspacl is
NULL. No unrestricted catalog columns or ACL entries leave the inspector.

| Result field | PostgreSQL type | Expected Python type | Nullability and meaning |
| --- | --- | --- | --- |
| oid | oid | exact int | Non-null catalog row identifier; must equal the independently supplied namespace OID |
| owner | oid | exact int | Non-null owner identifier; must equal the independently supplied owner OID |
| acl_safe | boolean | exact bool | NOT EXISTS always yields true or false; must be true to establish the private ACL policy |

There is at most one namespace row because its name is unique. An absent row is
returned by fetchrow as None and remains a namespace_present rejection. A present
row must have exactly these three named fields. The PostgreSQL 18
[namespace catalog](https://www.postgresql.org/docs/18/catalog-pg-namespace.html)
retains the expected OID fields, asyncpg's
[built-in OID codec](https://github.com/MagicStack/asyncpg/blob/v0.32.0/asyncpg/protocol/codecs/pgproto.pyx)
decodes them as unsigned integers, and PostgreSQL's
[EXISTS semantics](https://www.postgresql.org/docs/18/functions-subquery.html#FUNCTIONS-SUBQUERY-EXISTS)
establish the Boolean result. Nullable nspacl is handled inside the query and
cannot by itself explain a null or missing acl_safe result.

The old set(namespace) check compares the values against the field names before
checking scalar types or identity/ACL values. The same mistake occurred in the
subsequent recovery-observation validator, where iterating a Record produces
Boolean flag values rather than flag names. Correcting only the first site would
therefore expose a second false rejection at RECOVERY_METADATA.

The correction uses explicit .keys() at both sites and checks the exact field
count. It retains all exact scalar-type checks, rejects missing/extra fields,
and preserves every OID, owner, private-ACL, schema-access, emptiness, session,
cluster and privilege condition. No query, SQL cast, codec, connection option,
retry, read-only boundary, transaction or disposal implementation was changed.
No additional live metadata diagnostic is needed to establish this defect.

This stage also means that connection, cluster verification and the initial
privilege/prepared-role gates completed for that attempt. It does not mean that
schema ownership or ACL values passed: those comparisons were never reached.
ROLLBACK_ACKNOWLEDGED and connection cleanup ACKNOWLEDGED report successful
inspector disposal calls; they do not verify the historical integration rollback.
The old creating_tables / rollback_unverified journal state remains accepted.

The new test_inspector_record_shapes module constructs actual asyncpg Records
with the driver's internal test factory, used only in offline tests. Three valid
record scenarios failed before the correction and pass afterward. Sixteen new
tests cover both row validators, all-catalog Record results, exact field counts,
missing/extra fields, null/wrong scalar types, altered OIDs/owner/ACL, unknown
Boolean flags, nonempty/session observations, cluster refusal, final read-only
drift and byte-for-byte preservation of synthetic historical evidence. Existing
cleanup/cancellation regressions continue to apply unchanged. The installed
driver version is covered; live catalog/connection behavior and other installed
driver versions have not been exercised by this update.

Validation for this correction: 609 Python unittests passed (593 previous and
16 new), with zero failures, errors, skips or audit-guard violations. The suite
used python -B -E -s with original-evidence/environment-file access, real driver
connections, external network calls and subprocess launches blocked. Syntax
checks passed for both modified/new Python files. The existing PowerShell offline
checks passed all 60 binding fixtures and 28 static consistency checks; the test
process used a process-only execution-policy override, with no machine/user
policy change. Tracked and changed-file Git whitespace checks passed. No Docker
command, database connection, SQL execution, live inspection, database-backed
scenario or original-evidence access occurred during this correction.
