# Historical-schema operator procedure: review before execution

The executable entry point is retirement_operator.ps1 with retirement_operator.py
and the sealed retire_empty_schema.psql. INSPECT and future retirement require
separate authorization. None of the live commands below was run during preparation.
The old provisioning drop_empty block still refuses a runner journal; retain that
guard and all four original files. Never replay the old schema or journal.

## Complete INSPECT-only Windows PowerShell session

Use a normal interactive Windows PowerShell console under the Windows account
that can read the evidence. Supply the FULL path of the approved existing Python
executable with project dependencies. Windows can resolve both a real interpreter
and a Store alias; the launcher rejects ambiguous automatic selection. No new
environment, package installation, elevation or folder-permission change is used.
Do not use ISE, stdin redirection or an output pipeline: Docker/psql needs the
inherited console for its non-echoing -W password prompt. The administrator role
name below is not a password. After separate INSPECT authorization:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'C:\Users\Mr. Qureshi\Downloads\dao_of_eternity_v022_golden_bonds\dao_of_eternity_v022\dao_of_eternity_bot'

$daoPythonPath = Read-Host 'Full path of the approved existing python.exe (no surrounding quotes)'
$daoOriginalContainerId = Read-Host 'ORIGINAL full 64-character container ID independently recorded in BOTH receipts'
$daoAdminRole = Read-Host 'Independently known existing administrator role in dao-postgres18-test'
$daoRecordDirectory = Join-Path $PWD 'integration_recovery\operator_records'
$daoLauncher = (Resolve-Path -LiteralPath '.\retirement_operator.ps1').Path
$daoPowerShell = Join-Path $PSHOME 'powershell.exe'

& $daoPowerShell -NoProfile -ExecutionPolicy Bypass -File $daoLauncher `
    -Action Inspect -AllowReadOnlyConnection `
    -OriginalContainerId $daoOriginalContainerId -AdminRole $daoAdminRole `
    -PythonPath $daoPythonPath -RecordDirectory $daoRecordDirectory
$daoInspectExit = $LASTEXITCODE
if ($daoInspectExit -ne 0) {
    throw 'INSPECT rejected or interrupted. Preserve the reported operator directory; no automatic retry.'
}
```

