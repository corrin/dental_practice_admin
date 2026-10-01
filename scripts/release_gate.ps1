<#
.SYNOPSIS
  Everything that must pass before a release. One script, run every time.

.DESCRIPTION
  Six tiers, cheapest first, so a failure surfaces as early as possible:

    1. lint and types            ruff, mypy
    2. hermetic suite            the fake Principle and the fake AI, in process
    3. code budget               application code against the 2,000-line limit
    4. end to end                fake Principle, fake AI, the task and the web application as
                                 separate processes, driven through a browser
    5. smoke                     every page loaded in a real browser; ANY console error fails
    6. integration               the REAL Principle staging API

  Tier 5 exists because this suite was once green while the chat page did not start: the
  end-to-end tests asserted the backend, and nothing asserted that a browser could use the page.
  It needs internet, because the ChatKit component loads from OpenAI's CDN, but no credentials and
  no money. Needing internet is a reason to say so, not a reason to skip it.

  Tiers 1-5 need no credentials. Tier 6 needs a staging key and is why this script exists rather
  than a CI badge: CI stays hermetic, so the only place the integration tier runs is here.

  One tier is deliberately NOT included:

    -m llm  calls the REAL model, and is the only thing that proves our wire format still matches
            OpenAI's. Run it before a release that touched chat, and when the SDK is upgraded.

  A green run of this script does not prove the application against OpenAI's API or against
  production Principle. It proves it against two simulations, in a real browser, and against
  staging.

.PARAMETER SkipIntegration
  Run tiers 1-4 only. For a change that cannot affect Principle access.

.PARAMETER SkipSmoke
  Skip the browser tier. Only when there is genuinely no internet; it is the tier that answers
  "does it launch".

.PARAMETER IncludeLive
  Also run -m llm. Slower, costs money, needs an OpenAI key.

.EXAMPLE
  .\scripts\release_gate.ps1
  .\scripts\release_gate.ps1 -SkipIntegration
  .\scripts\release_gate.ps1 -IncludeLive
#>
[CmdletBinding()]
param(
    [switch]$SkipIntegration,
    [switch]$SkipSmoke,
    [switch]$IncludeLive
)

$ErrorActionPreference = 'Continue'
$script:Failed = @()
$started = Get-Date

function Stage {
    param([string]$Name, [scriptblock]$Run)
    Write-Host ''
    Write-Host "== $Name" -ForegroundColor Cyan
    & $Run
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   FAILED: $Name" -ForegroundColor Red
        $script:Failed += $Name
    } else {
        Write-Host "   ok" -ForegroundColor Green
    }
}

Write-Host "Release gate for Principle_admin" -ForegroundColor White

Stage 'Dependencies are locked and installed' { uv sync --frozen }
if (Get-Command caddy -ErrorAction SilentlyContinue) {
    Stage 'Caddy configuration' { caddy validate --config deploy\Caddyfile --adapter caddyfile }
}
Stage 'Lint'                                  { uv run ruff check . }
Stage 'Types'                                 { uv run mypy }
Stage 'Hermetic suite (fake Principle, fake AI)' { uv run pytest -q }
Stage 'Code budget'                           { uv run python -m tests.test_budget }
Stage 'End to end (separate processes, browser)'  { uv run pytest -m e2e -q }

if ($SkipSmoke) {
    Write-Host ''
    Write-Host 'Skipping the browser tier. This run does NOT prove the pages launch.' -ForegroundColor Yellow
} else {
    Stage 'Smoke (every page in a real browser, no console errors)' { uv run pytest -m smoke -q }
}

if ($SkipIntegration) {
    Write-Host ''
    Write-Host 'Skipping the integration tier. This run does NOT prove Principle access.' -ForegroundColor Yellow
} else {
    if (-not $env:PRINCIPLE_API_KEY) {
        Write-Host ''
        Write-Host 'PRINCIPLE_API_KEY is not set. The integration tier will refuse, not skip.' -ForegroundColor Yellow
    }
    if (-not $env:PRINCIPLE_ENVIRONMENT) { $env:PRINCIPLE_ENVIRONMENT = 'staging' }
    if (-not $env:PRINCIPLE_API_BASE_URL) {
        $env:PRINCIPLE_API_BASE_URL = 'https://api.staging.principle.dental'
    }
    if ($env:PRINCIPLE_API_BASE_URL -match 'api\.principle\.dental') {
        throw "Refusing to run the release gate against production ($($env:PRINCIPLE_API_BASE_URL))."
    }
    Stage "Integration (real staging: $($env:PRINCIPLE_API_BASE_URL))" {
        uv run pytest -m integration -q
    }
}

if ($IncludeLive) {
    Stage 'Live model compatibility (real OpenAI, costs money)' { uv run pytest -m llm -q }
} else {
    Write-Host ''
    Write-Host 'Skipping -m llm. This run does NOT prove the OpenAI wire format.' -ForegroundColor Yellow
}

$elapsed = [int]((Get-Date) - $started).TotalSeconds
Write-Host ''
if ($script:Failed.Count -gt 0) {
    Write-Host "$($script:Failed.Count) stage(s) failed after ${elapsed}s:" -ForegroundColor Red
    $script:Failed | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host "All stages passed in ${elapsed}s." -ForegroundColor Green
Write-Host 'Deployment still needs scripts\verify.ps1 on the host and deploy\ACCEPTANCE.md signed.'
exit 0
