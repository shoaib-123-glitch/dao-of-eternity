# Dedicated interactive launch. No dotenv, provisioning, Docker or integration run.
param(
    [switch]$AuthorizeV025Candidate,
    [switch]$AuthorizeDatabaseWrites,
    [switch]$AuthorizeDiscordLogin,
    [switch]$DiagnoseOnly,
    [switch]$AuthorizeReadOnlyConnection,
    [switch]$EnableDeveloperConsole,
    [string]$PythonPath = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($MyInvocation.InvocationName -eq '.') { throw 'Run this file as a script; do not dot-source it.' }
if (-not $AuthorizeV025Candidate) {
    Write-Output 'V025_CANDIDATE_NOT_LAUNCHED: require explicit -AuthorizeV025Candidate and a separate v0.25 sandbox database/login.'
    exit 2
}
if ($DiagnoseOnly) {
    if (-not ($AuthorizeReadOnlyConnection -and $AuthorizeDiscordLogin) -or $AuthorizeDatabaseWrites) {
        Write-Output 'Not diagnosed. Require -DiagnoseOnly -AuthorizeReadOnlyConnection -AuthorizeDiscordLogin; omit database-write authorization.'
        exit 2
    }
} elseif (-not ($AuthorizeDatabaseWrites -and $AuthorizeDiscordLogin) -or $AuthorizeReadOnlyConnection) {
    Write-Output 'Not launched. Both -AuthorizeDatabaseWrites and -AuthorizeDiscordLogin are required.'
    exit 2
}

$daoSaved = @{}
$daoSaved['DAO_V025_CANDIDATE'] = [Environment]::GetEnvironmentVariable('DAO_V025_CANDIDATE','Process')
$daoSecure = $null
$daoPointer = [IntPtr]::Zero
$daoExitCode = 2
$daoStage = 'INTERPRETER_RESOLUTION'
Push-Location -LiteralPath $PSScriptRoot
try {
    $daoPythonCommand = Get-Command -Name $PythonPath -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $daoStage = 'PROJECT_CHECK'
    if (-not (Test-Path -LiteralPath '.\private_playtest.py' -PathType Leaf)) {
        throw 'Private gameplay launcher is missing.'
    }
    Write-Output 'Use an independently verified dedicated test cluster and separate private Discord application.'
    Write-Output 'The launcher never creates a database/role or reads an environment file.'
    $daoStage = 'TARGET_INPUT'
    $daoValues = @{
        HOST = '127.0.0.1'
        PORT = Read-Host 'Verified local test-cluster TCP port (not 5432)'
        DATABASE = Read-Host 'Fresh gameplay database (dao_playtest_v023_<name>)'
        USER = Read-Host 'Dedicated login (dao_playtest_<name>)'
        SYSTEM_IDENTIFIER = Read-Host 'Independently recorded test-cluster system_identifier'
        APPLICATION_ID = Read-Host 'Separate private TEST Discord application ID'
        GUILD_ID = Read-Host 'Private TEST Discord guild ID'
    }
    $daoStage = 'CONFIGURATION'
    if ($daoValues.PORT -notmatch '\A[0-9]{1,5}\z' -or
        [int]$daoValues.PORT -lt 1024 -or [int]$daoValues.PORT -gt 65535 -or [int]$daoValues.PORT -eq 5432 -or
        $daoValues.DATABASE -cnotmatch '\Adao_playtest_v023_[a-z0-9_]{1,24}\z' -or
        $daoValues.USER -cnotmatch '\Adao_playtest_[a-z0-9_]{1,32}\z') {
        throw 'Dedicated test host/port/database/login settings are invalid.'
    }
    foreach ($daoKey in @('SYSTEM_IDENTIFIER', 'APPLICATION_ID', 'GUILD_ID')) {
        if ($daoValues[$daoKey] -notmatch '\A[1-9][0-9]{0,19}\z') {
            throw 'An explicit positive decimal cluster/application/guild ID is required.'
        }
    }
    if (-not $daoValues.DATABASE.StartsWith('dao_playtest_v023_v025_') -or -not $daoValues.USER.StartsWith('dao_playtest_v025_')) {
        throw 'V025 candidate requires its separate sandbox database and dedicated login.'
    }
    Write-Output ('Target: {0}:{1} / {2}; login {3}; cluster {4}; app {5}; guild {6}' -f
        $daoValues.HOST, $daoValues.PORT, $daoValues.DATABASE, $daoValues.USER,
        $daoValues.SYSTEM_IDENTIFIER, $daoValues.APPLICATION_ID, $daoValues.GUILD_ID)
    $daoStage = 'TARGET_REVIEW'
    if ($DiagnoseOnly) {
        Write-Output 'Read-only diagnostic: REST login/guild read and catalog identity check; no gateway, command sync, table setup or gameplay.'
        $daoApprovalPhrase = 'DIAGNOSE PRIVATE PLAYTEST'
    } else {
        Write-Output 'This starts Discord login, checks identity, runs existing table setup/backfill and enables gameplay writes.'
        $daoApprovalPhrase = 'LAUNCH PRIVATE PLAYTEST'
    }
    if ((Read-Host ('Type ' + $daoApprovalPhrase + ' to approve this exact target')) -cne $daoApprovalPhrase) {
        throw 'Target was not approved.'
    }
    $daoConsole = @{}
    if ($EnableDeveloperConsole) {
        if ($DiagnoseOnly) { throw 'The developer console cannot be enabled for a read-only diagnostic.' }
        $daoStage = 'DEV_CONSOLE_INPUT'
        Write-Output 'Developer console: disposable sandbox only. The database must carry the sandbox marker comment.'
        if ((Read-Host 'Re-type the sandbox database name') -cne $daoValues.DATABASE -or
            (Read-Host 'Re-type the cluster system_identifier') -cne $daoValues.SYSTEM_IDENTIFIER) {
            throw 'Developer console sandbox identity was not confirmed.'
        }
        $daoConsole['DEVELOPER_IDS'] = Read-Host 'Developer Discord user IDs (comma-separated, 1-3)'
        $daoConsole['TARGET_IDS'] = Read-Host 'Disposable test-account Discord IDs (comma-separated, 0-10; Enter for none)'
        if ($daoConsole.DEVELOPER_IDS -notmatch '\A[1-9][0-9]{0,19}(,[1-9][0-9]{0,19}){0,2}\z' -or
            ($daoConsole.TARGET_IDS -ne '' -and $daoConsole.TARGET_IDS -notmatch '\A[1-9][0-9]{0,19}(,[1-9][0-9]{0,19}){0,9}\z')) {
            throw 'Developer console IDs must be comma-separated decimal Discord IDs.'
        }
        if ((Read-Host 'Type ENABLE SANDBOX DEVELOPER CONSOLE to approve') -cne 'ENABLE SANDBOX DEVELOPER CONSOLE') {
            throw 'Developer console was not approved.'
        }
        $daoConsole[''] = 'sandbox-only-explicitly-authorized'
        $daoConsole['DATABASE'] = $daoValues.DATABASE
        $daoConsole['SYSTEM_IDENTIFIER'] = $daoValues.SYSTEM_IDENTIFIER
        $daoConsole['GUILD_ID'] = $daoValues.GUILD_ID
    }
    $daoStage = 'ENVIRONMENT_SETUP'
    [Environment]::SetEnvironmentVariable('DAO_V025_CANDIDATE','explicitly-authorized','Process')
    foreach ($daoKey in @('', 'DATABASE', 'SYSTEM_IDENTIFIER', 'GUILD_ID', 'DEVELOPER_IDS', 'TARGET_IDS')) {
        $daoName = if ($daoKey) { 'DAO_DEV_CONSOLE_' + $daoKey } else { 'DAO_DEV_CONSOLE' }
        $daoSaved[$daoName] = [Environment]::GetEnvironmentVariable($daoName, 'Process')
        # Without -EnableDeveloperConsole, inherited console settings are removed.
        $daoConsoleValue = if ($daoConsole.ContainsKey($daoKey)) { $daoConsole[$daoKey] } else { $null }
        [Environment]::SetEnvironmentVariable($daoName, $daoConsoleValue, 'Process')
    }
    foreach ($daoKey in @('HOST', 'PORT', 'DATABASE', 'USER', 'SYSTEM_IDENTIFIER',
                         'APPLICATION_ID', 'GUILD_ID', 'TOKEN', 'PASSWORD')) {
        $daoName = 'DAO_PLAYTEST_' + $daoKey
        $daoSaved[$daoName] = [Environment]::GetEnvironmentVariable($daoName, 'Process')
        if ($daoValues.ContainsKey($daoKey)) {
            [Environment]::SetEnvironmentVariable($daoName, $daoValues[$daoKey], 'Process')
        }
    }
    foreach ($daoKey in @('TOKEN', 'PASSWORD')) {
        $daoStage = 'SECRET_PROMPT'
        $daoPrompt = if ($daoKey -eq 'TOKEN') { 'Separate TEST bot token' } else { 'Dedicated gameplay login password' }
        $daoSecure = Read-Host $daoPrompt -AsSecureString
        if ($daoSecure.Length -eq 0) { throw 'Both test credentials must be nonempty.' }
        $daoPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($daoSecure)
        try {
            [Environment]::SetEnvironmentVariable(('DAO_PLAYTEST_' + $daoKey),
                [Runtime.InteropServices.Marshal]::PtrToStringBSTR($daoPointer), 'Process')
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($daoPointer)
            $daoPointer = [IntPtr]::Zero
            $daoSecure.Dispose()
            $daoSecure = $null
        }
    }
    $daoStage = 'PYTHON_PROCESS'
    if ($DiagnoseOnly) {
        & $daoPythonCommand.Source -B -E -s .\private_playtest.py --diagnose-only --authorize-read-only-connection --authorize-discord-login
    } else {
        & $daoPythonCommand.Source -B -E -s .\private_playtest.py --authorize-database-writes --authorize-discord-login
    }
    $daoExitCode = $LASTEXITCODE
    if ($daoExitCode -ne 0) {
        Write-Output ('PRIVATE_PLAYTEST_PROCESS_EXIT {{"stage":"PYTHON_PROCESS","exit_code":{0}}}' -f $daoExitCode)
    }
} catch {
    # Do not stringify an arbitrary process/credential-conversion exception.
    Write-Output ('PRIVATE_PLAYTEST_STOPPED {{"stage":"{0}","family":"POWERSHELL_ERROR"}}' -f $daoStage)
} finally {
    if ($daoPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($daoPointer) }
    if ($null -ne $daoSecure) { $daoSecure.Dispose() }
    foreach ($daoName in $daoSaved.Keys) {
        [Environment]::SetEnvironmentVariable($daoName, $daoSaved[$daoName], 'Process')
    }
    Pop-Location
}
exit $daoExitCode
