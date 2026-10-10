<#
.SYNOPSIS
  Refuse, naming the setting, if a release would not start on this host.

.DESCRIPTION
  Runs the release's own settings check with what the service runs with: the release
  directory's .env, plus the settings the service definition pins. Run it in the new release
  before stopping the running service; verify.ps1 runs it again after the switch.
#>
[CmdletBinding()]
param([string]$ReleaseRoot = 'C:\Program Files\DentalPracticeAdmin')
$ErrorActionPreference = 'Stop'

[xml]$service = Get-Content -LiteralPath (Join-Path $ReleaseRoot 'deploy\dental-practice-admin.xml')
foreach ($pinned in $service.service.env) {
    Set-Item -LiteralPath "env:$($pinned.name)" -Value $pinned.value
}
# The check reports on stderr; under Stop, Windows PowerShell would abort on its first line.
$ErrorActionPreference = 'Continue'
Push-Location $ReleaseRoot
try {
    & (Join-Path $ReleaseRoot '.venv\Scripts\python.exe') -m dental_practice_admin.config
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
