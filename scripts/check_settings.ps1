<#
.SYNOPSIS
  Refuse, naming the setting, if a release would not start on this host.

.DESCRIPTION
  Runs the release's own settings check with what the service runs with: the release
  directory's .env, plus the settings the service definition pins, and none of the application
  variables in the caller's shell, which the service would not have. Run it in the new release
  before stopping the running service; verify.ps1 runs it again after the switch. The caller's
  variables are restored afterwards.
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
foreach ($variable in Get-ChildItem env: | Where-Object { $_.Name -match '^(ADMIN|PRINCIPLE|OPENAI|AKAHU)_' }) {
    $previous[$variable.Name] = $variable.Value
    [Environment]::SetEnvironmentVariable($variable.Name, $null, 'Process')
}
foreach ($pinned in $service.service.env) {
    if (-not $previous.ContainsKey($pinned.name)) {
        $previous[$pinned.name] = [Environment]::GetEnvironmentVariable($pinned.name, 'Process')
    }
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
