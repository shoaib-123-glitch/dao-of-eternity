# Review before running. Run as a script; do not dot-source it.
# Exit codes: 0 passed, 1 rejected, 2 setup/cleanup failure, 130 cancelled.
param(
    [switch]$DockerForwarding,
    [switch]$PrivilegeDiagnostics
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$daoExitCode = 2
$daoFinished = $false
$daoSavedEnvironment = @{}
$daoSecurePassword = $null
$daoPasswordPointer = [IntPtr]::Zero
$daoLocationPushed = $false

try {
    # This file belongs in dao_of_eternity_bot. No environment files are loaded.
    Push-Location -LiteralPath $PSScriptRoot
    $daoLocationPushed = $true
    if (-not (Test-Path -LiteralPath '.\integration_support.py' -PathType Leaf)) {
        throw 'Project directory is not valid.'
    }

    $daoVenvRoot = $env:VIRTUAL_ENV
    if ([string]::IsNullOrWhiteSpace($daoVenvRoot)) {
        $daoVenvRoot = Read-Host 'Existing virtual environment directory (containing pyvenv.cfg; no quotes)'
    }
    if ([string]::IsNullOrWhiteSpace($daoVenvRoot)) {
        throw 'An existing virtual environment is required.'
    }
    $daoVenvRoot = [IO.Path]::GetFullPath($daoVenvRoot)
    $daoPython = Join-Path $daoVenvRoot 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $daoPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $daoVenvRoot 'pyvenv.cfg') -PathType Leaf)) {
        throw 'The supplied virtual environment is not valid.'
    }

    $daoLaunchMode = 'direct'
    if ($DockerForwarding) {
        # Independently reviewed dao-postgres18-test host mapping. Never infer
        # the internal address from the client host or a rejected SQL session.
        $daoLaunchMode = 'docker-forwarding'
        $daoPort = 5433
    } else {
        $daoPortText = Read-Host 'PostgreSQL 18 local test-server TCP port'
        if ($daoPortText -notmatch '\A[0-9]{1,5}\z') {
            throw 'An explicit decimal TCP port is required.'
        }
        $daoPort = [int]$daoPortText
        if ($daoPort -lt 1 -or $daoPort -gt 65535) {
            throw 'The port is outside the permitted range.'
        }
    }

    foreach ($daoName in @('DAO_TEST_HOST', 'DAO_TEST_PORT', 'DAO_TEST_DATABASE',
                           'DAO_TEST_USER', 'DAO_TEST_PASSWORD')) {
        $daoSavedEnvironment[$daoName] = [Environment]::GetEnvironmentVariable($daoName, 'Process')
    }
    [Environment]::SetEnvironmentVariable('DAO_TEST_HOST', '127.0.0.1', 'Process')
    [Environment]::SetEnvironmentVariable('DAO_TEST_PORT', [string]$daoPort, 'Process')
    [Environment]::SetEnvironmentVariable('DAO_TEST_DATABASE', 'dao_eternity_test', 'Process')
    [Environment]::SetEnvironmentVariable('DAO_TEST_USER', 'dao_test_user', 'Process')

    $daoPasswordPrompt = 'DAO_TEST_PASSWORD'
    if ($DockerForwarding) {
        $daoPasswordPrompt = 'Password for dao_test_user in Docker container dao-postgres18-test'
    }
    $daoSecurePassword = Read-Host $daoPasswordPrompt -AsSecureString
    if ($daoSecurePassword.Length -eq 0) {
        throw 'A nonempty password is required.'
    }
    $daoPasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($daoSecurePassword)
    [Environment]::SetEnvironmentVariable('DAO_TEST_PASSWORD',
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($daoPasswordPointer), 'Process')

    $daoPythonSource = @'
import asyncio
import json
import sys


