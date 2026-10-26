# First disposable schema: operator procedure for review

**Prepared only. None of the Docker commands or SQL below has been executed.**
Review and authorize provisioning before running these blocks. Provisioning is
separate from authorizing database-backed tests; this procedure never runs them.

Reserved schema: `dao_it_ea92ae2483dd4ac494729f5628b429e2`.
The actual runner's offline `prepare_plan()` generated its UUID and plan, and
`read_plan()` validated that plan. Only the local, Git-ignored recovery directory
and plan were created. There is no known namespace OID until provisioning occurs.

The runner requires this exact plan/name, an independently recorded OID in
16384..4294967295, `dao_test_user` ownership, private ACL, an empty namespace,
and the pinned cluster. Adoption requires database CREATE to remain absent. The
plan reserves a name; it does not assert that a database schema already exists.

The permission model remains one administrator-provisioned namespace. Use an
existing authorized administrator with database CREATE, permission to SET ROLE
to `dao_test_user`, and access to the cluster identity metadata. No new grants
are supplied. The script briefly uses SET LOCAL ROLE as the schema owner for
schema ACL changes/removal, then restores the administrator role. It never
changes role membership, database grants, authentication or server configuration.
Keep other schema setup, gameplay tests and administrative DDL stopped throughout
provisioning and empty-schema recovery.

All database commands run through `docker exec` into the verified full container
ID. Publishing must be exactly `127.0.0.1:5433 -> 5432/tcp`, with no additional
published bindings on container port 5432. The container-local
psql client explicitly connects to the runner's pinned `172.17.0.2:5432` listener.
If that independently verified address has changed, stop for review. No host
PostgreSQL client or host database connection is used.

## 1. Verify the local reservation and Docker identity

Use the same PowerShell window for all blocks. Native command exit codes are
checked explicitly. No environment files or complete Docker environment/config
dumps are read. Passwords are never supplied through arguments or variables.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'C:\Users\Mr. Qureshi\Downloads\dao_of_eternity_v022_golden_bonds\dao_of_eternity_v022\dao_of_eternity_bot'

$daoSchema = 'dao_it_ea92ae2483dd4ac494729f5628b429e2'
$daoPlan = Join-Path $PWD "integration_recovery\$daoSchema.plan.json"
$daoReceipt = Join-Path $PWD "integration_recovery\$daoSchema.provisioning.json"
$daoPrecommit = Join-Path $PWD "integration_recovery\$daoSchema.precommit.json"
$daoRunnerJournal = Join-Path $PWD "integration_recovery\$daoSchema.json"
$daoSqlFile = (Resolve-Path -LiteralPath '.\provision_disposable_schema.psql').Path
$daoContainerSql = "/tmp/$daoSchema.provision.psql"
$daoContainerReceipt = "/tmp/$daoSchema.provisioning.json"
$daoContainerPrecommit = "/tmp/$daoSchema.precommit.json"

$daoPlanData = Get-Content -LiteralPath $daoPlan -Raw | ConvertFrom-Json
if ($daoPlanData.version -ne 1 -or $daoPlanData.schema -cne $daoSchema -or
    $daoPlanData.manifest_digest -cne '9c1af28dec0ea7eeb13641a08f48500a0ffcdc6b54056937dd1b11d0b0260c38' -or
    $daoPlanData.target.host -cne '127.0.0.1' -or $daoPlanData.target.port -ne 5433 -or
    $daoPlanData.target.database -cne 'dao_eternity_test' -or $daoPlanData.target.role -cne 'dao_test_user' -or
    $daoPlanData.target.server_address -cne '172.17.0.2' -or $daoPlanData.target.server_port -ne 5432 -or
    [string]$daoPlanData.target.system_identifier -cne '7699864005918920747') {
    throw 'Reserved plan changed. Stop for review.'
}
if ((Test-Path -LiteralPath $daoReceipt) -or (Test-Path -LiteralPath $daoPrecommit) -or
    (Test-Path -LiteralPath $daoRunnerJournal)) {
    throw 'Existing recovery evidence: use the recovery section; do not reprovision.'
}

