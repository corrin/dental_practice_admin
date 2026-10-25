<#
.SYNOPSIS
  Runs the integration tier against Principle production, reading only. A release gate, not a
  default.

.DESCRIPTION
  These tests read api.principle.dental (ADR 0006: staging's copy goes stale, so it is not
  evidence of what Principle returns). Nothing in the tier writes. They refuse rather than skip
  when credentials are absent, because a skip and a pass look identical in a summary line.

  Requires PRINCIPLE_API_KEY_PROD and PRINCIPLE_PRACTICE_ID_PROD in the environment or in .env.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host "Integration tier against production, reading only" -ForegroundColor Cyan
uv run pytest -m integration -v
exit $LASTEXITCODE
