# Offline only: run the exact Markdown binding validator on synthetic JSON.
# Other operator commands and the psql file are parsed/read, never invoked.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$daoReview = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'POSTGRES_SCHEMA_PROVISIONING_REVIEW.md'))
$daoBatch = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'provision_disposable_schema.psql'))
$daoFragments = [regex]::Matches($daoReview, '(?ms)^# BEGIN DAO POSTGRES BINDING VALIDATOR[^\r\n]*\r?\n(.*?)^# END DAO POSTGRES BINDING VALIDATOR')
if ($daoFragments.Count -ne 1) { throw 'Expected exactly one binding validator in the operator procedure.' }
$daoFragment = $daoFragments[0].Groups[1].Value
$daoTokens = $null
$daoErrors = $null
$daoAst = [System.Management.Automation.Language.Parser]::ParseInput($daoFragment, [ref]$daoTokens, [ref]$daoErrors)
if ($daoErrors.Count -ne 0) { throw 'Binding validator has PowerShell syntax errors.' }
# Only define the pure validator. In particular, never evaluate the Docker setup block.
$daoDefinitions = @($daoAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true))
if ($daoDefinitions.Count -ne 1 -or $daoDefinitions[0].Name -cne 'Assert-DaoPostgresBinding') {
    throw 'Unexpected validator definition.'
}
foreach ($daoCommand in $daoAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    if ($daoCommand.GetCommandName() -notin @('New-Object', 'ConvertFrom-Json', 'Where-Object')) {
        throw 'Unexpected command in the offline validator fragment.'
    }
}
. ([scriptblock]::Create($daoFragment))

