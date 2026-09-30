<#
.SYNOPSIS
  Runs the integration tier against Principle staging. A release gate, not a default.

.DESCRIPTION
  These tests reach api.staging.principle.dental and assert by reading state back. They
  refuse rather than skip when credentials are absent, because a skip and a pass look
  identical in a summary line.

  Requires PRINCIPLE_API_KEY and PRINCIPLE_PRACTICE_ID in the environment or in .env.
  Refuses to run against production.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = 'https://api.staging.principle.dental'
)

$ErrorActionPreference = 'Stop'

if ($BaseUrl -match 'api\.principle\.dental') {
    throw "Refusing to run the integration tier against production ($BaseUrl)."
}

$env:PRINCIPLE_ENVIRONMENT = 'staging'
$env:PRINCIPLE_API_BASE_URL = $BaseUrl

if (-not $env:PRINCIPLE_API_KEY) {
    Write-Host 'PRINCIPLE_API_KEY is not set; the suite will refuse and say so.' -ForegroundColor Yellow
}

Write-Host "Integration tier against $BaseUrl" -ForegroundColor Cyan
uv run pytest -m integration -v
exit $LASTEXITCODE
