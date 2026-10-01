<#
.SYNOPSIS
  Launch the real application against the two simulations, and print the URL.

.DESCRIPTION
  Three processes, the same three the end-to-end tier uses:

    fake Principle   tests.fake.server        its own SQLite practice, no network
    fake AI          tests.fake_ai.server     the Responses API, scripted
    the application  principle_admin.app      exactly as WinSW will run it

  Nothing here is a special dev mode. The application is the production entry point with
  configuration pointing at the simulations, so if it does not launch here it will not launch on
  the practice host either. That is the point: "the tests pass" and "it starts" are different
  claims, and this is how the second one gets checked.

  Ctrl+C stops everything.

.PARAMETER Port
  Port for the application. Default 8080, matching deploy/principle-admin.xml.

.PARAMETER NoSeed
  Skip creating example diary runs.

.PARAMETER GoogleSignIn
  Use the real Google flow instead of the local developer identity. Independent of -RealAI, because
  they are independent questions; a single switch for both would re-weld what config.py separates.

  Needs PRINCIPLE_GOOGLE_CLIENT_ID, PRINCIPLE_GOOGLE_CLIENT_SECRET, PRINCIPLE_SESSION_SECRET and
  PRINCIPLE_STAFF_EMAILS, all of which .env supplies.

  Use it with -Tunnel: Google will not redirect to a host it cannot reach, and it will refuse a
  redirect URI it has not been given.

.PARAMETER RealAI
  Call the real OpenAI API instead of the simulation. Needs OPENAI_API_KEY, and costs money per
  message. Principle stays the fake regardless of either switch, so no patient record is involved.

.PARAMETER Tunnel
  Also publish the application on a real HTTPS domain through ngrok, so the Google sign-in and
  ChatKit paths can be exercised the way they will run in production, and so someone else can try
  it before DNS exists.

  The tunnel opens only after the health checks pass. Opening it first means the first thing a
  visitor sees is a connection refused, which looks like the application is broken when it is
  merely still starting -- the same discipline as DocketWorks' start_ngrok_when_ready.sh.

  The authtoken comes from ngrok's own configuration, deliberately: putting one in this repository
  is how SMS_Bridge ended up with a live credential in a file.

.EXAMPLE
  .\scripts\run_dev.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$NoSeed,
    [switch]$Tunnel,
    [switch]$GoogleSignIn,
    [switch]$RealAI,
    [string]$TunnelDomain = 'massey-admin-dev.ngrok-free.app',
    # Registered with OpenAI for $TunnelDomain. Public by construction -- ChatKit renders it into
    # the page -- and paired with the domain here because changing one without the other gives a
    # chat box that silently refuses to start.
    [string]$TunnelDomainKey = 'domain_pk_6abdbc9826848196996cb8f8fc75f39d08cc1a0f97eb9339'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent

# Load .env into this process. pydantic-settings reads the PRINCIPLE_* names from the file itself,
# but the OpenAI SDK looks OPENAI_API_KEY up in the environment, so without this -Live would find no
# key in a file that plainly contains one. Existing environment variables win, as dotenv does.
$envFile = Join-Path $repo '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $name, $value = $trimmed -split '=', 2
        $name = $name.Trim()
        if ($name -and -not (Get-Item "Env:$name" -ErrorAction SilentlyContinue)) {
            Set-Item "Env:$name" $value.Trim()
        }
    }
}

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $chosen = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $chosen
}

$principlePort = Get-FreePort
$aiPort = Get-FreePort

$env:PYTHONPATH = $repo
$env:PRINCIPLE_ENVIRONMENT = 'fake'
$env:PRINCIPLE_API_BASE_URL = "http://127.0.0.1:$principlePort"
$env:PRINCIPLE_API_KEY = 'fake-principle-key'
$env:PRINCIPLE_PRACTICE_ID = 'fake-practice-0001'
$env:PRINCIPLE_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'PrincipleAdmin-dev'

$env:PRINCIPLE_SIGN_IN = if ($GoogleSignIn) { 'google' } else { 'developer' }

if ($RealAI) {
    # Leave OPENAI_BASE_URL unset so the SDK reaches the real API.
    Remove-Item Env:OPENAI_BASE_URL -ErrorAction SilentlyContinue
    if (-not $env:OPENAI_API_KEY -or $env:OPENAI_API_KEY -eq 'fake-openai-key') {
        throw 'OPENAI_API_KEY is not set. -RealAI calls the real model and needs a real key.'
    }
} else {
    # OpenAI's own documented overrides, so the application needs no knowledge it is simulated.
    $env:OPENAI_BASE_URL = "http://127.0.0.1:$aiPort/v1"
    $env:OPENAI_API_KEY = 'fake-openai-key'
}