$daoGood = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'
$daoFixtures = @(
    @{ Name = 'exact binding'; Value = $daoGood; Allow = $true }
    @{ Name = 'reordered fields and whitespace'; Value = ' { "5432/tcp" : [ { "HostPort" : "5433", "HostIp" : "127.0.0.1" } ] } '; Allow = $true }
    @{ Name = 'unambiguous JSON escaping'; Value = '{"5432\/tcp":[{"Host\u0049p":"127.0.0.\u0031","HostPort":"5433"}]}'; Allow = $true }
    @{ Name = 'unrelated published container port'; Value = '{"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"8080"}],"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $true }
    @{ Name = 'unpublished other ports'; Value = '{"8080/tcp":null,"5432/udp":[],"5432/sctp":null,"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $true }
    @{ Name = 'IPv4 wildcard'; Value = '{"5432/tcp":[{"HostIp":"0.0.0.0","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'empty address'; Value = '{"5432/tcp":[{"HostIp":"","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'missing address'; Value = '{"5432/tcp":[{"HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'null address'; Value = '{"5432/tcp":[{"HostIp":null,"HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'nonstring address'; Value = '{"5432/tcp":[{"HostIp":127,"HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'different loopback address'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.2","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'nonloopback address'; Value = '{"5432/tcp":[{"HostIp":"172.17.0.2","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'hostname'; Value = '{"5432/tcp":[{"HostIp":"localhost","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'address whitespace'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1 ","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'IPv6 wildcard'; Value = '{"5432/tcp":[{"HostIp":"::","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'IPv6 loopback'; Value = '{"5432/tcp":[{"HostIp":"::1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'IPv4 mapped IPv6'; Value = '{"5432/tcp":[{"HostIp":"::ffff:127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'wrong published port'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5432"}]}'; Allow = $false }
    @{ Name = 'nonstring port'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":5433}]}'; Allow = $false }
    @{ Name = 'null port'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":null}]}'; Allow = $false }
    @{ Name = 'empty port'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":""}]}'; Allow = $false }
    @{ Name = 'missing port'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1"}]}'; Allow = $false }
    @{ Name = 'extra binding field'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433","Other":"unexpected"}]}'; Allow = $false }
    @{ Name = 'wrong property case'; Value = '{"5432/tcp":[{"hostip":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'duplicate identical binding'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"},{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'good plus wildcard'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"},{"HostIp":"0.0.0.0","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'wildcard plus good'; Value = '{"5432/tcp":[{"HostIp":"0.0.0.0","HostPort":"5433"},{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'good plus unspecified'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"},{"HostIp":"","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'good plus IPv6'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"},{"HostIp":"::1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'good plus second host port'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"},{"HostIp":"127.0.0.1","HostPort":"5434"}]}'; Allow = $false }
    @{ Name = 'additional PostgreSQL UDP publishing'; Value = '{"5432/udp":[{"HostIp":"127.0.0.1","HostPort":"5433"}],"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'duplicate PostgreSQL metadata keys'; Value = '{"5432/tcp":[{"HostIp":"0.0.0.0","HostPort":"5433"}],"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'duplicate identical PostgreSQL keys'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}],"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'duplicate HostIp keys'; Value = '{"5432/tcp":[{"HostIp":"0.0.0.0","HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'duplicate HostPort keys'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"9999","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'case alias duplicates'; Value = '{"5432/tcp":[{"hostip":"0.0.0.0","HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'escaped duplicate property'; Value = '{"5432/tcp":[{"Host\u0049p":"0.0.0.0","HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'escaped duplicate port key'; Value = '{"5432\/tcp":[],"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'missing PostgreSQL port'; Value = '{}'; Allow = $false }
    @{ Name = 'unpublished PostgreSQL port'; Value = '{"5432/tcp":null}'; Allow = $false }
    @{ Name = 'empty PostgreSQL array'; Value = '{"5432/tcp":[]}'; Allow = $false }
    @{ Name = 'binding object instead of array'; Value = '{"5432/tcp":{"HostIp":"127.0.0.1","HostPort":"5433"}}'; Allow = $false }
    @{ Name = 'null root'; Value = 'null'; Allow = $false }
    @{ Name = 'root array'; Value = '[{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}]'; Allow = $false }
    @{ Name = 'string root'; Value = '"5432/tcp"'; Allow = $false }
    @{ Name = 'missing inspection output'; Value = $null; Allow = $false }
    @{ Name = 'empty inspection output'; Value = ''; Allow = $false }
    @{ Name = 'whitespace inspection output'; Value = '   '; Allow = $false }
    @{ Name = 'multiple inspection records'; Value = @($daoGood, $daoGood); Allow = $false }
    @{ Name = 'already decoded object'; Value = [pscustomobject]@{}; Allow = $false }
    @{ Name = 'truncated JSON'; Value = '{"5432/tcp":['; Allow = $false }
    @{ Name = 'trailing JSON record'; Value = ($daoGood + '{}'); Allow = $false }
    @{ Name = 'stdout diagnostic prefix'; Value = ('warning: ' + $daoGood); Allow = $false }
    @{ Name = 'unquoted property'; Value = '{"5432/tcp":[{HostIp:"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'trailing comma'; Value = '{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433",}]}'; Allow = $false }
    @{ Name = 'JSON comment'; Value = '{/*comment*/"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'non-JSON whitespace'; Value = ([string][char]0x00a0 + $daoGood); Allow = $false }
    @{ Name = 'unknown port metadata key'; Value = '{"unexpected":null,"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'wrong protocol case'; Value = '{"5432/TCP":[{"HostIp":"127.0.0.1","HostPort":"5433"}]}'; Allow = $false }
    @{ Name = 'oversized inspection result'; Value = (' ' * 65537); Allow = $false }
)
$daoAcceptedCount = 0
foreach ($daoFixture in $daoFixtures) {
    $daoAccepted = $false
    try {
        $daoResult = @(Assert-DaoPostgresBinding -PortJson $daoFixture.Value)
        $daoAccepted = ($daoResult.Count -eq 0) # The validator must not emit catalog/metadata rows.
    } catch {
        $daoAccepted = $false
    }
    if ($daoAccepted -ne $daoFixture.Allow) { throw ('Binding regression failed: ' + $daoFixture.Name) }
    if ($daoAccepted) { $daoAcceptedCount++ }
}

$script:daoConsistencyChecks = 0
function Assert-DaoStatic {
    param([bool]$Condition, [string]$Label)
    if (-not $Condition) { throw ('Static consistency regression failed: ' + $Label) }
    $script:daoConsistencyChecks++
}
$daoBlocks = [regex]::Matches($daoReview, '(?ms)^```powershell\r?\n(.*?)^```')
Assert-DaoStatic ($daoBlocks.Count -eq 4) 'four operator blocks retained'
foreach ($daoBlock in $daoBlocks) {
    $daoBlockTokens = $null
    $daoBlockErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput($daoBlock.Groups[1].Value, [ref]$daoBlockTokens, [ref]$daoBlockErrors)
    Assert-DaoStatic ($daoBlockErrors.Count -eq 0) 'PowerShell block syntax'
}
$daoSchema = 'dao_it_ea92ae2483dd4ac494729f5628b429e2'
Assert-DaoStatic ($daoBatch.Contains('\set ON_ERROR_STOP on') -and $daoBatch.Contains('\set ON_ERROR_ROLLBACK off')) 'failures stop the batch without savepoint continuation'
Assert-DaoStatic ($daoBatch.Contains('\set dao_action inspect')) 'default action is inspection'
$daoWrite = $daoBatch.IndexOf('BEGIN READ WRITE;')
$daoWriteCommit = $daoBatch.IndexOf('COMMIT;', $daoWrite)
Assert-DaoStatic ($daoBatch.IndexOf('\quit') -lt $daoWrite -and $daoBatch.IndexOf('\echo INSPECTION_ONLY_NO_DDL') -lt $daoWrite) 'inspection exits before write transaction'
Assert-DaoStatic ([regex]::Matches($daoBatch, '(?m)^\s*BEGIN READ ONLY;').Count -eq 2) 'read-only initial and postcommit verification'
Assert-DaoStatic ($daoReview.Contains("'psql', '-X', '-W', '-q'") -and $daoReview.Contains("'--interactive', '--tty'")) 'password prompt is container-local and masked; psqlrc disabled'
Assert-DaoStatic ($daoReview.Contains("'env', '-u', 'PGSERVICE'") -and $daoReview.Contains("'PGHOSTADDR=172.17.0.2'")) 'connection overrides are constrained'
Assert-DaoStatic ([regex]::Matches($daoBatch, '(?m)^\s*CREATE SCHEMA "' + $daoSchema + '" AUTHORIZATION dao_test_user;').Count -eq 1) 'exactly one fixed schema creation'
Assert-DaoStatic ($daoBatch.Contains('REVOKE ALL ON SCHEMA "' + $daoSchema + '" FROM PUBLIC;')) 'PUBLIC revocation retained'
Assert-DaoStatic ($daoBatch.Contains('DROP SCHEMA "' + $daoSchema + '" RESTRICT;') -and $daoBatch -cnotmatch '\bCASCADE\b') 'recovery remains restrictive'
Assert-DaoStatic ($daoBatch -cnotmatch '(?m)^\s*(GRANT|ALTER|CREATE TABLE|TRUNCATE|DELETE|UPDATE|INSERT)\b') 'no unrelated database writes or grants'
Assert-DaoStatic ($daoBatch.Contains('n.oid = :''dao_expected_oid''::oid') -and $daoBatch.Contains('n.nspowner = :''dao_verified_role_oid''::oid')) 'recovery OID and owner checks retained'
Assert-DaoStatic ($daoBatch.Contains('pg_depend') -and $daoBatch.Contains('pg_extension') -and $daoBatch.Contains('aclexplode')) 'emptiness and effective ACL checks retained'
Assert-DaoStatic ([regex]::Matches($daoBatch, "NOT has_database_privilege\('dao_test_user', current_database\(\), 'CREATE'\)").Count -eq 5) 'database CREATE stays denied at all five verification points'
Assert-DaoStatic ([regex]::Matches($daoBatch, "system_identifier::text FROM pg_control_system\(\)\) = '7699864005918920747'").Count -eq 2) 'cluster identifier checked before and after operator review'
Assert-DaoStatic ($daoReview.Contains('^[0-9a-f]{64}$') -and $daoReview.Contains("'/dao-postgres18-test'")) 'full container ID and name verification retained'
$daoPortRead = $daoReview.IndexOf('$daoPortsText = docker inspect')
$daoPortExitCheck = $daoReview.IndexOf('if ($LASTEXITCODE -ne 0) { throw ''Port inspection failed.'' }')
$daoBindingCheck = $daoReview.IndexOf('Assert-DaoPostgresBinding -PortJson $daoPortsText')
Assert-DaoStatic ($daoPortRead -lt $daoPortExitCheck -and $daoPortExitCheck -lt $daoBindingCheck -and $daoBindingCheck -lt $daoReview.IndexOf('$daoNetworksText = docker inspect')) 'inspection failure and binding validation gate later Docker actions'
$daoPrecommitMarker = '\o /tmp/' + $daoSchema + '.precommit.json'
$daoFinalMarker = '\o /tmp/' + $daoSchema + '.provisioning.json'
Assert-DaoStatic ([regex]::Matches($daoBatch, [regex]::Escape($daoPrecommitMarker)).Count -eq 1 -and [regex]::Matches($daoBatch, [regex]::Escape($daoFinalMarker)).Count -eq 1) 'precommit and final receipts each written once to distinct files'
Assert-DaoStatic ($daoBatch.IndexOf($daoPrecommitMarker) -gt $daoWrite -and $daoBatch.IndexOf($daoPrecommitMarker) -lt $daoWriteCommit -and $daoBatch.IndexOf($daoFinalMarker) -gt $daoWriteCommit) 'receipt transaction ordering'
Assert-DaoStatic ($daoReview.IndexOf('$daoProvisionExit = $LASTEXITCODE') -lt $daoReview.IndexOf('# Preserve the original precommit receipt')) 'native failure status captured before receipt copying'
Assert-DaoStatic ($daoReview.Contains("if (Test-Path -LiteralPath `$daoPrecommit) { throw") -and $daoReview.Contains("if (Test-Path -LiteralPath `$daoReceipt) { throw")) 'existing local evidence is not overwritten'
$daoConditionalDepth = 0
foreach ($daoLine in ($daoBatch -split '\r?\n')) {
    if ($daoLine.Trim().StartsWith('\if ')) { $daoConditionalDepth++ }
    elseif ($daoLine.Trim() -ceq '\endif') { $daoConditionalDepth-- }
    if ($daoConditionalDepth -lt 0) { throw 'Unbalanced psql conditional.' }
}
Assert-DaoStatic ($daoConditionalDepth -eq 0) 'psql conditional nesting'
foreach ($daoFileText in @($daoReview, $daoBatch)) {
    $daoNames = @([regex]::Matches($daoFileText, 'dao_it_[0-9a-f]{32}') | ForEach-Object { $_.Value } | Sort-Object -Unique)
    Assert-DaoStatic ($daoNames.Count -eq 1 -and $daoNames[0] -ceq $daoSchema) 'reserved schema consistency'
}
'Binding fixtures passed: {0} ({1} allowed, {2} rejected)' -f $daoFixtures.Count, $daoAcceptedCount, ($daoFixtures.Count - $daoAcceptedCount)
'Static consistency checks passed: {0}' -f $daoConsistencyChecks
'Docker invocations: 0; PostgreSQL connections: 0; SQL execution: 0'