$daoContainerId = [string](docker inspect --type container --format '{{.Id}}' dao-postgres18-test)
if ($LASTEXITCODE -ne 0 -or $daoContainerId -cnotmatch '^[0-9a-f]{64}$') {
    throw 'Container identity unavailable.'
}
$daoContainerFacts = [string](docker inspect --type container --format '{{.Name}}|{{.State.Running}}|{{.Config.Image}}|{{.Image}}' $daoContainerId)
if ($LASTEXITCODE -ne 0) { throw 'Container inspection failed.' }
$daoFacts = $daoContainerFacts -split '\|'
if ($daoFacts.Count -ne 4 -or $daoFacts[0] -cne '/dao-postgres18-test' -or $daoFacts[1] -cne 'true') {
    throw 'Unexpected container or container is stopped.'
}
$daoPortsText = docker inspect --type container --format '{{json .NetworkSettings.Ports}}' $daoContainerId
if ($LASTEXITCODE -ne 0) { throw 'Port inspection failed.' }
# BEGIN DAO POSTGRES BINDING VALIDATOR (also exercised by the offline regressions)
function Assert-DaoPostgresBinding {
    param([object]$PortJson)
    if ($PortJson -isnot [string] -or [string]::IsNullOrWhiteSpace($PortJson) -or $PortJson.Length -gt 65536) {
        throw 'Missing or ambiguous Docker port inspection output.'
    }
    # Docker Ports is a JSON object whose values are null or arrays of string-valued
    # binding objects. Validate that grammar before ConvertFrom-Json: Windows
    # PowerShell accepts some non-JSON syntax and silently overwrites duplicate keys.
    $daoJsonString = '"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    $daoJsonPair = "$daoJsonString[ \t\r\n]*:[ \t\r\n]*$daoJsonString"
    $daoJsonObject = "\{[ \t\r\n]*(?:$daoJsonPair(?:[ \t\r\n]*,[ \t\r\n]*$daoJsonPair)*)?[ \t\r\n]*\}"
    $daoJsonArray = "\[[ \t\r\n]*(?:$daoJsonObject(?:[ \t\r\n]*,[ \t\r\n]*$daoJsonObject)*)?[ \t\r\n]*\]"
    $daoJsonPort = "$daoJsonString[ \t\r\n]*:[ \t\r\n]*(?:null|$daoJsonArray)"
    $daoJsonMap = "\A[ \t\r\n]*\{[ \t\r\n]*(?:$daoJsonPort(?:[ \t\r\n]*,[ \t\r\n]*$daoJsonPort)*)?[ \t\r\n]*\}[ \t\r\n]*\z"
    if (-not [regex]::IsMatch($PortJson, $daoJsonMap,
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant, [timespan]::FromMilliseconds(250))) {
        throw 'Malformed Docker port inspection output.'
    }
    $daoTokens = [regex]::Matches($PortJson, "$daoJsonString|[{}\[\]:,]|null")
    $daoScopes = New-Object 'System.Collections.Generic.Stack[object]'
    for ($daoIndex = 0; $daoIndex -lt $daoTokens.Count; $daoIndex++) {
        $daoToken = $daoTokens[$daoIndex].Value
        if ($daoToken -ceq '{') {
            $daoScopes.Push([System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase))
        } elseif ($daoToken -ceq '}') {
            [void]$daoScopes.Pop()
        } elseif ($daoToken.StartsWith('"') -and $daoIndex + 1 -lt $daoTokens.Count -and
                  $daoTokens[$daoIndex + 1].Value -ceq ':') {
            $daoKey = ConvertFrom-Json -InputObject $daoToken -ErrorAction Stop
            if (-not $daoScopes.Peek().Add($daoKey)) { throw 'Duplicate or ambiguous Docker port metadata keys.' }
        }
    }
    $daoParsedPorts = ConvertFrom-Json -InputObject $PortJson -ErrorAction Stop
    $daoPortProperties = @($daoParsedPorts.PSObject.Properties)
    foreach ($daoProperty in $daoPortProperties) {
        if ($daoProperty.Name -cnotmatch '\A[1-9][0-9]{0,4}/(tcp|udp|sctp)\z' -or
            [int]($daoProperty.Name -split '/')[0] -gt 65535) {
            throw 'Unexpected Docker port metadata key.'
        }
        if ($daoProperty.Name -cmatch '\A5432/' -and $daoProperty.Name -cne '5432/tcp' -and
            $null -ne $daoProperty.Value -and $daoProperty.Value.Count -ne 0) {
            throw 'Unexpected additional PostgreSQL protocol publishing.'
        }
    }
    $daoPgProperties = @($daoPortProperties | Where-Object { $_.Name -ceq '5432/tcp' })
    if ($daoPgProperties.Count -ne 1 -or $daoPgProperties[0].Value -isnot [array] -or
        $daoPgProperties[0].Value.Count -ne 1) {
        throw 'Exactly one PostgreSQL published binding is required.'
    }
    $daoBinding = $daoPgProperties[0].Value[0]
    $daoBindingFields = @($daoBinding.PSObject.Properties.Name)
    if ($daoBindingFields.Count -ne 2 -or $daoBindingFields -cnotcontains 'HostIp' -or
        $daoBindingFields -cnotcontains 'HostPort' -or
        $daoBinding.HostIp -isnot [string] -or $daoBinding.HostIp -cne '127.0.0.1' -or
        $daoBinding.HostPort -isnot [string] -or $daoBinding.HostPort -cne '5433') {
        throw 'PostgreSQL publishing must be exactly 127.0.0.1:5433, with no additional bindings.'
    }
}
# END DAO POSTGRES BINDING VALIDATOR
Assert-DaoPostgresBinding -PortJson $daoPortsText
$daoNetworksText = docker inspect --type container --format '{{json .NetworkSettings.Networks}}' $daoContainerId
if ($LASTEXITCODE -ne 0) { throw 'Network inspection failed.' }
$daoNetworks = $daoNetworksText | ConvertFrom-Json
$daoAddresses = @($daoNetworks.PSObject.Properties | ForEach-Object { $_.Value.IPAddress })
if ('172.17.0.2' -notin $daoAddresses) { throw 'Pinned internal IPv4 address changed.' }