def main():
    if sys.prefix == sys.base_prefix:
        print("PREFLIGHT SETUP FAILED [SETUP]: an existing virtual environment is required.")
        return 2

    import integration_support as support

    try:
        args = sys.argv[1:]
        diagnostics = args[-1:] == ['--privilege-diagnostics']
        if diagnostics:
            args = args[:-1]
        config = support.TestDatabaseConfig.from_environment()
        expected_server = None
        if args == ['direct']:
            pass
        elif args == ['docker-forwarding']:
            # The cluster identifier is an exact integer, never a floating-point
            # shell value. These nonsecret pins were independently verified.
            if (config.host, config.port, config.database, config.user) != (
                    '127.0.0.1', 5433, 'dao_eternity_test', 'dao_test_user'):
                raise support.IntegrationSafetyError(
                    'The reviewed Docker client destination is required.',
                    category=support.FailureCategory.CONFIGURATION)
            expected_server = support.ExpectedServerIdentity(
                address='172.17.0.2', port=5432,
                system_identifier=7699864005918920747)
        else:
            raise support.IntegrationSafetyError(
                'An explicit valid launcher mode and complete server pin are required.',
                category=support.FailureCategory.CONFIGURATION)
    except support.IntegrationSafetyError:
        print("PREFLIGHT SETUP FAILED [CONFIGURATION]: explicit test configuration was rejected.")
        return 2

    try:
        kwargs = {'allow_connection': True}
        if expected_server is not None:
            kwargs['expected_server'] = expected_server
        report = asyncio.run(support.preflight_catalog(config, **kwargs))
    except support.IntegrationSafetyError as exc:
        # Never print str/repr(exc), SQLSTATE, driver fields, SQL or catalog rows.
        category = support.failure_category(exc).value
        print(f"PREFLIGHT REJECTED [{category}]: connection or safety verification failed.")
        if diagnostics:
            diagnostic = support.privilege_diagnostic(exc)
            if diagnostic is not None:
                print(json.dumps({'privilege_diagnostic': diagnostic}, sort_keys=True))
        if getattr(exc, "termination_unconfirmed", False) is True:
            print("CONNECTION_CLEANUP: test-session closure is unconfirmed; review before retrying.")
        return 1

    # The existing preflight has already closed its connection before returning.
    if type(report.server_major) is not int or report.server_major != 18:
        print("PREFLIGHT REJECTED [VERSION]: PostgreSQL 18 is required.")
        return 1

    if expected_server is not None and (
            report.host != '127.0.0.1' or type(report.port) is not int or report.port != 5433
            or type(report.server_port) is not int or report.server_port != 5432
            or report.cluster_identity_verified is not True):
        print("PREFLIGHT REJECTED [IDENTITY]: the reviewed Docker identity report is required.")
        return 1

    print(json.dumps({
        "status": "PASSED",
        "server_major": report.server_major,
        "database": report.database,
        "role": report.role,
        "host": report.host,
        "port": report.port,
        "server_port": report.server_port,
        "cluster_identity_verified": report.cluster_identity_verified,
        "read_only": report.read_only,
        "effective_privileges_verified": report.effective_privileges_verified,
        "can_create_schema": report.can_create_schema,
    }, sort_keys=True, indent=2))
    return 0


def entrypoint():
    try:
        return main()
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        print("PREFLIGHT CANCELLED [CANCELLED]; confirm test-session closure before retrying.")
        if getattr(exc, "termination_unconfirmed", False) is True:
            print("CONNECTION_CLEANUP: test-session closure is unconfirmed; review before retrying.")
        return 130
    except Exception:
        print("PREFLIGHT SETUP FAILED [SETUP]: unexpected error details suppressed.")
        return 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
'@

    # No activation script, system-Python fallback or bytecode files.
    # Only nonsecret mode/diagnostic options. The password is never an argument.
    $daoPythonArguments = @('-B', '-E', '-s', '-', $daoLaunchMode)
    if ($PrivilegeDiagnostics) { $daoPythonArguments += '--privilege-diagnostics' }
    $daoPythonSource | & $daoPython @daoPythonArguments
    $daoNativeStatus = $LASTEXITCODE
    if ($null -eq $daoNativeStatus) {
        throw 'Python did not supply an exit status.'
    }
    if ($daoNativeStatus -in @(-1073741510, 3221225786)) {
        $daoExitCode = 130  # Windows STATUS_CONTROL_C_EXIT.
    } elseif ($daoNativeStatus -in @(0, 1, 2, 130)) {
        $daoExitCode = [int]$daoNativeStatus
    } else {
        $daoExitCode = 2
        Write-Host 'PREFLIGHT SETUP FAILED [SETUP]: Python returned an unexpected exit status.'
    }
    $daoFinished = $true
}
catch [System.Management.Automation.PipelineStoppedException] {
    $daoExitCode = 130
    $daoFinished = $true
}
catch [System.OperationCanceledException] {
    $daoExitCode = 130
    $daoFinished = $true
}
catch {
    $daoExitCode = 2
    $daoFinished = $true
    Write-Host 'PREFLIGHT SETUP FAILED [SETUP]: local setup or process launch failed; details suppressed.'
}
finally {
    # Some hosts bypass catch on Ctrl+C, but still execute finally.
    if (-not $daoFinished) { $daoExitCode = 130 }
    $daoCleanupFailed = $false
    foreach ($daoName in $daoSavedEnvironment.Keys) {
        try {
            [Environment]::SetEnvironmentVariable($daoName, $daoSavedEnvironment[$daoName], 'Process')
        } catch { $daoCleanupFailed = $true }
    }
    $daoSavedEnvironment.Clear()
    if ($daoPasswordPointer -ne [IntPtr]::Zero) {
        try {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($daoPasswordPointer)
        } catch { $daoCleanupFailed = $true }
    }
    if ($null -ne $daoSecurePassword) {
        try { $daoSecurePassword.Dispose() } catch { $daoCleanupFailed = $true }
    }
    if ($daoLocationPushed) {
        try { Pop-Location } catch { $daoCleanupFailed = $true }
    }
    if ($daoCleanupFailed) {
        $daoExitCode = 2
        Write-Host 'PREFLIGHT SETUP FAILED [SETUP]: local cleanup was incomplete.'
    }
    exit $daoExitCode
}
