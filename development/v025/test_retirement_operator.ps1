# Offline: parse the launcher, invoke only its pure argument builder, and pass
# synthetic arguments through Windows PowerShell's actual native argv boundary.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$daoSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'retirement_operator.ps1'))
$daoTokens = $null
$daoErrors = $null
$daoAst = [System.Management.Automation.Language.Parser]::ParseInput($daoSource, [ref]$daoTokens, [ref]$daoErrors)
if ($daoErrors.Count -ne 0) { throw 'Launcher syntax rejected.' }
$daoDefinitions = @($daoAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true))
if ($daoDefinitions.Count -ne 1 -or $daoDefinitions[0].Name -cne 'New-DaoOperatorArguments') {
    throw 'Expected one pure argument builder.'
}
. ([scriptblock]::Create($daoDefinitions[0].Extent.Text))
$daoPython = @(Get-Command python -CommandType Application -ErrorAction Stop)[0].Source
$daoCount = 0
foreach ($daoAction in @('Inspect', 'inspect', 'Retire', 'retire')) {
    foreach ($daoDirectory in @('', 'C:\Dao operator records\with spaces', 'C:\Dao & records\with spaces')) {
        $daoArguments = @(New-DaoOperatorArguments -Action $daoAction -OriginalContainerId ('a' * 64) `
            -AdminRole 'postgres' -ProjectRoot 'C:\Dao project with spaces' -RecordDirectory $daoDirectory `
            -InspectionRecord 'C:\Dao operator records\operation-a\inspect.result.json' -InspectionToken ('b' * 64))
        # Only Python's standard-library JSON encoder is executed. Neither the
        # operator module nor any Docker/database program is run in this test.
        $daoRoundTripText = & $daoPython -B -E -s -c 'import sys,json; print(json.dumps(sys.argv[1:]))' @daoArguments
        if ($LASTEXITCODE -ne 0) { throw 'Native argument round trip failed.' }
        $daoRoundTrip = ConvertFrom-Json -InputObject $daoRoundTripText
        if ($daoRoundTrip.Count -ne $daoArguments.Count) { throw 'Native argument count changed.' }
        for ($daoIndex = 0; $daoIndex -lt $daoArguments.Count; $daoIndex++) {
            if ($daoRoundTrip[$daoIndex] -cne $daoArguments[$daoIndex]) { throw 'Native argument value changed.' }
        }
        if (($daoArguments -ccontains '--authorize-retirement') -ne ($daoAction.ToLowerInvariant() -ceq 'retire')) {
            throw 'Retirement argument isolation failed.'
        }
        $daoCount++
    }
}
if ($daoSource.Contains('SetEnvironmentVariable') -or $daoSource.Contains('SecureStringToBSTR') -or
    $daoSource.Contains('Invoke-Expression') -or $daoSource.Contains('docker ')) {
    throw 'Unexpected launcher credential/process surface.'
}
if ($daoSource.IndexOf('if (-not $AllowReadOnlyConnection)') -gt $daoSource.IndexOf('Read-Host')) {
    throw 'Authorization must precede prompts.'
}
if ($daoSource.IndexOf('$daoOperatorExit = $LASTEXITCODE') -lt $daoSource.IndexOf('& $daoPython @daoArguments')) {
    throw 'Native status propagation ordering rejected.'
}
'Windows PowerShell native argument fixtures passed: {0}' -f $daoCount
'Launcher syntax and authorization/credential checks passed'
'Docker invocations: 0; PostgreSQL connections: 0; SQL execution: 0'