All variables are initialized here; no prior Markdown section must be executed.
The process-only execution-policy override handles the locally observed Restricted
script policy without changing machine/user policy, original-file ACLs or the
parent shell's environment. Centrally enforced policy can still refuse execution.
The child receives individual arguments, including paths with spaces. See
[Microsoft's Windows PowerShell -File documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1).

Before any connection or password prompt, the workflow:

1. Uses the existing strict recovery-inspector validator to READ all four original
   files, bounded and with reparse-path/JSON/manifest/identity checks. Both receipts
   must agree on the database OID, original container ID, namespace OID 16390 and
   owner OID 16389. The original journal must remain creating_tables /
   rollback_unverified with empty inventory. Files are opened rb, never for writing.
2. Exclusively creates operation-<UUID> beneath RecordDirectory and evidence.json
   containing SHA-256 hashes of the exact four source byte sequences, their OIDs,
   original container/cluster pins and the raw batch hash. Every new record uses
   xb, flush and fsync; nothing is replaced, deleted or permission-adjusted. The
   record directory's parent must already exist and permit ordinary creation.
   A read-only ORIGINAL FILE is fine; an unwritable RECORD DIRECTORY stops before
   Docker. On denial, stop and choose an independently approved private record
   directory with an existing writable parent. Do not change original ACLs or elevate.
3. Freshly inspects ONLY Docker ID/name/running/port/network fields, never Config.Env
   or full inspect output. Requires the original full ID, running dao-postgres18-test,
   exactly one 127.0.0.1:5433 -> 5432/tcp binding, no additional PostgreSQL protocol
   publishing, one unambiguous network with IPv4 172.17.0.2 and no alternate IPv6
   address. Missing, malformed, duplicate and ambiguous metadata rejects.
4. Requires container-local postgres --version to report 18.6 and independently
   reads pg_controldata under LC_ALL=C for exactly one cluster ID
   7699864005918920747. The binary version precedes connection; SQL later checks
   the RUNNING server's version 180006. Every exec/copy uses the full pinned ID.
5. Verifies the normalized source seal and BOTH copies of the full canonical
   privilege guard in retire_empty_schema.psql. Uses atomic mkdir -m 0700 for a
   new unused container-local /tmp directory, copies the batch there, then requires
   sha256sum to match its exact raw local bytes. Old staging files/receipts stay put.
6. Repeats fresh Docker/control-file/version verification after staging, rereads the
   original bytes, and durably creates inspect.attempt.json with UNKNOWN, staged
   paths and snapshot hash before starting the one authorized psql process.

psql runs only INSIDE that container, using -X -W, fixed database dao_eternity_test
and internal endpoint 172.17.0.2:5432. Service/options/password overrides are
cleared and default_transaction_read_only starts on. Enter the administrator
password directly in psql's masked prompt. It never enters PowerShell/Python,
command arguments, environment variables or local records.

The SQL uses BEGIN READ ONLY and verifies the receipt database OID, live role OID
16389, exact schema name/OID 16390, owner, effective private ACL, schema USAGE/CREATE,
no relations/dependencies/extension placement, no active dao_test_user sessions,
role flags/memberships, disabled database CREATE and disabled event triggers.
SET LOCAL ROLE dao_test_user temporarily selects that role to run the SAME canonical
effective-privilege queries and SAME narrow built-in pg_settings exception as
preflight, then RESET ROLE restores the administrator. No grants or ownership change
occur. INSPECT commits only its read-only transaction and exits BEFORE the
retirement prompt and BEGIN READ WRITE.

Successful inspection emits a single literal row to a NEW private container-local
inspect.status file via psql --output. No catalog rows are copied out. The helper
requires both exit zero and that exact one-line marker, rereads all four originals,
then exclusively writes/fsyncs inspect.result.json. See
[psql output/password options](https://www.postgresql.org/docs/18/app-psql.html).

Expected milestones: the new operator directory, evidence_files_readable 4, the
verified database/owner OIDs, the masked administrator password prompt,
RETIREMENT_EMPTY_SCHEMA_CHECK_PASSED and RETIREMENT_INSPECTION_ONLY_NO_DDL. Final
JSON must report INSPECT_PASSED, evidence_unchanged true and historical rollback
UNKNOWN. It supplies an inspection_token ONLY after the result flush succeeds.
Retain that output privately with the new operator record. The token links future
readiness to this completed inspection; it is neither retirement approval nor a
database password. A readable partial success file alone is insufficient.

Rejections retain fixed category/stage information, with no copied exception text
or Docker stderr in operator records. Native psql keeps its direct console for
password entry; VERBOSITY=terse and SHOW_CONTEXT=never limit server error context.
Review native console output before sharing it. No retry, backend termination,
staging deletion or original-record update follows rejection. Partial records stay
partial; attempted/uncertain operations retain UNKNOWN and staged paths for review.

## Future retirement: separate approval required

Do not run this section as part of INSPECT. Obtain separate approval for this exact
empty schema, then establish an exclusive operator window: no suite, bot, test-role
clients, concurrent DDL or other administrator activity. RESTRICT rejects acquired
contents but cannot exclude a hostile concurrent DBA renaming/replacing names.

Keep the variables from the successful INSPECT session. Only after retirement
approval, use its successful result path and token, never a failed/UNKNOWN record:

```powershell
$daoInspectionRecord = Read-Host 'Full path of inspect.result.json from the successful authorized INSPECT'
$daoInspectionToken = Read-Host 'inspection_token from that INSPECT successful final output'
& $daoPowerShell -NoProfile -ExecutionPolicy Bypass -File $daoLauncher `
    -Action Retire -AllowReadOnlyConnection -AuthorizeRetirement `
    -OriginalContainerId $daoOriginalContainerId -AdminRole $daoAdminRole `
    -PythonPath $daoPythonPath -RecordDirectory $daoRecordDirectory `
    -InspectionRecord $daoInspectionRecord -InspectionToken $daoInspectionToken
$daoRetirementExit = $LASTEXITCODE
if ($daoRetirementExit -ne 0) {
    throw 'Retirement requires separately authorized read-only recovery review. Retain records; no retry.'
}
```

The helper requires the matching snapshot/batch and correct inspection token. It
creates a separate operation directory and an exclusive, SINGLE-USE
retirement.authorization.json beside the prior inspection result. It repeats all
fresh evidence/Docker/staging checks and durably writes retire.attempt.json with
UNKNOWN before connecting. A consumed inspection cannot be reused; even an early
failure may consume it. Do not reset/delete that record or try again automatically.

The batch repeats read-only inspection, securely prompts for the administrator
password, and requires this separate exact confirmation:
RETIRE EMPTY dao_it_ea92ae2483dd4ac494729f5628b429e2 OID 16390 OWNER 16389.
It then enters ONE READ WRITE transaction, repeats all identity, ownership, ACL,
emptiness, quiescence and FULL effective-privilege checks with fresh observations,
locally selects the pinned owner and issues exactly:
DROP SCHEMA "dao_it_ea92ae2483dd4ac494729f5628b429e2" RESTRICT;

ON_ERROR_STOP stays on and ON_ERROR_ROLLBACK off; lock/statement/idle transaction
timeouts remain 3/10/30 seconds. Errors stop the batch without savepoint continuation.
After commit, a separate READ ONLY transaction must prove BOTH name and original
OID absent, with identity and database CREATE still disabled. Only then is the fixed
absence marker produced. Exit zero, exact marker, unchanged evidence and a durably
persisted new result are required for ABSENCE_OBSERVED. Lost commit responses,
failed postcommit checks and failed local flushes remain UNKNOWN. Preserve original
and new records for a separately authorized read-only administrator inspection.
Retirement never proves the old rollback; its historical verification stays UNKNOWN.

Fresh provisioning still requires a NEW plan/name, actual new OID and new receipts,
as described in POSTGRES_NEXT_STEP_REVIEW.md. The original schema and journal are
permanently retired reservations. Nothing in this workflow creates a fresh schema
or authorizes integration tests.

## Validation and readiness

Ready for a separately authorized INSPECT-only execution. The current directory
ACLs, real Docker/TTY/password behavior and live SQL catalog results were not
exercised here; local read/write permission checks precede Docker and fail closed.
The retirement path remains separately authorized and its first live use still
requires review of the strengthened SQL batch and an exclusive operator window.

668 offline Python unittests passed (630 previous and 38 new), with no failures,
errors, skips or audit-guard violations. Tests used synthetic evidence and mocked
Docker/psql processes; the guard blocked actual evidence/environment-file access,
real database connections, network and subprocess launches. The separate Windows
PowerShell test invoked only the pure argument builder and standard-library Python
argv echo: all 12 native argument fixtures passed, including spaces/ampersands and
case variants. Existing checks passed 60 binding fixtures and 28 static consistency
checks. Both new Python files passed AST/compile checks, the launcher parsed, all
three proposed Markdown PowerShell blocks parsed without invocation, and Git
whitespace checks passed for all seven files in this update. SQL tests checked
canonical policy equality, source sealing, conditionals, rejection paths, commit
boundaries and completion-row ordering; no PostgreSQL/psql parser or SQL execution
was used. No original recovery evidence or environment file was opened during
development; no Docker command, database connection, integration test, provisioning
or retirement operation was run.
