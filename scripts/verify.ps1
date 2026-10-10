<#
.SYNOPSIS
  Read-only production verification. Run on the practice host after installation or a reboot.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\Program Files\DentalPracticeAdmin',
    [string]$DataRoot = 'C:\ProgramData\DentalPracticeAdmin',
    [string]$HealthUrl = 'http://127.0.0.1:8080/health'
)
$ErrorActionPreference = 'Stop'
$script:Failures = @()
function Check {
    param([string]$Name, [scriptblock]$Test)
    try {
        $detail = & $Test
        Write-Host "PASS: $Name - $detail" -ForegroundColor Green
    } catch {
        Write-Host "FAIL: $Name - $_" -ForegroundColor Red
        $script:Failures += $Name
    }
}

Check 'Configuration is complete' {
    # Startup refuses a missing setting; this names it, instead of only a stopped service.
    $python = Join-Path $InstallRoot '.venv\Scripts\python.exe'
    Push-Location $InstallRoot
    try {
        & $python -c 'from dental_practice_admin.config import load_settings; load_settings().require_web_configured()'
        if ($LASTEXITCODE -ne 0) { throw 'a required setting is missing; the error above names it' }
    } finally { Pop-Location }
    'every setting present'
}
Check 'Application service is running as a designated account' {
    $service = Get-CimInstance Win32_Service -Filter "Name='dental-practice-admin'"
    if (-not $service -or $service.State -ne 'Running') { throw 'service is not running' }
    if (-not $service.StartName -or $service.StartName -in @('LocalSystem', 'NT AUTHORITY\SYSTEM')) {
        throw 'service needs a designated account'
    }
    $service.StartName
}
Check 'Runtime data is outside the release directory' {
    $data = (Resolve-Path -LiteralPath $DataRoot).Path.TrimEnd('\') + '\'
    $install = (Resolve-Path -LiteralPath $InstallRoot).Path.TrimEnd('\') + '\'
    if ($data.StartsWith($install, 'OrdinalIgnoreCase')) { throw 'data sits inside the release' }
    $data
}
Check 'Service opens its database and reports production' {
    $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 15
    if ($health.status -ne 'ok' -or $health.principle -ne 'production') {
        throw 'service does not report healthy production configuration'
    }
    'production readiness confirmed under the running service identity'
}
Check 'Application schedule launcher is configured for unattended execution' {
    $task = Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner'
    if ($task.State -eq 'Disabled') { throw 'task is disabled' }
    if ($task.Principal.LogonType -ne 'Password') { throw 'task needs an unattended Password logon' }
    if ($task.Actions.Count -ne 1) { throw 'expected one launcher action' }
    $action = $task.Actions[0]
    $expected = Join-Path $InstallRoot '.venv\Scripts\python.exe'
    if ($action.Execute -ne $expected -or -not (Test-Path -LiteralPath $expected)) {
        throw 'task does not use the installed interpreter'
    }
    if ($action.WorkingDirectory -ne $InstallRoot -or
        $action.Arguments -ne '-m dental_practice_admin.schedules') {
        throw 'task does not invoke the installed due-task runner'
    }
    $pollSeconds = & $expected -c 'from dental_practice_admin.schedules import POLL_SECONDS; print(POLL_SECONDS)'
    if ($LASTEXITCODE -ne 0) { throw 'cannot read installed polling interval' }
    if ($task.Triggers.Count -ne 1 -or [System.Xml.XmlConvert]::ToTimeSpan($task.Triggers[0].Repetition.Interval).TotalSeconds -ne [int]$pollSeconds) {
        throw 'launcher repetition does not match the application polling interval'
    }
    $info = $task | Get-ScheduledTaskInfo
    if ($info.LastTaskResult -ne 0) { throw "last task result is $($info.LastTaskResult)" }
    if ($info.LastRunTime -lt (Get-Date).AddSeconds(-2 * [int]$pollSeconds)) { throw 'launcher has missed two polling intervals' }
    'registered action and last scheduler outcome verified'
}
Check 'Application has checked its schedules recently' {
    $python = Join-Path $InstallRoot '.venv\Scripts\python.exe'
    $audit = Join-Path $DataRoot 'production\audits\launcher.jsonl'
    & $python "$PSScriptRoot\check_runner.py" $audit
    if ($LASTEXITCODE -ne 0) { throw 'no recent successful scheduler check' }
}
if ($script:Failures.Count) { exit 1 }
Write-Host 'Host checks passed. Reboot and restore evidence is recorded in deploy/ACCEPTANCE.md.'
exit 0