# ChatKit skips domain verification on localhost and enforces it everywhere else, so the tunnel
# needs the key registered for its origin or the chat box never starts.
if ($Tunnel) { $env:PRINCIPLE_CHATKIT_DOMAIN_KEY = $TunnelDomainKey }

$processes = @()
function Start-Server {
    param([string]$Target, [int]$On, [string]$Label)
    Write-Host "  starting $Label on $On" -ForegroundColor DarkGray
    $started = Start-Process -FilePath 'uv' -PassThru -NoNewWindow -WorkingDirectory $repo `
        -ArgumentList @(
            'run', 'python', '-m', 'uvicorn', $Target,
            '--host', '127.0.0.1', '--port', "$On",
            # The same flags the service and the test spine use. Behind a tunnel or a proxy,
            # without these the application builds http://127.0.0.1 absolute URLs and the Google
            # redirect stops matching the one registered with Google.
            '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'
        )
    $script:processes += $started
    return $started
}

function Wait-ForHttp {
    param([string]$Url, [int[]]$Accept, [string]$Label)
    for ($i = 0; $i -lt 120; $i++) {
        try {
            $code = (Invoke-WebRequest -Uri $Url -TimeoutSec 3 -SkipHttpErrorCheck).StatusCode
            if ($Accept -contains $code) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "$Label did not answer at $Url"
}

try {
    Write-Host 'Principle_admin, against the simulations' -ForegroundColor Cyan
    Start-Server 'tests.fake.server:app'    $principlePort 'fake Principle' | Out-Null
    if (-not $RealAI) { Start-Server 'tests.fake_ai.server:app' $aiPort 'fake AI' | Out-Null }
    Start-Server 'principle_admin.app:app'  $Port          'application'    | Out-Null

    # Each fake refuses a call it does not serve, and the refusal is the readiness signal.
    Wait-ForHttp "http://127.0.0.1:$principlePort/v1/practices" @(401, 403, 500) 'fake Principle'
    if (-not $RealAI) { Wait-ForHttp "http://127.0.0.1:$aiPort/v1/responses" @(500) 'fake AI' }
    Wait-ForHttp "http://127.0.0.1:$Port/health"                @(200)           'application'

    if (-not $NoSeed) {
        Write-Host '  seeding example diary runs' -ForegroundColor DarkGray
        foreach ($day in '2026-09-28', '2026-09-29', '2026-09-30') {
            uv run python -m principle_admin.tasks diary --date $day --initiator 'run_dev' | Out-Null
        }
    }

    if ($Tunnel) {
        Write-Host "  publishing on https://$TunnelDomain" -ForegroundColor DarkGray
        $started = Start-Process -FilePath 'ngrok' -PassThru -NoNewWindow -WorkingDirectory $repo `
            -ArgumentList @('http', "--url=$TunnelDomain", "$Port", '--log=stdout')
        $script:processes += $started
        Wait-ForHttp "https://$TunnelDomain/health" @(200) 'tunnel'
    }

    Write-Host ''
    Write-Host "  Runs and tasks   http://127.0.0.1:$Port/" -ForegroundColor Green
    Write-Host "  Chat             http://127.0.0.1:$Port/chat" -ForegroundColor Green
    Write-Host "  Health           http://127.0.0.1:$Port/health" -ForegroundColor DarkGray
    if ($Tunnel) {
        Write-Host ''
        Write-Host "  Public           https://$TunnelDomain/" -ForegroundColor Green
        Write-Host '  Chat works here: the origin is registered with OpenAI. Sign-in does not yet --'
        Write-Host '  that needs a Google OAuth client with /auth/callback on this exact origin.'
    }
    Write-Host ''
    $who = if ($GoogleSignIn) { 'real Google sign-in' } else { 'local developer identity' }
    $ai = if ($RealAI) { 'real OpenAI' } else { 'simulated AI' }
    Write-Host "  $who, $ai, fake Principle."
    Write-Host '  Principle is the fake regardless, so no patient record is involved.' -ForegroundColor DarkGray
    Write-Host '  Ctrl+C to stop.' -ForegroundColor DarkGray

    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($process in $script:processes) {
            if ($process.HasExited) {
                throw "a server exited with $($process.ExitCode); see the output above"
            }
        }
    }
} finally {
    Write-Host ''
    Write-Host 'stopping' -ForegroundColor DarkGray
    foreach ($process in $script:processes) {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
}
