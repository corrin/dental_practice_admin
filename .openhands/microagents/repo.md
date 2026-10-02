---
name: repo
type: repo
agent: CodeActAgent
---

# Working in this repo efficiently

The environment is prepared for you by `.openhands/setup.sh` before you start. **Do not
install tooling yourself** — no `pip install codespell`, `apt-get install aspell`,
`pip install pyspellchecker`, etc. It is already done:

- Dependencies are installed via `uv` (Python is pinned to 3.14 in `pyproject.toml`).
- Run project tools through uv: `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`.
- `codespell` is on PATH for spell-checking prose (`codespell README.md`).

## Match the work to the task — don't over-explore

- **Docs / prose edits** (`README.md`, `ARCHITECTURE.md`, `docs/**`): edit the prose
  directly. You do **not** need to read `src/` internals (auth, config, routers, ports) to
  fix wording or links. Spell-check with `codespell <file>`. Don't re-grep the same term
  across the tree repeatedly — one search is enough.
- **Code changes**: read `docs/adr/README.md` first (an ADR wins over habit — see
  `AGENTS.md`), make the change, then `uv run ruff check . && uv run mypy && uv run pytest -q`.
- **End-to-end tests** (only if your change needs them): `uv run playwright install chromium`
  then `uv run pytest -m e2e -q`. Skip otherwise — they are deselected by default.

## CI note

CI (`.github/workflows/ci.yml`) is **Windows-only by design** (path/tz/Playwright behaviour
that doesn't reproduce on Linux). The Linux-safe equivalent you can run here is the
`ruff` / `mypy` / `pytest -q` subset above; the integration tier needs staging credentials
and is a release gate, not a CI/agent step.

## Opening a pull request (important)

The built-in "push" / "create PR" action uses OpenHands' single provider token, which on
this instance belongs to another project and has **no write access here** — so the native
button cannot push or open PRs for this repo. Use this repo's own credential instead: the
`dental_admin_github_pat` custom secret (OpenHands Settings > Secrets), which `setup.sh`
already wires into `origin`.

Workflow (never work on `main` — see `AGENTS.md`):

1. Create a feature branch, make the change, commit.
2. Open the PR with a single command:

   ```bash
   dental_admin_github_pat="${dental_admin_github_pat}" bash .openhands/open-pr.sh "Your PR title"
   ```

   It pushes the current branch with the dental PAT and opens the PR (base `main`), printing
   the PR URL. The leading `dental_admin_github_pat=...` keeps the secret reference on the
   command line so it is available regardless of how OpenHands injects secrets.

Fallback if the helper is unavailable: `setup.sh` already pointed `origin` at the secret, so
`git push -u origin <branch>` works; then open the PR from the "Create a pull request" URL
GitHub prints.
