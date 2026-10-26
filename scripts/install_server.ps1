# Installs or upgrades the admin app on the server, in an elevated PowerShell:
#   Invoke-WebRequest https://raw.githubusercontent.com/corrin/dental_practice_admin/main/scripts/install_server.ps1 -OutFile $env:TEMP\install_server.ps1; Set-ExecutionPolicy Bypass -Scope Process -Force; & $env:TEMP\install_server.ps1
# Third-party libraries install only when they changed. Safe to re-run, including after a failure;
# it stops at the first error and says what it needs. -Ref <commit> installs that commit instead
# of main, for example to roll back.
param([string]$Ref = 'origin/main')
$ErrorActionPreference = 'Stop'
# The exit code decides: Windows PowerShell 5.1 turns a tool's stderr notice into an error when
# output is redirected.
function Run { $ErrorActionPreference = 'Continue'; & $args[0] @($args | Select-Object -Skip 1); if ($LASTEXITCODE) { throw "failed (exit $LASTEXITCODE): $args" } }
$root = 'C:\Program Files\DentalPracticeAdmin'
$account = 'practice-app'
# Only a run that creates the account, sets up the service or registers the launcher needs the
# password: Windows keeps it for both. Read-Host gets nothing in a second powershell inside a remote
# session, hence & above.
$password = $null
$service = Get-CimInstance Win32_Service -Filter "Name='dental-practice-admin'"
if (-not (Get-LocalUser $account -ErrorAction SilentlyContinue) -or -not $service -or $service.StartName -ne ".\$account") {
    $password = Read-Host "Password for the $account account (a new one creates it; otherwise its existing password)"
    if (-not $password) { throw 'No password entered.' }
}
# The app's own account, not an administrator; ARCHITECTURE.md says what that does and doesn't protect.
if (-not (Get-LocalUser $account -ErrorAction SilentlyContinue)) {
    New-LocalUser $account -Password (ConvertTo-SecureString $password -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null
}

# Tools, each at its latest version. 1641 and 3010: installed, restart needed.
function Package($name) { choco upgrade $name -y --no-progress; if ($LASTEXITCODE -notin 0, 1641, 3010) { throw "choco $name failed (exit $LASTEXITCODE)" } }
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) { Invoke-RestMethod https://community.chocolatey.org/install.ps1 | Invoke-Expression }
Package git
Package uv
Package nodejs-lts
# A running caddy.exe can't be replaced, so Caddy stops only when there is a newer one, and comes
# back even if the upgrade fails.
$caddyRunning = (Get-Service caddy -ErrorAction SilentlyContinue).Status -eq 'Running'
if ($caddyRunning -and (choco outdated -r | Select-String '^caddy\|')) { Stop-Service caddy; try { Package caddy } finally { Start-Service caddy } }
elseif (-not $caddyRunning) { Package caddy }
Invoke-WebRequest https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe -OutFile $env:TEMP\winsw.exe
# uv's own Python, readable by the app's account, and packages copied rather than linked to this
# administrator's cache.
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

# The code, and its settings
if (-not (Test-Path "$root\.git")) { Run git clone --quiet https://github.com/corrin/dental_practice_admin.git $root }
Set-Location $root
if (-not (Test-Path .env)) {
    Copy-Item deploy\production.env.example .env
    throw "Fill in every '...' in $root\.env (its comments say what each is), then run this again."
}
if (Select-String -Path .env -Pattern '^[A-Z_]+=.*\.\.\.' -Quiet) { throw "$root\.env still has a '...' value: fill it in, then run this again." }
$adminHost = (Get-Content .env | Where-Object { $_ -match '^ADMIN_HOST=' }) -replace '^ADMIN_HOST=' -replace '"'
if (-not $adminHost) { throw "Add ADMIN_HOST=<the public hostname> to $root\.env (deploy\production.env.example says more), then run this again." }
Run icacls .env /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' "$($account):R"

# Stopping the app ends any run mid-write: pause the launcher, and refuse while a run is in progress.
$launcher = Get-ScheduledTask -TaskPath '\Dental Practice Admin\' -TaskName 'Task runner' -ErrorAction SilentlyContinue
if ($launcher) { $launcher | Disable-ScheduledTask | Out-Null; while ((Get-ScheduledTask -TaskPath '\Dental Practice Admin\' -TaskName 'Task runner').State -eq 'Running') { Start-Sleep 5 } }
try {
    $running = 0
    if ((Get-Service dental-practice-admin -ErrorAction SilentlyContinue) -and (Test-Path .venv\Scripts\python.exe)) {
        $running = Run .venv\Scripts\python.exe -c "from dental_practice_admin.config import Settings; from dental_practice_admin.storage import Storage; print(Storage(Settings().database_path).db.execute('SELECT count(*) FROM task_runs WHERE outcome = ?', ('running',)).fetchone()[0])"
    }
    if ([int]$running) { throw "$running run(s) in progress (Results page); nothing changed; run this again when they finish" }
} catch {
    if ($launcher) { $launcher | Enable-ScheduledTask | Out-Null }
    throw
}
# A failure from here leaves the launcher paused, so no task starts against a half-installed app.
try {
    if (Get-Service dental-practice-admin -ErrorAction SilentlyContinue) { Stop-Service dental-practice-admin }
    Run git fetch --quiet origin
    Run git checkout --quiet --detach $Ref
    # Each third-party layer installs only what changed: uv sync only changed packages (all
    # prebuilt); npm ci, which replaces every Node package, only when the lock changed; the browser
    # only when Playwright's version changed. Nothing for development is installed.
    Run uv sync --locked --no-dev
    $lock = (Get-FileHash package-lock.json).Hash
    if (-not (Test-Path node_modules\.installed-lock) -or (Get-Content node_modules\.installed-lock) -ne $lock) {
        Run npm.cmd ci --omit=dev
        Set-Content node_modules\.installed-lock $lock
    }
    # browser.py runs the browser in ms-playwright beside the app.
    $env:PLAYWRIGHT_BROWSERS_PATH = "$root\ms-playwright"
    Run node node_modules/playwright-core/cli.js install --no-shell chromium
    # Everything on this server is backed up by the byte: no second copy of each download.
    Run uv cache clean
    Run npm.cmd cache clean --force
    Run powershell -NoProfile -File scripts\check_settings.ps1 -ReleaseRoot $root
    # WinSW reads the definition at each start; its start and failure settings only at install.
    Copy-Item deploy\dental-practice-admin.xml .
    if (-not (Get-Service dental-practice-admin -ErrorAction SilentlyContinue)) {
        Copy-Item $env:TEMP\winsw.exe dental-practice-admin.exe
        Run .\dental-practice-admin.exe install
    }
    if ($password) { Run sc.exe config dental-practice-admin obj= ".\$account" password= $password }
    Start-Service dental-practice-admin
    $health = $null; $deadline = (Get-Date).AddMinutes(2)
    while (-not $health -and (Get-Date) -lt $deadline) { Start-Sleep 5; try { $health = Invoke-RestMethod http://127.0.0.1:8080/health } catch { } }
    if (-not $health -or $health.status -ne 'ok' -or $health.principle -ne 'production') { throw "unhealthy: $($health | ConvertTo-Json -Compress); see C:\ProgramData\DentalPracticeAdmin\logs" }
} catch {
    Write-Host 'The scheduled launcher stays paused until this script succeeds.' -ForegroundColor Red
    throw
}

# Caddy, firewall, launcher. The Caddyfile reads ADMIN_HOST from caddy.env; other services on this
# server add their own sites to C:\ProgramData\Caddy\sites.
$caddyConfig = { (Get-Content C:\ProgramData\Caddy\Caddyfile, C:\ProgramData\Caddy\caddy.env -Raw -ErrorAction SilentlyContinue) -join '' }
$before = & $caddyConfig
New-Item -ItemType Directory -Force C:\ProgramData\Caddy\sites | Out-Null
Copy-Item deploy\Caddyfile C:\ProgramData\Caddy\Caddyfile
Set-Content C:\ProgramData\Caddy\caddy.env "ADMIN_HOST=$adminHost"
$caddyfileChanged = (& $caddyConfig) -ne $before
Run caddy validate --config C:\ProgramData\Caddy\Caddyfile --adapter caddyfile --envfile C:\ProgramData\Caddy\caddy.env
New-Item -ItemType Directory -Force 'C:\Program Files\Caddy' | Out-Null
Copy-Item deploy\caddy-service.xml 'C:\Program Files\Caddy'
if (-not (Get-Service caddy -ErrorAction SilentlyContinue)) {
    Copy-Item $env:TEMP\winsw.exe 'C:\Program Files\Caddy\caddy-service.exe'
    Run 'C:\Program Files\Caddy\caddy-service.exe' install
    Run sc.exe config caddy obj= 'NT SERVICE\caddy'
}
Run icacls C:\ProgramData\Caddy /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'NT SERVICE\caddy:(OI)(CI)M'
if ($caddyfileChanged -and (Get-Service caddy).Status -eq 'Running') { Restart-Service caddy }
Start-Service caddy
if (-not (Get-NetFirewallRule -DisplayName 'Caddy HTTP and HTTPS' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'Caddy HTTP and HTTPS' -Direction Inbound -Protocol TCP -LocalPort 80, 443 -Action Allow -Profile Any | Out-Null }
# The launcher keeps the password Windows stored for it, so it is registered again only when it is
# missing, or was registered from anything but this deploy\task-runner.xml (with no record of what
# it was registered from, it counts as different); otherwise it is switched back on. S4U, which
# stores no password, is refused here: Access is denied, even to an administrator.
$registered = 'C:\ProgramData\DentalPracticeAdmin\task-runner.registered'
$definition = (Get-FileHash deploy\task-runner.xml).Hash
if (-not $launcher -or $launcher.Principal.LogonType -ne 'Password' -or -not (Test-Path $registered) -or (Get-Content $registered) -ne $definition) {
    # Read-Host throws RemoteException in a remote session, which is not interactive.
    if (-not $password) { try { $password = Read-Host "Password for the $account account (its existing password)" } catch [System.Management.Automation.RemoteException] {} }
    if ($password) {
        Register-ScheduledTask -TaskPath '\Dental Practice Admin\' -TaskName 'Task runner' -Xml (Get-Content deploy\task-runner.xml -Raw) -User $account -Password $password -Force | Out-Null
        Set-Content $registered $definition
    } elseif ($launcher) {
        # Schedules keep running on the launcher as it was; verify.ps1 fails until it is registered.
        $launcher | Enable-ScheduledTask | Out-Null
        Write-Warning "The launcher keeps its previous definition. Run this again in an interactive PowerShell, with the $account password, to update it."
    } else {
        throw "No password entered, so there is no scheduled launcher; run this again in an interactive PowerShell."
    }
} else {
    $launcher | Enable-ScheduledTask | Out-Null
}
Remove-Item $env:TEMP\winsw.exe
Remove-Item -Recurse -Force $env:TEMP\chocolatey -ErrorAction SilentlyContinue
Write-Host "Done: $(git rev-parse --short HEAD)." -ForegroundColor Green
