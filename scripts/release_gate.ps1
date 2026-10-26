<#
.SYNOPSIS
  The fixed release gate. Missing prerequisites and failed checks both prevent a release.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$script:Failed = @()
Set-Location (Split-Path $PSScriptRoot -Parent)

function Stage {
    param([string]$Name, [scriptblock]$Run)
    Write-Host "Checking $Name"
    # A stage passes or fails on its exit code. Under Windows PowerShell 5.1, a tool's notice on
    # stderr (uv's "Using CPython", npm's warnings) becomes an error record when output is
    # redirected, and Stop would fail a stage that passed.
    $ErrorActionPreference = 'Continue'
    try {
        & $Run
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    } catch {
        Write-Host "FAILED: $Name - $_" -ForegroundColor Red
        $script:Failed += $Name
    }
}

Stage 'Locked browser server' { npm.cmd ci }
Stage 'Locked dependencies' { uv sync --locked }
if ($script:Failed.Count) { exit 1 }
Stage 'Installed application' { uv run --locked python -c "from dental_practice_admin.app import create_app" }
# The Caddyfile names its site from ADMIN_HOST, which only the server's .env holds; without one the
# site has no name and Caddy refuses the file. The installer validates with the real hostname.
Stage 'Caddy configuration' {
    $saved = $env:ADMIN_HOST
    $env:ADMIN_HOST = 'admin.example.invalid'
    try { caddy validate --config deploy\Caddyfile --adapter caddyfile } finally { $env:ADMIN_HOST = $saved }
}
Stage 'Lint' { uv run --locked ruff check . }
Stage 'Types' { uv run --locked mypy }
Stage 'Released Principle interface' { uv run --locked python -m scripts.refresh_spec --check }
Stage 'Local suite' { uv run --locked pytest -q }
Stage 'End to end' { uv run --locked pytest -m e2e -q }
Stage 'Browser smoke' { uv run --locked pytest -m smoke -q }
Stage 'Principle production, reading' { & "$PSScriptRoot\run_integration_tests.ps1" }
Stage 'Real model with synthetic input' { uv run --locked pytest -m llm -q }

if ($script:Failed.Count) {
    Write-Host "Release NOT verified: $($script:Failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'Release checks passed. Host verification and deploy/ACCEPTANCE.md are still required.'
exit 0
