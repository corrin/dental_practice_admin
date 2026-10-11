# Installs or upgrades the admin app on the server from a prebuilt release; deploy\RELEASE.md says
# when and how to run it. Safe to re-run, including after a failure. Stops at the first error.
param(
    # A release tag to install instead of the latest, for example to roll back.
    [string]$Release
)
$ErrorActionPreference = 'Stop'
# The exit code decides: Windows PowerShell 5.1 turns a tool's stderr notice into an error when
# output is redirected.
function Run { $ErrorActionPreference = 'Continue'; & $args[0] @($args | Select-Object -Skip 1); if ($LASTEXITCODE) { throw "failed (exit $LASTEXITCODE): $args" } }
$repo = 'corrin/dental_practice_admin'
$root = 'C:\Program Files\DentalPracticeAdmin'
$account = 'practice-app'
if (-not (Test-Path "$root\.env")) { throw ".env is missing: create $root\.env from the template in deploy\RELEASE.md, then run this again" }
$password = Read-Host "$account password (from 1Password)"

# Tools the app runs at runtime, each at its latest version: Git (chat saves task drafts), Node.js
# (the browser server) and Caddy. 1641 and 3010: installed, restart needed.
function Package($name) { choco upgrade $name -y --no-progress; if ($LASTEXITCODE -notin 0, 1641, 3010) { throw "choco $name failed (exit $LASTEXITCODE)" } }
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) { Invoke-RestMethod https://community.chocolatey.org/install.ps1 | Invoke-Expression }
Package git
Package nodejs-lts
# A running caddy.exe can't be replaced, so Caddy stops only when there is a newer one, and comes
# back even if the upgrade fails.
$caddyRunning = (Get-Service caddy -ErrorAction SilentlyContinue).Status -eq 'Running'
if ($caddyRunning -and (choco outdated -r | Select-String '^caddy\|')) { Stop-Service caddy; try { Package caddy } finally { Start-Service caddy } }
elseif (-not $caddyRunning) { Package caddy }
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine')
Invoke-WebRequest https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe -OutFile $env:TEMP\winsw.exe

# Account rights and data folders
$sid = (Get-LocalUser $account).SID.Value
Run secedit /export /cfg $env:TEMP\rights.inf /areas USER_RIGHTS
(Get-Content $env:TEMP\rights.inf) | ForEach-Object { if ($_ -match '^Se(Service|Batch)LogonRight =' -and $_ -notmatch $sid) { "$_,*$sid" } else { $_ } } | Set-Content $env:TEMP\rights.inf -Encoding Unicode
Run secedit /configure /db $env:TEMP\rights.sdb /cfg $env:TEMP\rights.inf /areas USER_RIGHTS
Remove-Item $env:TEMP\rights.*
New-Item -ItemType Directory -Force C:\ProgramData\DentalPracticeAdmin, C:\ProgramData\Caddy\logs | Out-Null
Run icacls C:\ProgramData\DentalPracticeAdmin /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' "$($account):(OI)(CI)M"

# The release. Only one is kept on disk: rolling back installs an older tag the same way.
$tag = if ($Release) { $Release } else { (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest").tag_name }
$installed = if (Test-Path "$root\RELEASE") { (Get-Content "$root\RELEASE" -Raw).Trim() } else { '' }
$launcher = Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner' -ErrorAction SilentlyContinue
if ($tag -ne $installed) {
    Write-Host "Installing release $tag (installed: $(if ($installed) { $installed } else { 'none' }))"
    Invoke-WebRequest "https://github.com/$repo/releases/download/$tag/dental-practice-admin.zip" -OutFile $env:TEMP\dental-practice-admin.zip
    if (Test-Path "$root-new") { Remove-Item -Recurse -Force "$root-new" }
    Expand-Archive $env:TEMP\dental-practice-admin.zip "$root-new"
    Remove-Item $env:TEMP\dental-practice-admin.zip
    Copy-Item "$root\.env" "$root-new\.env"

    # Stopping the app ends any run mid-write: pause the launcher, and refuse while a run is in
    # progress. Until the new release is healthy, the launcher stays paused.
    if ($launcher) { $launcher | Disable-ScheduledTask | Out-Null; while ((Get-ScheduledTask -TaskPath '\Massey Smiles Admin\' -TaskName 'Task runner').State -eq 'Running') { Start-Sleep 5 } }
    if (Test-Path "$root\python\python.exe") {
        Push-Location $root
        try { $running = Run "$root\python\python.exe" -c "from dental_practice_admin.config import Settings; from dental_practice_admin.storage import Storage; print(Storage(Settings().database_path).db.execute('SELECT count(*) FROM task_runs WHERE outcome = ?', ('running',)).fetchone()[0])" }
        finally { Pop-Location }
        if ([int]$running) { if ($launcher) { $launcher | Enable-ScheduledTask | Out-Null }; throw "$running run(s) in progress (Results page); nothing changed; run this again when they finish" }
    }
    if (Get-Service dental-practice-admin -ErrorAction SilentlyContinue) { Stop-Service dental-practice-admin }
    # The browser server and its Node process can outlive the service and hold files open.
    Get-Process | Where-Object { $_.Path -like "$root\*" } | Stop-Process -Force
    if (Test-Path "$root-old") { Remove-Item -Recurse -Force "$root-old" }
    try { Rename-Item $root "$root-old"; Rename-Item "$root-new" $root }
    catch {
        # Put the running release back, so a failed swap never leaves the app down.
        if (-not (Test-Path $root) -and (Test-Path "$root-old")) { Rename-Item "$root-old" $root }
        if (Get-Service dental-practice-admin -ErrorAction SilentlyContinue) { Start-Service dental-practice-admin }
        if ($launcher) { $launcher | Enable-ScheduledTask | Out-Null }
        throw "couldn't swap in release $tag (is a file in $root open?): $_"
    }
}
Run icacls "$root\.env" /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' "$($account):R"
Run powershell -NoProfile -File "$root\scripts\check_settings.ps1" -ReleaseRoot $root

# The app service. WinSW reads its definition at each start; start and failure settings only at
# install.
Copy-Item "$root\deploy\dental-practice-admin.xml" $root
if (-not (Test-Path "$root\dental-practice-admin.exe")) { Copy-Item $env:TEMP\winsw.exe "$root\dental-practice-admin.exe" }
if (-not (Get-Service dental-practice-admin -ErrorAction SilentlyContinue)) { Run "$root\dental-practice-admin.exe" install }
Run sc.exe config dental-practice-admin obj= ".\$account" password= $password
if ((Get-Service dental-practice-admin).Status -ne 'Running') { Start-Service dental-practice-admin }
$health = $null; $deadline = (Get-Date).AddMinutes(2)
while (-not $health -and (Get-Date) -lt $deadline) { Start-Sleep 5; try { $health = Invoke-RestMethod http://127.0.0.1:8080/health } catch { } }
if (-not $health -or $health.status -ne 'ok' -or $health.principle -ne 'production') {
    $swapped = $tag -ne $installed
    $rollback = if ($swapped -and $installed) { " To roll back, run this script with -Release $installed." } else { '' }
    $paused = if ($swapped) { ' The scheduled launcher stays paused.' } else { '' }
    throw "unhealthy: $($health | ConvertTo-Json -Compress); see C:\ProgramData\DentalPracticeAdmin\logs.$paused$rollback"
}
if (Test-Path "$root-old") { Remove-Item -Recurse -Force "$root-old" }

# Caddy (installed here, first started at the switch), firewall, launcher
$caddyfileChanged = -not (Test-Path C:\ProgramData\Caddy\Caddyfile) -or (Get-FileHash "$root\deploy\Caddyfile").Hash -ne (Get-FileHash C:\ProgramData\Caddy\Caddyfile).Hash
Copy-Item "$root\deploy\Caddyfile" C:\ProgramData\Caddy\Caddyfile
Run caddy validate --config C:\ProgramData\Caddy\Caddyfile --adapter caddyfile
New-Item -ItemType Directory -Force 'C:\Program Files\Caddy' | Out-Null
Copy-Item "$root\deploy\caddy-service.xml" 'C:\Program Files\Caddy'
if (-not (Get-Service caddy -ErrorAction SilentlyContinue)) {
    Copy-Item $env:TEMP\winsw.exe 'C:\Program Files\Caddy\caddy-service.exe'
    Run 'C:\Program Files\Caddy\caddy-service.exe' install
    Run sc.exe config caddy obj= 'NT SERVICE\caddy'
}
Run icacls C:\ProgramData\Caddy /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'NT SERVICE\caddy:(OI)(CI)M'
if ($caddyfileChanged -and (Get-Service caddy).Status -eq 'Running') { Restart-Service caddy }
if (-not (Get-NetFirewallRule -DisplayName 'Caddy HTTP and HTTPS' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'Caddy HTTP and HTTPS' -Direction Inbound -Protocol TCP -LocalPort 80, 443 -Action Allow -Profile Any | Out-Null }
if (-not (Get-NetFirewallRule -DisplayName 'SMS bridge from reception' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'SMS bridge from reception' -Direction Inbound -Protocol TCP -LocalPort 5170 -RemoteAddress 192.168.192.125 -Action Allow -Profile Any | Out-Null }
Run schtasks /Create /F /XML "$root\deploy\task-runner.xml" /TN '\Massey Smiles Admin\Task runner' /RU $account /RP $password
Remove-Item $env:TEMP\winsw.exe
Remove-Item -Recurse -Force $env:TEMP\chocolatey -ErrorAction SilentlyContinue
Write-Host "Done: release $tag." -ForegroundColor Green
