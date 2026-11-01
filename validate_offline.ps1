# One command for every offline validation. This script has no live operator path.
param([string]$PythonPath = 'python')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & $PythonPath -B -E -s .\validate_offline.py
    if ($LASTEXITCODE -ne 0) { throw 'Guarded offline Python validation failed.' }
    & .\test_schema_provisioning.ps1
    & .\test_retirement_operator.ps1
    foreach ($daoFile in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File) {
        $daoTokens = $null
        $daoErrors = $null
        [void][Management.Automation.Language.Parser]::ParseFile($daoFile.FullName, [ref]$daoTokens, [ref]$daoErrors)
        if ($daoErrors.Count -ne 0) { throw ('PowerShell parse failed: ' + $daoFile.Name) }
    }
    foreach ($daoDocName in @('POSTGRES_FRESH_WORKFLOW.md', 'POSTGRES_SECOND_ATTEMPT_INVESTIGATION.md',
                            'POSTGRES_INVENTORY_DIAGNOSTICS_REVIEW.md', 'RELEASE_V023_SHATTERED_HEAVENS.md',
                            'PRIVATE_GAMEPLAY_V023_RC1.md', 'PRIVATE_PLAYTEST_LAUNCH_INVESTIGATION.md',
                            'RELEASE_V024_LIVING_DAO.md', 'RELEASE_V024_STABLE.md', 'ROADMAP_V025.md')) {
        $daoDoc = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $daoDocName))
        $daoBlocks = [regex]::Matches($daoDoc, '(?ms)^```powershell\r?\n(.*?)^```')
        foreach ($daoBlock in $daoBlocks) {
            $daoTokens = $null
            $daoErrors = $null
            [void][Management.Automation.Language.Parser]::ParseInput($daoBlock.Groups[1].Value, [ref]$daoTokens, [ref]$daoErrors)
            if ($daoErrors.Count -ne 0) { throw ('Procedure PowerShell parse failed: ' + $daoDocName) }
        }
    }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Tracked whitespace validation failed.' }
    # git diff --check omits untracked files; check all new text sources too.
    foreach ($daoName in @('fresh_integration.py', 'test_fresh_integration.py', 'validate_offline.py',
                           'validate_offline.ps1', 'POSTGRES_FRESH_WORKFLOW.md',
                           'fresh_recovery_inspector.py', 'test_fresh_recovery_inspector.py',
                           'test_inventory_query_investigation.py', 'POSTGRES_SECOND_ATTEMPT_INVESTIGATION.md',
                           'inventory_fetch_diagnostics.py', 'test_inventory_fetch_diagnostics.py',
                           'POSTGRES_INVENTORY_DIAGNOSTICS_REVIEW.md', 'shattered_heavens_v023.py',
                           'sect_activities_v023.py', 'qi_interface_v023.py', 'test_shattered_heavens_v023.py',
                           'test_sect_companion_v023.py', 'test_qi_interface_v023.py', 'RELEASE_V023_SHATTERED_HEAVENS.md',
                           'private_playtest.py', 'test_private_playtest.py', 'launch_private_playtest.ps1',
                           'PRIVATE_GAMEPLAY_V023_RC1.md', 'test_private_playtest_diagnostics.py',
                           'PRIVATE_PLAYTEST_LAUNCH_INVESTIGATION.md', 'combat_journey_v024.py',
                           'companion_bonds_v024.py', 'living_world_v024.py', 'sect_projects_v024.py',
                           'onboarding_v024.py', 'test_living_dao_v024.py', 'test_living_dao_ui_v024.py',
                           'RELEASE_V024_LIVING_DAO.md', 'test_living_dao_stable.py',
                           'RELEASE_V024_STABLE.md', 'ROADMAP_V025.md')) {
        $daoText = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $daoName))
        if ($daoText -match '(?m)[\t ]+\r?$' -or -not $daoText.EndsWith("`n")) {
            throw ('New file whitespace validation failed: ' + $daoName)
        }
    }
    'All offline validations passed. No Docker command, SQL or database-backed suite was executed.'
} finally {
    Pop-Location
}
