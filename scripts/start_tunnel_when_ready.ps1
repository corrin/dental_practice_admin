<#
.SYNOPSIS
  Wait for the application, then publish it on the dev domain through ngrok.

.DESCRIPTION
  The one piece of ordering tasks.json cannot express. Everything else starts in parallel and
  sorts itself out; the tunnel is the public edge, and opening it before the application answers
  means the first thing a visitor sees is a 502 — which reads as "the application is broken" when
  it is merely still starting.

  Same shape as DocketWorks' scripts/ops/start_ngrok_when_ready.sh, for the same reason.

  The authtoken comes from ngrok's own configuration. Putting one in this repository is how
  SMS_Bridge ended up holding a live credential in a tracked-adjacent file.
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$Domain = 'massey-admin-dev.ngrok-free.app',
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    throw 'ngrok is not on PATH. Install it, or start the stack without the tunnel.'
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$last = 'no response'
while ((Get-Date) -lt $deadline) {
    try {
        $code = [int](Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" `
            -TimeoutSec 3 -UseBasicParsing).StatusCode
        if ($code -eq 200) {
            Write-Host "application ready on $Port; publishing https://$Domain" -ForegroundColor Green
            # exec, so VS Code's stop button stops ngrok rather than this wrapper.
            ngrok http "--url=$Domain" $Port --log=stdout
            exit $LASTEXITCODE
        }
        $last = "status $code"
    } catch [System.Net.WebException] {
        # -SkipHttpErrorCheck is PowerShell 7+; on 5.1 a non-2xx arrives as an exception.
        $last = $_.Exception.Message
    }
    Start-Sleep -Milliseconds 500
}

throw "The application did not answer on port $Port within ${TimeoutSeconds}s ($last)."
