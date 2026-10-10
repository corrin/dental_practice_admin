<#
.SYNOPSIS
  Refuse, naming the setting, if a release would not start on this host.

.DESCRIPTION
  Runs the release's own settings check with what the service runs with: the release
  directory's .env, plus the settings the service definition pins. Run it in the new release
  before stopping the running service; verify.ps1 runs it again after the switch. The pinned
  variables are set only for the check and restored afterwards, so a caller's later checks see
  the host as it is.
#>
[CmdletBinding()]
param([string]$ReleaseRoot = 'C:\Program Files\DentalPracticeAdmin')
$ErrorActionPreference = 'Stop'

$python = Join-Path $ReleaseRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    [Console]::Error.WriteLine("Not ready: no interpreter at $python; run uv sync --locked in the release first.")
    exit 1
}
[xml]$service = Get-Content -LiteralPath (Join-Path $ReleaseRoot 'deploy\dental-practice-admin.xml')
$previous = @{}
foreach ($pinned in $service.service.env) {
    $previous[$pinned.name] = [Environment]::GetEnvironmentVariable($pinned.name, 'Process')
    [Environment]::SetEnvironmentVariable($pinned.name, $pinned.value, 'Process')
}
# The check reports on stderr; under Stop, Windows PowerShell would abort on its first line.
$ErrorActionPreference = 'Continue'
$code = 1
Push-Location $ReleaseRoot
try {
    & $python -m dental_practice_admin.config
    # A run that never set an exit code is a failure, not a pass.
    if ($null -ne $LASTEXITCODE) { $code = $LASTEXITCODE }
} finally {
    Pop-Location
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
exit $code