# This independent control-file reading precedes any database connection.
# pg_controldata uses the container's existing PGDATA setting, not an env file.
$daoControlLines = @(docker exec --user postgres --env LC_ALL=C $daoContainerId pg_controldata)
if ($LASTEXITCODE -ne 0) { throw 'Independent cluster control-file verification failed.' }
$daoIdentifierLines = @($daoControlLines | Where-Object { $_ -match '^Database system identifier:\s+[0-9]+\s*$' })
if ($daoIdentifierLines.Count -ne 1 -or
    $daoIdentifierLines[0] -notmatch '^Database system identifier:\s+7699864005918920747\s*$') {
    throw 'Cluster identifier differs from the independent pin.'
}

[pscustomobject]@{
    Container = 'dao-postgres18-test'; ContainerId = $daoContainerId
    Image = $daoFacts[2]; ImageId = $daoFacts[3]
    Client = '127.0.0.1:5433'; Internal = '172.17.0.2:5432'
    ClusterIdentifier = '7699864005918920747'; Schema = $daoSchema
} | Format-List

$daoAdminRole = Read-Host 'Existing authorized administrator role in this test cluster'
if ($daoAdminRole -cnotmatch '^[a-z_][a-z0-9_]{0,62}$' -or $daoAdminRole -ceq 'dao_test_user') {
    throw 'Use the independently known administrator role; no role discovery or grants are automated.'
}

