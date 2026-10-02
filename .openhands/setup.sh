#!/usr/bin/env bash
# OpenHands runs this automatically when a runtime is initialised for this repo.
# Purpose: hand the agent a ready environment so it never has to install tooling
# mid-task. The project is uv-managed and pinned to Python 3.14 (see pyproject.toml),
# and proofreading tasks want a spell checker on PATH -- provide both up front.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Ensure uv (the project's package/Python manager).
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

# 2. Install project + dev dependencies, fetching Python 3.14 if the runtime lacks it.
#    --frozen keeps it reproducible against uv.lock.
uv sync --frozen

# 3. Pre-install the doc tooling the agent otherwise reaches for mid-task (codespell).
#    `uv tool install` puts it on PATH as a plain `codespell` command.
uv tool install codespell >/dev/null 2>&1 || uv tool upgrade codespell >/dev/null 2>&1 || true

# 4. Wire git auth for pushing to this repo, if the dental PAT secret is present.
#    OpenHands' single provider token belongs to another project and cannot write here;
#    the `dental_admin_github_pat` custom secret (Settings > Secrets) is this repo's own
#    credential. Point `origin` at it so plain `git push` works. Guarded so this is a
#    no-op anywhere the secret is absent (local clones, CI) -- those keep their own origin.
if [ -n "${dental_admin_github_pat:-}" ]; then
  git remote set-url origin \
    "https://x-access-token:${dental_admin_github_pat}@github.com/corrin/dental_practice_admin.git"
  echo "setup.sh: origin wired to dental_admin_github_pat for pushes."
fi

echo "setup.sh ready: $(uv run python --version); ruff/mypy/pytest via 'uv run', codespell on PATH."
