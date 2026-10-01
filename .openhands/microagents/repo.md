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
