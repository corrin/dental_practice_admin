<#
.SYNOPSIS
  Install the pre-commit hook that refuses to commit patient data or credentials.

.DESCRIPTION
  Convenience only. The gate is tests/test_no_leaked_data.py, because a hook lives in .git: absent
  on a fresh clone, absent in CI, and skipped by --no-verify. Run this once per clone.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$hook = Join-Path $repo '.git/hooks/pre-commit'

$body = @'
#!/bin/sh
# Installed by scripts/install_hooks.ps1. See tests/test_no_leaked_data.py for the real gate.
exec uv run python scripts/scan_for_leaks.py --staged
'@ -replace "`r`n", "`n"

# No BOM, and LF endings. Windows PowerShell's -Encoding utf8 writes a byte order mark, which lands
# in front of the shebang and makes git report "cannot spawn .git/hooks/pre-commit: No such file or
# directory" -- a message that says nothing about the actual cause.
[System.IO.File]::WriteAllText($hook, $body, [System.Text.UTF8Encoding]::new($false))

Write-Host "Installed $hook" -ForegroundColor Green
Write-Host 'Commits are now refused if they carry patient data, staff identities or credentials.'
