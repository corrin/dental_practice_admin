# Builds the self-contained release scripts\install_server.ps1 installs: this commit's files,
# a standalone Python with every runtime package installed, node_modules and the browser.
# Nothing is built or downloaded on the server. Output: build\dental-practice-admin.zip.
$ErrorActionPreference = 'Stop'
# The exit code decides: Windows PowerShell 5.1 turns a tool's stderr notice into an error when
# output is redirected.
function Run { $ErrorActionPreference = 'Continue'; & $args[0] @($args | Select-Object -Skip 1); if ($LASTEXITCODE) { throw "failed (exit $LASTEXITCODE): $args" } }
Set-Location (Split-Path $PSScriptRoot -Parent)
$build = Join-Path (Get-Location) 'build'
$release = Join-Path $build 'release'
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory $release | Out-Null

Run git archive --format=tar -o "$build\source.tar" HEAD
Run tar -xf "$build\source.tar" -C $release

# uv's standalone Python runs from any folder, unlike a virtual environment.
$version = [regex]::Match((Get-Content pyproject.toml -Raw), 'requires-python\s*=\s*">=(\d+\.\d+)').Groups[1].Value
Run uv python install $version --install-dir "$build\pythons"
# Beside the install, uv adds a link named for the minor version; copy the real folder.
$installed = @(Get-ChildItem "$build\pythons" -Directory -Filter 'cpython-*' | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) })
if ($installed.Count -ne 1) { throw "expected one Python in $build\pythons, found $($installed.Count)" }
Copy-Item $installed[0].FullName "$release\python" -Recurse
$python = "$release\python\python.exe"
Run uv export --locked --no-dev --no-emit-project --output-file "$build\requirements.txt"
# --break-system-packages: uv marks its Pythons as externally managed; this copy is the app's own.
Run uv pip install --python $python --break-system-packages --require-hashes -r "$build\requirements.txt"
Run uv pip install --python $python --break-system-packages --no-deps .

Push-Location $release
try {
    Run npm.cmd ci --omit=dev
    # The full Chromium only: the app's browser server runs it even when headless, so Playwright's
    # separate headless shell (280 MB) would be dead weight. tests/test_mcp_browser.py passes with
    # this alone and fails with the shell alone.
    $env:PLAYWRIGHT_BROWSERS_PATH = "$release\ms-playwright"
    Run node node_modules/playwright-core/cli.js install --no-shell chromium
} finally { Pop-Location }
Run git rev-parse --short HEAD | Set-Content "$release\RELEASE"

# From another folder, so it can't pick up this checkout's source.
Push-Location $env:TEMP
try { Run $python -c 'from dental_practice_admin.app import create_app' } finally { Pop-Location }

Run tar -a -c -f "$build\dental-practice-admin.zip" -C $release .
Write-Host "Built $build\dental-practice-admin.zip ($([math]::Round((Get-Item "$build\dental-practice-admin.zip").Length / 1MB)) MB)"
