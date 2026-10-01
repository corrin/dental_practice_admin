# Massey Smiles Admin

Administrative tooling for one dental practice, over [Principle
Dental](https://principle.dental), the patient management system. Staff get a small web page of task
results and a chat interface; Windows Task Scheduler runs the tasks.

Published in case it is useful to someone, not offered as a product. It is specific to how this
practice works and deeply tied to Principle; there is no abstraction over the patient management
system and none is wanted.

It is small on purpose — 2,000 lines, [enforced by a test](tests/test_budget.py) —
because a tool one practice has to maintain should be a tool one person can read. The budget
counts what runs in production, which is the only code that can break for staff. Tests, comments
and tooling are reported but not budgeted: none of them has ever caused a phone call.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and its constraints.

> **Status: walking skeleton.** The daily diary report exists to prove the channels work —
> scheduled task, CLI, web page, chat — not because anyone needs that particular report. Expect
> to replace it with whatever your practice actually needs.

## Licence, and why AGPL

[GNU AGPL-3.0](LICENSE).

AGPL rather than GPL, deliberately. A practice that self-hosts this never *distributes* it, so
the GPL's trigger would never fire and improvements could stay private indefinitely. AGPL section 13
closes that: run a modified version as a network service your staff log into and you owe them its
source. If anyone does pick this up, their improvements come back.

Not affiliated with Principle Dental. The API is used as a customer.
[`tests/spec/fingerprint.json`](tests/spec/fingerprint.json) is a normalized description of the
published read operations, used for [generation](#generated-principle-interface). The original
specification and its examples are not redistributed here.

## Three Principles

The application code is identical against all three; only the transport's bottom inch changes,
so auth, paging and error handling are exercised for real everywhere.

| | What it is | Used by |
| --- | --- | --- |
| **fake** | [`tests/fake/`](tests/fake/) — a real implementation with its own SQLite state, no network | the default suite, the [E2E tier](#running-the-tests), local development |
| **staging** | `api.staging.principle.dental` | the [integration tier](#running-the-tests), the [recorder](scripts/record_principle_wire.py) |
| **production** | `api.principle.dental` | the practice |

Configuration refuses to address production unless it says `PRINCIPLE_ENVIRONMENT=production`,
and refuses to call itself production while addressing anything else.

## Running the application

Python 3.14 is required. Run `uv sync --locked` once, then use
**Terminal > Run Task > Run** in VS Code. uv selects a compatible interpreter from
[`pyproject.toml`](pyproject.toml) and recreates an incompatible project virtual environment; it
can download Python when needed. After upgrading Python, run `uv sync --locked` again in each
development or release directory before starting the application. Service and scheduled-task
paths still point to `.venv\Scripts\python.exe`.

The ordinary setup is Principle staging, Google login, real OpenAI, and ngrok at
`https://massey-admin-dev.ngrok-free.app`. The command is
`uv run python scripts/run.py --preset staging --sign-in google`.
Stop it with Ctrl+C; the launcher stops its child processes. Only one local run can hold port
8080: the launcher refuses to start while something else already has it.

Keep credentials in the gitignored `.env`: `PRINCIPLE_API_KEY_STAGING`,
`PRINCIPLE_PRACTICE_ID_STAGING`, `OPENAI_API_KEY`, `ADMIN_GOOGLE_CLIENT_ID`,
`ADMIN_GOOGLE_CLIENT_SECRET`, `ADMIN_SESSION_SECRET`, and `ADMIN_STAFF_EMAILS` and/or
`ADMIN_STAFF_DOMAIN`. Register the Google callback
`https://massey-admin-dev.ngrok-free.app/auth/callback` and configure
`ADMIN_CHATKIT_DOMAIN_KEY` for that hostname. Install ngrok and authenticate it through its own
configuration. Fake Principle credentials are accepted only by the simulation, not staging.

Google login protects every route except GET `/auth/login`, `/auth/callback`, and `/health`.
Health exposes readiness metadata, not reports or filesystem paths. Settings are read once at
startup; restart after changing credentials, access lists, or configuration.

### Diagnostic commands

For a read-only check of the Principle staging API, website login, and Firestore:

```powershell
uv run python -m scripts.check_staging
uv run python -m scripts.check_staging --only api
uv run python -m scripts.check_staging --only browser --ui-env-file ../od_data/.env
```

The API uses the application's `PRINCIPLE_API_KEY_STAGING` and
`PRINCIPLE_PRACTICE_ID_STAGING` configuration. Browser checks need `PRINCIPLE_UI_EMAIL`
and `PRINCIPLE_UI_PASSWORD` in `.env`, the shell, or an explicitly supplied `--ui-env-file`.
Install Chromium once with `uv run playwright install chromium`. Use `--headed` to watch,
or `--workspace "Workspace name"` to select a different staging workspace.

Output is JSON containing counts and statuses, without credentials or record contents.
Exit code 1 means a check failed or could not confirm access. Login success alone does not
prove workspace access; Firestore transport HTTP 200 does not prove a document exists.
The Firestore probe GETs up to three staging document references observed during login.
A practice-ID mismatch is reported even if the API can read the sole returned practice.
No records, screenshots, or authenticated session files are saved.

Further scripts can import `check_api` and `staging_browser` from
[`scripts.check_staging`](scripts/check_staging.py). Within `with staging_browser() as session`,
call `session.login(email, password)`, use `session.page` for Playwright navigation, and
`session.read_document(name)` for an exact Firestore GET under that user's permissions. Record
bodies and tokens remain in memory; callers must keep them out of logs and version control.

### Presets and configuration

Principle, sign-in, and AI are independent. These examples change only the choices named:

```powershell
uv run python scripts/run.py --preset fake --sign-in developer  # local fakes, NO GOOGLE LOGIN
uv run python scripts/run.py --principle fake                  # Google and real AI, fake records
uv run python scripts/run.py --ai fake                         # Google and staging records, fake AI
uv run python scripts/run.py --preset production               # explicit live-data investigation
```

The fake preset selects the two simulations and local access; disabling Google still requires
`--sign-in developer`. It creates one example report if fake history is empty. Production
Principle always requires Google. A production investigation needs production credentials;
the loader selects credentials from the chosen environment's section.

CLI selections override `.env` and the shell. For settings not selected on the command line,
shell variables override `.env`. `PRINCIPLE_ENVIRONMENT` selects Principle (default `staging`).
Group its key and practice ID under matching `_FAKE`, `_STAGING`, and `_PROD` settings in `.env`.
Optional endpoint overrides use `PRINCIPLE_API_BASE_URL_FAKE`, `_STAGING`, or `_PROD`; otherwise
standard endpoints apply. Shared, unscoped Principle credentials are not used. Missing selected
credentials fail startup.
`--principle` overrides `--preset`, which overrides the configured environment.
`ADMIN_SIGN_IN` configures authentication; `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and
`ADMIN_AGENT_MODEL` configure AI. Use only the `OPENAI_*` spellings for its key and endpoint.
`ADMIN_PUBLIC_BASE_URL` overrides the access address for diagnostics. Presets are shortcuts,
not restrictions on mixing providers. Developer identity is prominently announced and has no
sign-out button because it does not establish a Google session.

## Running the tests

```powershell
uv sync
uv run pytest                          # fake only: hermetic, fast, needs no credentials
uv run pytest -m e2e                   # fake, task and web app as separate processes, in a browser
uv run pytest -m integration           # the real staging API; refuses if unconfigured
uv run pytest -m llm                   # bounded real OpenAI check with synthetic text; costs tokens
uv run python -m scripts.code_size     # the 2,000-line budget, counted
```

The integration tier needs `PRINCIPLE_API_KEY_STAGING` and `PRINCIPLE_PRACTICE_ID_STAGING` for a
**staging** workspace. It is a release gate, deliberately absent from
[CI](.github/workflows/ci.yml), which stays hermetic.

[`scripts/release_gate.ps1`](scripts/release_gate.ps1) runs the fixed release checks: installed
imports, Caddy validation, lint, types, local tests, browser tests, Principle staging, and the
synthetic real-model check. It requires Caddy, Playwright Chromium, network access, and
staging/OpenAI credentials. Missing prerequisites fail the gate. Run individual test commands for
diagnosis; partial checks do not certify a release. No release command exercises production
records.

## Generated Principle interface

Endpoint definitions and chat tool schemas are generated from the committed
[`tests/spec/fingerprint.json`](tests/spec/fingerprint.json) snapshot.
[`generated_principle.json`](src/dental_practice_admin/generated_principle.json) is a generated
artifact: do not edit it. The shared HTTP executor supplies authentication, practice scope,
validation and pagination. No endpoint-specific wrapper functions are maintained.

```powershell
# Offline regeneration and verification
uv run python -m scripts.refresh_spec --generate
uv run python -m scripts.refresh_spec --check

# Read the published specification without changing files
uv run python -m scripts.refresh_spec --upstream-check

# Fetch, show changes, regenerate, and run focused tests
uv run python -m scripts.refresh_spec --update
```

The update leaves a normal working-tree diff for review. Commit the snapshot, compatibility
configuration and generated output together, run the release gate, and release the application.
The running app never fetches or adopts a new interface automatically. The API's `/v1` and
OpenAPI version string do not establish compatibility; checks compare content.

Run [`scripts/install_hooks.ps1`](scripts/install_hooks.ps1) once per clone to install the
pre-commit hook. After the leak scan it checks the **staged** generator, snapshot, compatibility
configuration and artifact in a temporary directory. It neither contacts Principle nor edits or
stages files. CI checks the same deterministic output offline; the integration/release tier checks
the published interface. Missing files, incompatible changes and unavailable upstream checks fail
explicitly.

[`tests/spec/compatibility.json`](tests/spec/compatibility.json) holds verified deviations and
coverage rules. Its patches name the expected upstream value, so an upstream fix requires review
instead of silently applying an obsolete exception. Additional unused response fields are
accepted. Missing required fields, wrong types and invalid response envelopes are refused.

Chat exposes generated, practice-scoped business reads and the diary tools. Patient-specific
paths without a proven practice boundary, administration and writes are excluded. Generated
searches and pages are labelled partial unless complete coverage has been verified. Patient
search cannot establish a whole-practice patient total. Large results require a narrower query.
The diary fake models verified diary behaviour; synthetic generation/transport tests exercise
the generic adapter without claiming to verify other live endpoints.

### Production incompatibility warning

Normal production reads detect incompatible response shapes, invalid JSON, repeated cursors,
duplicate rows and HTTP 404/405/410/422 responses. A deduplicated warning persists in SQLite
and appears on authenticated admin pages when loaded. It says **possible incompatibility**:
an endpoint error can also mean a missing resource, and an unchanged response shape cannot
prove unchanged business meaning. Authentication failures, rate limits and server outages
remain ordinary request failures rather than interface-change alerts.

Only operation names, reason codes, interface hashes and timestamps are stored in these warnings.
The app does not retry through Firestore or the browser, regenerate itself, send email, or poll
in the background. The warning survives restarts and successful responses from the same
interface. A successful response for the affected operation after release of a changed interface
clears it. Investigation and interface updates belong to the development/release workflow.

Firestore contract checks belong alongside live integration tests when a verified Firestore
operation is added. Sampling documents is not treated as an authoritative schema.

## Why the fake is built the way it is

The fake computes every answer from its own state. It never replays a stored response, because
a stored answer stops being true the moment state changes. It refuses rather than guesses in
three places: a route it does not serve, a query parameter it does not apply, and an error body
nobody has recorded.

This is not theoretical. Building the fake from the published specification produced a fake that
was wrong in five ways, each found by pointing the recorder at a real staging tenant:

| The specification says | Principle actually does |
| --- | --- |
| `meta.total` is the total number of items | it is the current page's row count; `limit=1` answers `total: 1` |
| `nextOffsetId` is "the id of the last record" | it is the last record's `createdAt`, and the order is `createdAt` descending |
| an invalid `offsetId` is a client error | it is ignored, and the first page is returned |
| `/v1/.../practitioners` takes `limit` and pages | it ignores `limit` and returns no `meta` at all |
| (unstated) | the default `limit` is 20 |

Each is pinned in
[`tests/integration/test_pagination_contract.py`](tests/integration/test_pagination_contract.py),
so if Principle changes any of them a test fails rather than a report going quietly wrong. The
third one is the dangerous one: because a stale cursor is ignored rather than refused, a long walk
can silently restart and double-count, so
[`PrincipleClient.rows`](src/dental_practice_admin/principle.py) refuses on a repeated row id.

These are observations against one staging tenant on 2026-10-01, offered so the next person does
not have to rediscover them. If Principle has since changed, the tests will say so.

## Recordings are not distributed

[`tests/recordings/`](tests/recordings/) holds wire bodies captured from a real tenant, and the
success bodies are **gitignored**. They derive from real patient records, and no key-based
anonymiser deserves the confidence needed to publish its output as health information.

Run [`scripts/record_principle_wire.py`](scripts/record_principle_wire.py) against your own
staging tenant to get your own. The anonymiser denies by default — an unrecognised field is
replaced and reported rather than passed through — and it is still not a basis for publishing
patient-derived data.

Committed: [`tests/recordings/refusals/`](tests/recordings/refusals/), which are error bodies.
Those are Principle's own wording and carry no patient data, and the fake needs them so it can
only refuse in words nobody invented. See [tests/recordings/README.md](tests/recordings/README.md).

## Layout

```text
src/dental_practice_admin/     the application (budgeted with deploy/: 2,000 lines)
  config.py              which Principle, and where local data lives
  principle.py           the API client and the catalogue of calls it may make
  tasks.py               business operations and the command Task Scheduler runs
  storage.py             run history
  app.py, templates/     staff pages
tests/
  fake/                  the fake Principle: its own store, its own routes
  recordings/            refusal bodies (committed); success bodies (yours, local)
  spec/                  the derived fingerprint of the operations we call
  integration/           the staging tier and the specification drift check
  e2e/                   separate processes, real browser
scripts/                 tools: the recorder, the fingerprinter, the release gate, verify.ps1
.vscode/tasks.json       one Run entry; scripts/run.py starts the configured services
deploy/                  what runs in production: Caddy, WinSW, Task Scheduler (budgeted)
docs/adr/                rules for changing this codebase
docs/principle/          what tasks have learned about Principle's website and Firestore
```

## Deployment

Windows, natively: one Uvicorn process under WinSW, tasks under Task Scheduler.
The service invokes `dental_practice_admin.app:create_app --factory`; install the package with
`uv sync --locked` in the release directory before starting it. Runtime configuration lives in
the host's `.env` and the service environment, separately from the development checkout.

[`scripts/verify.ps1`](scripts/verify.ps1) is the gate — service identity, data directory outside
the release, health endpoint naming its configured Principle, scheduled task registered without
interactive logon, and a complete production diary recorded within the daily schedule's 26-hour
allowance. [`deploy/ACCEPTANCE.md`](deploy/ACCEPTANCE.md) holds what only a person can sign off:
the reboot, the unattended run and the restore drill.

Caddy fronts the application; [`deploy/Caddyfile`](deploy/Caddyfile) is the configuration it runs.
Access is open to the internet so staff can work from home, which makes the Google sign-in
allowlist the only access control.