# Reserve fresh staging paths; an existing file is a rejection, never overwrite it.
foreach ($daoStagingPath in @($daoContainerSql, $daoContainerReceipt, $daoContainerPrecommit)) {
    docker exec --user postgres $daoContainerId test '!' '-e' $daoStagingPath
    if ($LASTEXITCODE -ne 0) { throw 'Container staging path exists or cannot be checked.' }
}
docker cp -- $daoSqlFile "${daoContainerId}:$daoContainerSql"
if ($LASTEXITCODE -ne 0) { throw 'Could not stage the reviewed SQL file.' }
$daoLocalHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $daoSqlFile).Hash.ToLowerInvariant()
$daoRemoteHashLine = [string](docker exec --user postgres $daoContainerId sha256sum $daoContainerSql)
if ($LASTEXITCODE -ne 0 -or ($daoRemoteHashLine -split '\s+')[0] -cne $daoLocalHash) {
    throw 'Staged SQL differs from the reviewed local file.'
}

$daoPsql = @(
    'exec', '--interactive', '--tty', '--user', 'postgres', $daoContainerId,
    'env', '-u', 'PGSERVICE', '-u', 'PGSERVICEFILE', '-u', 'PGSYSCONFDIR', '-u', 'PGOPTIONS',
    'PGHOSTADDR=172.17.0.2',
    'psql', '-X', '-W', '-q', '--host=172.17.0.2', '--port=5432',
    "--username=$daoAdminRole", '--dbname=dao_eternity_test',
    "--set=dao_admin_role=$daoAdminRole", "--set=dao_container_id=$daoContainerId",
    "--file=$daoContainerSql"
)

# Read-only catalog inspection. Password is requested inside psql without echo.
& docker @daoPsql '--set=dao_action=inspect'
if ($LASTEXITCODE -ne 0) { throw 'Read-only database/role verification rejected; no provisioning.' }
```

Expected: the selected container name/full ID and image, the independent pinned
cluster identifier, then `IDENTITY_VERIFIED`, the exact database/role with their
actual OIDs, `database_create_disabled=true`, and a fixed-schema JSON result with
`status=ABSENT`. Finally: `INSPECTION_ONLY_NO_DDL`. PostgreSQL must report version
180006 (18.6) and the pinned server endpoint or the script rejects. Review the
administrator identity against the existing account you intended to use.
Missing, null, malformed, duplicate-key or ambiguous port metadata is rejected.
The only accepted PostgreSQL binding is the single exact IPv4 loopback binding:
wildcards, empty addresses, IPv6, extra host ports and duplicate bindings reject.
Other container ports are outside this PostgreSQL binding check; an unpublished
null/empty entry on another protocol is not an additional published binding.

The inspection only stages a credential-free file in the test container's /tmp;
it does not create database objects. Keep the private recovery directory and
the original plan. If the schema is PRESENT, stop and use recovery inspection.
The container-local `env` command removes inherited service/options overrides
only for this client process and pins its host address; it reads no env file.

## 2. Provision exactly this schema, after review and authorization

The batch script repeats its read-only verification, prompts for the full phrase
below, then rechecks identity, database/role OIDs, role policy and database CREATE
before DDL. Its only successful database changes are CREATE SCHEMA and REVOKE
PUBLIC on that schema, in one transaction. Any ordinary precommit error exits
the file because ON_ERROR_STOP is on; closing the connection rolls back that
transaction. Existing names are rejected: there is no IF NOT EXISTS reuse.

```powershell
& docker @daoPsql '--set=dao_action=provision'
$daoProvisionExit = $LASTEXITCODE

# Preserve the original precommit receipt even when the psql operation failed.
docker exec --user postgres $daoContainerId test '-f' $daoContainerPrecommit
if ($LASTEXITCODE -eq 0) {
    if (Test-Path -LiteralPath $daoPrecommit) { throw 'Keep the existing local precommit evidence.' }
    docker cp -- "${daoContainerId}:$daoContainerPrecommit" $daoPrecommit
    if ($LASTEXITCODE -ne 0) { throw 'Precommit copy failed; retain the original container evidence and inspect.' }
}
docker exec --user postgres $daoContainerId test '-f' $daoContainerReceipt
$daoHasReceipt = ($LASTEXITCODE -eq 0)
if ($daoHasReceipt) {
    if (Test-Path -LiteralPath $daoReceipt) { throw 'Keep the existing local receipt; do not overwrite evidence.' }
    docker cp -- "${daoContainerId}:$daoContainerReceipt" $daoReceipt
    if ($LASTEXITCODE -ne 0) { throw 'Receipt copy failed; retain container evidence and inspect before further actions.' }
}
if ($daoProvisionExit -ne 0 -or -not $daoHasReceipt) {
    throw 'Provisioning rejected or commit outcome uncertain. Retain evidence; use read-only recovery inspection.'
}

