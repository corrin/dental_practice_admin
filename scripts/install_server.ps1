# Sets up the server to run the admin app, first install and every upgrade; deploy\RELEASE.md
# says when and how to run it. Safe to re-run, including after a failure. Stops at the first error.
$ErrorActionPreference = 'Stop'
function Run { & $args[0] @($args | Select-Object -Skip 1); if ($LASTEXITCODE) { throw "failed (exit $LASTEXITCODE): $args" } }
Set-Location (Split-Path $PSScriptRoot -Parent)
$account = 'practice-app'
if (-not (Test-Path .env)) { throw '.env is missing: create it from the template in deploy\RELEASE.md, then run this again' }
$password = Read-Host "$account password (from 1Password)"

# Tools, each at its latest version. Pinning one fails whenever a newer one is already installed:
# installers refuse to downgrade. 1641 and 3010: installed, restart needed.
function Package($name) { choco upgrade $name -y --no-progress; if ($LASTEXITCODE -notin 0, 1641, 3010) { throw "choco $name failed (exit $LASTEXITCODE)" } }
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) { Invoke-RestMethod https://community.chocolatey.org/install.ps1 | Invoke-Expression; $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') }
Package git
Package uv
Package nodejs-lts
# A running caddy.exe can't be replaced, so Caddy stops only when there is a newer one, and comes
# back even if the upgrade fails.
$caddyRunning = (Get-Service caddy -ErrorAction SilentlyContinue).Status -eq 'Running'
if ($caddyRunning -and (choco outdated -r | Select-String '^caddy\|')) { Stop-Service caddy; try { Package caddy } finally { Start-Service caddy } }
elseif (-not $caddyRunning) { Package caddy }
Invoke-WebRequest https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe -OutFile $env:TEMP\winsw.exe
[Environment]::SetEnvironmentVariable('UV_PYTHON_PREFERENCE', 'only-managed', 'Machine')
[Environment]::SetEnvironmentVariable('UV_PYTHON_INSTALL_DIR', 'C:\ProgramData\uv\python', 'Machine')
[Environment]::SetEnvironmentVariable('UV_LINK_MODE', 'copy', 'Machine')
foreach ($name in 'Path', 'UV_PYTHON_PREFERENCE', 'UV_PYTHON_INSTALL_DIR', 'UV_LINK_MODE') { Set-Item "env:$name" ([Environment]::GetEnvironmentVariable($name, 'Machine')) }

# Account rights and data folders
$sid = (Get-LocalUser $account).SID.Value
Run secedit /export /cfg $env:TEMP\rights.inf /areas USER_RIGHTS
(Get-Content $env:TEMP\rights.inf) | ForEach-Object { if ($_ -match '^Se(Service|Batch)LogonRight =' -and $_ -notmatch $sid) { "$_,*$sid" } else { $_ } } | Set-Content $env:TEMP\rights.inf -Encoding Unicode
Run secedit /configure /db $env:TEMP\rights.sdb /cfg $env:TEMP\rights.inf /areas USER_RIGHTS
Remove-Item $env:TEMP\rights.*
New-Item -ItemType Directory -Force C:\ProgramData\DentalPracticeAdmin, C:\ProgramData\Caddy\logs, C:\ProgramData\uv | Out-Null
Run icacls C:\ProgramData\DentalPracticeAdmin /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' "$($account):(OI)(CI)M"
Run icacls C:\ProgramData\uv /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' "$($account):(OI)(CI)RX"
Run icacls .env /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' "$($account):R"

# The app. uv sync and npm ci replace files the running service holds open, so it stops first.
# Stopping ends any run mid-write: pause the launcher, and refuse while a run is in progress.
$launcher = Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner' -ErrorAction SilentlyContinue
if ($launcher) { $launcher | Disable-ScheduledTask | Out-Null; while ((Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner').State -eq 'Running') { Start-Sleep 5 } }
try {
    $running = 0
    if (Get-Service dental-practice-admin -ErrorAction SilentlyContinue) {
        $running = Run .venv\Scripts\python.exe -c "from dental_practice_admin.config import Settings; from dental_practice_admin.storage import Storage; print(Storage(Settings().database_path).db.execute('SELECT count(*) FROM task_runs WHERE outcome = ?', ('running',)).fetchone()[0])"
    }
    if ([int]$running) { throw "$running run(s) in progress (Results page); nothing changed; run this again when they finish" }
} catch {
    if ($launcher) { $launcher | Enable-ScheduledTask | Out-Null }
    throw
}
# A failure from here leaves the launcher paused, so no task starts against a half-installed release.
try {
    if (Get-Service dental-practice-admin -ErrorAction SilentlyContinue) { Stop-Service dental-practice-admin }
    # Everything on this server is backed up and paid for by the byte: no development tools, and
    # no second copy of each package left in the download caches.
    Run uv sync --locked --no-dev
    Run npm.cmd ci --omit=dev
    Run uv cache clean
    Run npm.cmd cache clean --force
    Run powershell -NoProfile -File scripts\check_settings.ps1 -ReleaseRoot (Get-Location).Path
    # WinSW reads the definition at each start; its start and failure settings only at install.
    Copy-Item deploy\dental-practice-admin.xml .
    if (-not (Get-Service dental-practice-admin -ErrorAction SilentlyContinue)) {
        Copy-Item $env:TEMP\winsw.exe dental-practice-admin.exe
        Run .\dental-practice-admin.exe install
    }
    Run sc.exe config dental-practice-admin obj= ".\$account" password= $password
    Start-Service dental-practice-admin
    $health = $null; $deadline = (Get-Date).AddMinutes(2)
    while (-not $health -and (Get-Date) -lt $deadline) { Start-Sleep 5; try { $health = Invoke-RestMethod http://127.0.0.1:8080/health } catch { } }
    if (-not $health -or $health.status -ne 'ok' -or $health.principle -ne 'production') { throw "unhealthy: $($health | ConvertTo-Json -Compress); see C:\ProgramData\DentalPracticeAdmin\logs" }
} catch {
    Write-Host 'The scheduled launcher stays paused until this script succeeds.' -ForegroundColor Red
    throw
}

# Caddy (installed here, first started at the switch), firewall, launcher
$caddyfileChanged = -not (Test-Path C:\ProgramData\Caddy\Caddyfile) -or (Get-FileHash deploy\Caddyfile).Hash -ne (Get-FileHash C:\ProgramData\Caddy\Caddyfile).Hash
Copy-Item deploy\Caddyfile C:\ProgramData\Caddy\Caddyfile
Run caddy validate --config C:\ProgramData\Caddy\Caddyfile --adapter caddyfile
New-Item -ItemType Directory -Force 'C:\Program Files\Caddy' | Out-Null
Copy-Item deploy\caddy-service.xml 'C:\Program Files\Caddy'
if (-not (Get-Service caddy -ErrorAction SilentlyContinue)) {
    Copy-Item $env:TEMP\winsw.exe 'C:\Program Files\Caddy\caddy-service.exe'
    Run 'C:\Program Files\Caddy\caddy-service.exe' install
    Run sc.exe config caddy obj= 'NT SERVICE\caddy'
}
Run icacls C:\ProgramData\Caddy /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'NT SERVICE\caddy:(OI)(CI)M'
if ($caddyfileChanged -and (Get-Service caddy).Status -eq 'Running') { Restart-Service caddy }
if (-not (Get-NetFirewallRule -DisplayName 'Caddy HTTP and HTTPS' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'Caddy HTTP and HTTPS' -Direction Inbound -Protocol TCP -LocalPort 80, 443 -Action Allow -Profile Any | Out-Null }
if (-not (Get-NetFirewallRule -DisplayName 'SMS bridge from reception' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'SMS bridge from reception' -Direction Inbound -Protocol TCP -LocalPort 5170 -RemoteAddress 192.168.192.125 -Action Allow -Profile Any | Out-Null }
Run schtasks /Create /F /XML deploy\task-runner.xml /TN '\Massey Smiles Admin\Task runner' /RU $account /RP $password
Remove-Item $env:TEMP\winsw.exe
Remove-Item -Recurse -Force $env:TEMP\chocolatey -ErrorAction SilentlyContinue
Write-Host 'Done.' -ForegroundColor Green
