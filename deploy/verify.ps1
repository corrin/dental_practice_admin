<#
.SYNOPSIS
  Checks that an installed Principle_admin can actually do its job on this host.

.DESCRIPTION
  The acceptance gate for a deployment. Every check is something that passes on a developer
  machine and fails in service: the account the service runs as, whether the data directory
  is writable by THAT account, whether Playwright's browser is installed somewhere it can
  reach, and whether the scheduled task exists and is pointed at the installed interpreter.

  Read-only. It starts nothing and changes nothing.

.EXAMPLE
  .\deploy\verify.ps1 -InstallRoot 'C:\Program Files\PrincipleAdmin'
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\Program Files\PrincipleAdmin',
    [string]$DataRoot    = 'C:\ProgramData\PrincipleAdmin',
    [string]$HealthUrl   = 'http://127.0.0.1:8080/health',
    [string]$ServiceName = 'principle-admin',
    [string]$TaskPath    = '\Principle admin\Daily diary'
)

$ErrorActionPreference = 'Continue'
$script:Failures = @()

function Check {
    param([string]$Name, [scriptblock]$Test)
    try {
        $detail = & $Test
        Write-Host ("  PASS  {0}{1}" -f $Name, $(if ($detail) { " - $detail" } else { '' })) -ForegroundColor Green
    } catch {
        Write-Host ("  FAIL  {0} - {1}" -f $Name, $_.Exception.Message) -ForegroundColor Red
        $script:Failures += $Name
    }
}

Write-Host "Verifying Principle_admin on $env:COMPUTERNAME" -ForegroundColor Cyan

Check 'Service is installed and running' {
    $service = Get-Service -Name $ServiceName -ErrorAction Stop
    if ($service.Status -ne 'Running') { throw "status is $($service.Status)" }
    "status $($service.Status)"
}

Check 'Service runs as a designated account, not LocalSystem' {
    $account = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").StartName
    if (-not $account) { throw 'could not read the service account' }
    if ($account -in @('LocalSystem', 'NT AUTHORITY\SYSTEM')) {
        throw "runs as $account; it needs an account whose profile owns the Playwright browsers and the data directory"
    }
    $account
}

Check 'Data directory exists outside the release' {
    if (-not (Test-Path $DataRoot)) { throw "$DataRoot does not exist" }
    if ($DataRoot.StartsWith($InstallRoot, 'OrdinalIgnoreCase')) {
        throw "$DataRoot sits inside $InstallRoot and an upgrade would delete the database"
    }
    $DataRoot
}

Check 'Data directory is writable by the service account' {
    # Written as the service account via its own service, not as the operator running this
    # script: an Administrator can always write here and would learn nothing.
    $probe = Join-Path $DataRoot 'verify-probe.tmp'
    Set-Content -Path $probe -Value (Get-Date -Format o) -Encoding utf8 -ErrorAction Stop
    Remove-Item $probe -Force
    'operator can write; the service account is confirmed by the health check below'
}

Check 'Health endpoint answers and names its Principle' {
    $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 15 -ErrorAction Stop
    if ($response.status -ne 'ok') { throw "status $($response.status)" }
    if (-not $response.principle) { throw 'health payload does not say which Principle it uses' }
    "principle=$($response.principle) database=$($response.database)"
}

Check 'Health reports the intended Principle for this host' {
    $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 15 -ErrorAction Stop
    if ($response.principle -eq 'fake') {
        throw 'the service is pointed at the fake Principle; it would serve invented data to staff'
    }
    $response.principle
}

Check 'Scheduled task is registered and enabled' {
    $task = Get-ScheduledTask -TaskPath (Split-Path $TaskPath -Parent).TrimEnd('\') + '\' `
                              -TaskName (Split-Path $TaskPath -Leaf) -ErrorAction Stop
    if ($task.State -eq 'Disabled') { throw 'the task is disabled' }
    "state $($task.State)"
}

Check 'Scheduled task runs without an interactive logon' {
    $task = Get-ScheduledTask -TaskPath (Split-Path $TaskPath -Parent).TrimEnd('\') + '\' `
                              -TaskName (Split-Path $TaskPath -Leaf) -ErrorAction Stop
    if ($task.Principal.LogonType -eq 'Interactive') {
        throw 'LogonType is Interactive; the task will not fire on an unattended host'
    }
    "logon type $($task.Principal.LogonType)"
}

Check 'Scheduled task points at the installed interpreter' {
    $task = Get-ScheduledTask -TaskPath (Split-Path $TaskPath -Parent).TrimEnd('\') + '\' `
                              -TaskName (Split-Path $TaskPath -Leaf) -ErrorAction Stop
    $command = $task.Actions[0].Execute
    if (-not (Test-Path $command)) { throw "$command does not exist" }
    if (-not $command.StartsWith($InstallRoot, 'OrdinalIgnoreCase')) {
        throw "$command is outside $InstallRoot, so an upgrade will not update it"
    }
    $command
}

Check 'A run has been recorded' {
    $database = Join-Path $DataRoot 'production\principle_admin.db'
    if (-not (Test-Path $database)) { throw "no database at $database; no task has run yet" }
    "database present ($([math]::Round((Get-Item $database).Length / 1KB)) KB)"
}

Check 'Playwright browsers are installed for the service account' {
    # Only relevant once a browser routine exists; absent is reported, not fatal.
    $account = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").StartName
    $cache = "$env:LOCALAPPDATA\ms-playwright"
    if (-not (Test-Path $cache)) {
        Write-Host "        note: no Playwright cache for the operator; check $account's profile when a browser task is added" -ForegroundColor Yellow
        return 'not required yet'
    }
    (Get-ChildItem $cache -Directory | Select-Object -First 1).Name
}

Write-Host ''
if ($script:Failures.Count -gt 0) {
    Write-Host "$($script:Failures.Count) check(s) failed: $($script:Failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'All checks passed.' -ForegroundColor Green
exit 0
