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

.EXAMPLE
  .\scripts\run_dev.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$NoSeed
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent

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
# OpenAI's own documented overrides, so the application needs no knowledge that it is simulated.
$env:OPENAI_BASE_URL = "http://127.0.0.1:$aiPort/v1"
$env:OPENAI_API_KEY = 'fake-openai-key'

$processes = @()
function Start-Server {
    param([string]$Target, [int]$On, [string]$Label)
    Write-Host "  starting $Label on $On" -ForegroundColor DarkGray
    $started = Start-Process -FilePath 'uv' -PassThru -NoNewWindow -WorkingDirectory $repo `
        -ArgumentList @(
            'run', 'python', '-m', 'uvicorn', $Target,
            '--host', '127.0.0.1', '--port', "$On"
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
    Start-Server 'tests.fake_ai.server:app' $aiPort        'fake AI'        | Out-Null
    Start-Server 'principle_admin.app:app'  $Port          'application'    | Out-Null

    # Each fake refuses a call it does not serve, and the refusal is the readiness signal.
    Wait-ForHttp "http://127.0.0.1:$principlePort/v1/practices" @(401, 403, 500) 'fake Principle'
    Wait-ForHttp "http://127.0.0.1:$aiPort/v1/responses"        @(500)           'fake AI'
    Wait-ForHttp "http://127.0.0.1:$Port/health"                @(200)           'application'

    if (-not $NoSeed) {
        Write-Host '  seeding example diary runs' -ForegroundColor DarkGray
        foreach ($day in '2026-09-28', '2026-09-29', '2026-09-30') {
            uv run python -m principle_admin.tasks diary --date $day --initiator 'run_dev' | Out-Null
        }
    }

    Write-Host ''
    Write-Host "  Runs and tasks   http://127.0.0.1:$Port/" -ForegroundColor Green
    Write-Host "  Chat             http://127.0.0.1:$Port/chat" -ForegroundColor Green
    Write-Host "  Health           http://127.0.0.1:$Port/health" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  Signed in as the local development user; there is no Google round-trip in `fake`.'
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
