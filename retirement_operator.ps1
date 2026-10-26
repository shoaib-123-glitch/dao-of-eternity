# Run as a script in a normal console; never dot-source. No credentials in arguments.
param(
    [ValidateSet('Inspect', 'Retire')][string]$Action = 'Inspect',
    [switch]$AllowReadOnlyConnection,
    [switch]$AuthorizeRetirement,
    [string]$OriginalContainerId,
    [string]$AdminRole,
    [string]$PythonPath,
    [string]$RecordDirectory,
    [string]$InspectionRecord,
    [string]$InspectionToken
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function New-DaoOperatorArguments {
    param([string]$Action, [string]$OriginalContainerId, [string]$AdminRole,
          [string]$ProjectRoot, [string]$RecordDirectory, [string]$InspectionRecord, [string]$InspectionToken)
    $daoResult = @('-B', '-E', '-s', (Join-Path $ProjectRoot 'retirement_operator.py'),
        '--action', $Action.ToLowerInvariant(), '--allow-read-only-connection',
        '--original-container-id', $OriginalContainerId, '--admin-role', $AdminRole,
        '--recovery-directory', (Join-Path $ProjectRoot 'integration_recovery'))
    if (-not [string]::IsNullOrWhiteSpace($RecordDirectory)) {
        $daoResult += @('--record-directory', [IO.Path]::GetFullPath($RecordDirectory))
    }
    if ($Action.ToLowerInvariant() -ceq 'retire') {
        $daoResult += @('--authorize-retirement', '--inspection-record', $InspectionRecord,
                        '--inspection-token', $InspectionToken)
    }
    return $daoResult
}
try {
    $Action = $Action.ToLowerInvariant()
    if (-not $AllowReadOnlyConnection) {
        Write-Output 'RETIREMENT OPERATOR DISABLED [CONNECTION_NOT_AUTHORIZED]'
        exit 2
    }
    if ($Action -ceq 'retire' -and (-not $AuthorizeRetirement -or [string]::IsNullOrWhiteSpace($InspectionRecord) -or
        $InspectionToken -cnotmatch '\A[0-9a-f]{64}\z')) {
        Write-Output 'RETIREMENT OPERATOR DISABLED [RETIREMENT_NOT_AUTHORIZED]'
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($OriginalContainerId)) {
        $OriginalContainerId = Read-Host 'ORIGINAL full container ID independently recorded in both receipts'
    }
    if ([string]::IsNullOrWhiteSpace($AdminRole)) {
        $AdminRole = Read-Host 'Independently known existing Docker PostgreSQL administrator role'
    }
    if ($OriginalContainerId -cnotmatch '\A[0-9a-f]{64}\z' -or
        $AdminRole -cnotmatch '\A[a-z_][a-z0-9_]{0,62}\z' -or $AdminRole -ceq 'dao_test_user') {
        throw 'Input rejected.'
    }
    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        $daoCandidates = @(Get-Command python -CommandType Application -ErrorAction Stop)
        if ($daoCandidates.Count -ne 1) { throw 'Approved interpreter unavailable.' }
        $PythonPath = $daoCandidates[0].Source
    }
    $daoPython = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $daoPython -PathType Leaf)) { throw 'Approved interpreter unavailable.' }
    if ($Action -ceq 'retire') {
        $InspectionRecord = (Resolve-Path -LiteralPath $InspectionRecord -ErrorAction Stop).Path
    }
    $daoArguments = @(New-DaoOperatorArguments -Action $Action -OriginalContainerId $OriginalContainerId `
        -AdminRole $AdminRole -ProjectRoot $PSScriptRoot -RecordDirectory $RecordDirectory `
        -InspectionRecord $InspectionRecord -InspectionToken $InspectionToken)
    # Inherit console handles, keeping Docker/psql's non-echoing -W prompt interactive.
    # No pipeline, stdout capture, password forwarding or process environment changes.
    & $daoPython @daoArguments
    $daoOperatorExit = $LASTEXITCODE
    exit $daoOperatorExit
} catch {
    Write-Output 'RETIREMENT OPERATOR REJECTED [LOCAL_SETUP]; preserve records; no retry.'
    exit 1
}