$daoRecord = Get-Content -LiteralPath $daoReceipt -Raw | ConvertFrom-Json
if ($daoRecord.phase -cne 'provisioned' -or $daoRecord.schema -cne $daoSchema -or
    $daoRecord.container_id -cne $daoContainerId -or
    $daoRecord.cluster_identifier -cne '7699864005918920747' -or
    $daoRecord.database -cne 'dao_eternity_test' -or $daoRecord.role -cne 'dao_test_user' -or
    ($daoRecord.namespace_oid -isnot [int] -and $daoRecord.namespace_oid -isnot [long]) -or
    [long]$daoRecord.namespace_oid -lt 16384 -or [long]$daoRecord.namespace_oid -gt 4294967295) {
    throw 'Receipt identity or namespace OID rejected. Stop for recovery review.'
}
foreach ($daoFlag in @('owner_verified', 'acl_private', 'public_access_absent', 'schema_empty', 'database_create_disabled')) {
    if ($daoRecord.$daoFlag -isnot [bool] -or $daoRecord.$daoFlag -ne $true) {
        throw 'Receipt safety verification failed.'
    }
}
$daoNamespaceOid = [uint32]$daoRecord.namespace_oid
[pscustomobject]@{ Plan = $daoPlan; Schema = $daoSchema; NamespaceOid = $daoNamespaceOid; Receipt = $daoReceipt } | Format-List
```

At the prompt, the exact confirmation is:

```text
PROVISION dao_it_ea92ae2483dd4ac494729f5628b429e2
```

Expected output includes `PRECOMMIT_NAMESPACE_OID=<actual integer>`, then
`PROVISIONED` with the same actual OID and all five Boolean flags true. No OID
has been invented or reserved offline. The receipt contains the actual namespace,
owner and database OIDs plus the fixed cluster/container identity. The runner
later consumes `$daoPlan` and `$daoNamespaceOid`; it does not consume this receipt.
The final receipt has a `.provisioning.json` suffix. The separate, original
`.precommit.json` receipt is preserved permanently; success never overwrites it.
Neither can collide with the runner's exclusive `<schema>.json` journal.

**Stop after recording this result. Do not launch the integration runner.**
The original global catalog preflight can reject an owned prepared schema; the
runner uses its existing exact-namespace exception and rechecks the full policy.
Do not weaken the global preflight to make an owned run schema pass it.

## 3. Failure and recovery

Do not repeat the provisioning command after an error, cancellation, timeout or
lost response. Retain the plan, both local/container receipts and console OID. Before
COMMIT, an ordinary script failure rolls back both schema creation and its ACL
change. Around COMMIT, the outcome may be unknown. The original precommit receipt
records `phase=commit_unknown`; a separate `phase=provisioned` receipt is written
only after a successful commit and read-only postcommit check. A disconnect or
crash can also lose a receipt; absence alone proves nothing.

To investigate, use the already verified full container ID and staged batch file:

```powershell
& docker @daoPsql '--set=dao_action=inspect'
if ($LASTEXITCODE -ne 0) { throw 'Recovery identity check failed. Stop for administrator review.' }
```

After restarting PowerShell, independently reverify the named container, published
mapping, IPv4 address and control-file pin using the same read-only commands in
section 1, then rebuild `$daoPsql` for the retained staging file. Do not rerun the
fresh-path staging block or overwrite any original receipt. Keep the original
container ID: a replacement container requires review even if its name matches.

- `ABSENT`: no schema-removal action is needed. Retain the failed reservation for
  review; a later attempt should use a fresh plan/name.
- `PRESENT`: compare its exact actual OID with the precommit console OID or retained
  receipt. Owner/ACL/emptiness and cluster/database/role must all be verified.
- Unknown OID, populated schema, changed identity, unexpected ACL or an existing
  runner journal: stop for manual review. This provisioning procedure cannot
  clean up failed database-backed runs.

Only after separate approval to remove a proven empty provisioned schema, use
the OID from the retained evidence. The following is optional recovery, not part
of successful provisioning; it has its own exact confirmation prompt.

```powershell
if (Test-Path -LiteralPath $daoRunnerJournal) { throw 'Runner evidence exists: use harness recovery review.' }
$daoRecoveryOidText = Read-Host 'Exact original namespace OID from retained precommit/receipt evidence'
if ($daoRecoveryOidText -cnotmatch '^[0-9]{1,10}$' -or
    [long]$daoRecoveryOidText -lt 16384 -or [long]$daoRecoveryOidText -gt 4294967295) {
    throw 'An independently recorded valid OID is required.'
}
& docker @daoPsql '--set=dao_action=drop_empty' "--set=dao_expected_oid=$daoRecoveryOidText"
if ($LASTEXITCODE -ne 0) { throw 'Empty-schema removal rejected or outcome uncertain. Retain evidence and inspect.' }
& docker @daoPsql '--set=dao_action=inspect'
if ($LASTEXITCODE -ne 0) { throw 'Post-removal read-only verification failed.' }
```

Confirmation: `DROP EMPTY dao_it_ea92ae2483dd4ac494729f5628b429e2`.
The batch rechecks identity and the original OID, ownership, ACL and namespace
dependencies, then drops only that empty schema with RESTRICT. Expected output
is `EMPTY_SCHEMA_REMOVED_RETAIN_PLAN_AND_RECEIPT`, followed by an inspection result
of `ABSENT`. No prefix cleanup, automatic retry, dependent-object removal or
changes to unrelated database objects are supplied. Staging files and receipts
are deliberately retained for review.

## Review basis and limits

Reviewed actual `integration_runner.prepare_plan/read_plan/run_suite`,
`IsolatedTestDatabase.adopt_empty/_check_namespace/_require_empty_namespace`,
the catalog ACL/emptiness predicates, and recovery-journal naming. The plan is
manifest-digest bound to the current reviewed declarations. The suite's existing
475 offline tests do not establish that this administrator procedure succeeds.
SQL remains statically reviewed and unexecuted; no container state, live schema
OID, administrative password or live result was obtained during preparation.
Correction checks: all 475 offline unittests passed again with zero guard
violations. `test_schema_provisioning.ps1` exercises the exact Markdown validator:
60 binding fixtures passed (5 allowed, 55 rejected), including duplicate JSON
keys that Windows PowerShell otherwise silently accepts. Its 28 static checks
passed, including parsing all four operator blocks, transaction failure handling,
inspection exit before DDL, administrator password prompting, fixed-schema scope,
OID/ACL guards and separate precommit/final receipts. The regression script also
passed syntax checking; Git whitespace checks passed for the changed files.
Run these additional offline checks from this directory with
`powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\test_schema_provisioning.ps1`.
This execution policy applies only to that child process; no persistent policy
change is needed. `provision_disposable_schema.psql` was statically reviewed and
left unchanged. No SQL syntax was validated by a PostgreSQL server, and no Docker
command was invoked. Live authentication, privileges, transaction outcomes and
receipt transfer remain unverified; retain the documented recovery evidence if
any future execution fails or its outcome is uncertain.

PostgreSQL references: [CREATE SCHEMA authority](https://www.postgresql.org/docs/18/sql-createschema.html),
[psql file execution, secure password prompt and ON_ERROR_STOP](https://www.postgresql.org/docs/18/app-psql.html),
[independent cluster control metadata](https://www.postgresql.org/docs/18/app-pgcontroldata.html),
[DROP SCHEMA RESTRICT](https://www.postgresql.org/docs/18/sql-dropschema.html).
