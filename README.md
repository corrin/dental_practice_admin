# Massey Smiles Admin

Administrative tooling for one dental practice, over [Principle
Dental](https://principle.dental), the patient management system. Staff get a small web page of task
results and a chat interface; the application owns schedules and Windows launches its runner.

Published in case it is useful to someone, not offered as a product. It is specific to how this
practice works and deeply tied to Principle; there is no abstraction over the patient management
system and none is wanted.

It is small on purpose, judged by Greybeard's Simplicity First rather than a fixed line cap,
because a tool one practice has to maintain should be a tool one person can read. The counter
reports what runs in production, which is the only code that can break for staff. Tests, comments
and tooling are reported separately: none of them has ever caused a phone call.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and its constraints.

> Practice reports and other tasks live in each practice's private repository. The application
> supplies chat, execution, review, results and scheduling.

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

Keep every setting in the gitignored `.env`. The fields of `Settings` in
[`config.py`](src/dental_practice_admin/config.py) are the complete list, and startup names any
that is missing; nothing falls back to a value in the code. Register the Google callback
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
shell variables override `.env`. No setting has a default in the code: every one is set in
`.env` or the environment, and a missing one fails startup. `PRINCIPLE_ENVIRONMENT` selects
Principle. Group its endpoint, key and practice ID under matching `_FAKE`, `_STAGING`, and
`_PROD` settings in `.env`, such as `PRINCIPLE_API_BASE_URL_STAGING`. Shared, unscoped Principle
credentials are not used.
`--principle` overrides `--preset`, which overrides the configured environment.
`ADMIN_SIGN_IN` configures authentication; `OPENAI_BASE_URL` (`https://api.openai.com/v1` for
the real model), `OPENAI_API_KEY`, and `ADMIN_AGENT_MODEL` configure AI. Use only the
`OPENAI_*` spellings for its key and endpoint. `ADMIN_PUBLIC_BASE_URL` is the access address;
set it empty to take the address from each request. The fake preset sets the fake bank, the
fake AI and `http://localhost:8080` itself. The fake Principle is whatever the `_FAKE` settings
in `.env` name, and the launcher serves it on the port `PRINCIPLE_API_BASE_URL_FAKE` gives. Presets are shortcuts,
not restrictions on mixing providers. Developer identity is prominently announced and has no
sign-out button because it does not establish a Google session.

## Running the tests

```powershell
uv sync
uv run pytest                          # fake only: hermetic, fast, needs no credentials
uv run pytest -m e2e                   # fake, task and web app as separate processes, in a browser
uv run pytest -m integration           # the real staging API; refuses if unconfigured
uv run pytest -m llm                   # bounded real OpenAI check with synthetic text; costs tokens
uv run python -m scripts.code_size     # application size, counted
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

## Principle interface and workflow promotion

FastMCP builds operations from `src/dental_practice_admin/principle_openapi.json`, the
released example-free OpenAPI snapshot. Chat, Python drafts and scheduled tasks share
its executor, practice scope, response validation and pagination. No endpoint generator
or generated adapter needs maintaining.

```powershell
uv run python -m scripts.refresh_spec --check          # offline schema validation
uv run python -m scripts.refresh_spec --upstream-check # compare published interface
uv run python -m scripts.refresh_spec --update         # explicit reviewed update
```

Updates enter production through a release. The pre-commit hook validates the staged
snapshot; CI validates the committed snapshot. Live release checks compare content,
since the upstream version string does not reliably identify changes.

Chat can use scoped API reads and writes, verified Firestore reads, deterministic
Playwright scripts and an AI browser fallback. Search results and pages carry partial
coverage unless completeness is established. Scripts share the tested API pagination.
Read saved state after a write and before retrying an uncertain operation.

`run_script` saves an immutable Python or Playwright draft, its explicit inputs and its
owner, then runs it in a supervised process. This is trusted automation under the service
account, not a sandbox. The owner can export source from the returned draft link.
Both languages return `summary`, `detail` and `coverage` (`complete` or `partial`).
Python defines `async run(services, inputs)`; Playwright defines `async (page, inputs)`.
`services.api`, `services.firestore.read` and `services.browser` share the chat integrations.

Task development creates local Git history under the environment's data directory. Pass the
returned `task_id` when refining the same task. Draft repositories have no remote. Direct chat
calls also save source and an execution audit. Patient inputs and results stay on the host;
credentials are excluded. The Results page links to each run's local JSONL audit.

Open **Reports & scripts** from Results to run installed reports, inspect recent results and
logs, or set automatic runs. Input contracts appear as labelled fields. Running a report shows
activity immediately and opens its result when execution finishes.

In chat, **Save to Reports & scripts** prepares the selected answer as a deterministic script.
The preparation action can create files and run synthetic tests, but cannot execute against the
practice. The **Test run** button explicitly runs the candidate with the entered inputs; **Save**
requires successful execution of that exact source and never repeats it. An identical previously
executed draft can reuse its evidence. Saved scripts are available to all signed-in staff while
the source conversation and draft executions remain private. Reports and scripts are one type.

**Request review for scheduling** records a local request in the saved version's
`review-request.json`. Maintainers inspect pending requests under the environment's
`saved/*/*/` directory, including source, input contract and synthetic tests. After checking for
patient data, `saved_scripts.publish_review(settings, name, revision, True)` exports that
version through the existing review process. This action sends no notification. Install the
merged version with `task_files.install_existing`; the matching saved entry then gives way to
the reviewed version and scheduling becomes available. Existing schedules retain their pins.
Shared packages survive private draft cleanup. Back them up with the runtime data directory.

Maintainers publish reviewed source, its input contract and synthetic tests using
`task_files.publish`, then install merged revisions with `task_files.install_existing`.
Configure `ADMIN_TASK_REPOSITORY` as `owner/private-repository` and `ADMIN_GITHUB_TOKEN` with
access to its contents and pull requests. Startup refuses to run without them, as for every
other setting (ADR 0002: a missing setting stops the whole application even if only one
feature uses it), so a host cannot discover the gap only when someone first asks for a review. The repository must already have a default branch.
Only `task.json`, `source.txt` and `test_task.py` are exported; local history and audits stay
on the host. Repository setup and review are outside the staff interface.

The `dental-practice-admin` command (also `python -m dental_practice_admin.schedules`) checks
which installed tasks are due and exits with failure if any execution is missed or untrustworthy.

Practice tasks do not require an application release. Development branches expire after 90 days
without edits or runs when
the task page is opened or another draft is saved; open reviews are protected. Local audit files
and immutable source snapshots survive cleanup. Back up the entire runtime data directory.

The app owns schedules through APScheduler's SQLite job store in the existing database. Staff
choose an installed revision, inputs, calendar time/days or an interval, and can edit, pause,
resume or remove it. Installing another revision does not change existing schedules. Windows
runs `dental-practice-admin run-due` every five minutes. Executions more than five minutes late
are recorded as missed. Failed or interrupted executions are uncertain and are not retried.
Inspect saved state before repeating a change. Installed execution needs neither Git nor GitHub.

### Automation installation and login

Install Node.js and the locked server and browser under the automation identity:

```powershell
npm ci
node node_modules/@playwright/mcp/cli.js install-browser chrome-for-testing
uv sync --locked
```

Set `PRINCIPLE_UI_EMAIL`, `PRINCIPLE_UI_PASSWORD`, `PRINCIPLE_FIREBASE_KEY`,
`PRINCIPLE_FIREBASE_PROJECT`, `PRINCIPLE_FIRESTORE_ROOT`, `PRINCIPLE_WORKSPACE` and
`PRINCIPLE_WORKSPACE_SLUG`, each suffixed `_STAGING` or `_PROD`, alongside the API
settings. Unscoped Principle credentials are ignored. The workspace value is the exact
accessible dropdown option; the slug is the URL segment, which can differ from the
option's subtitle. The Firestore root is `organisations/{org}/brands/{brand}`.

Browser login runs automatically before each workflow. The persistent profile, browser
output and cross-process lock live under the environment's data directory. Only one
browser workflow runs at a time; API and Firestore work can continue. Firebase reads
refresh their tokens and sign in once if a refresh is rejected. Invalid credentials
fail explicitly. No Firestore writes are exposed. `ADMIN_PLAYWRIGHT_MCP_PATH` locates the
locked Node package's command-line entry point under `node_modules`.

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
src/dental_practice_admin/     the application (counted with deploy/)
  config.py              which Principle, and where local data lives
  principle.py           the API client and the catalogue of calls it may make
  schedules.py           application schedules and the command Task Scheduler runs
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
deploy/                  what runs in production: Caddy, WinSW, Task Scheduler (counted)
docs/adr/                rules for changing this codebase
docs/principle/          what tasks have learned about Principle's website and Firestore
```

## Deployment

Windows, natively: one Uvicorn process under WinSW and one five-minute Task Scheduler launcher.
The service invokes `dental_practice_admin.app:create_app --factory`; install the package with
`uv sync --locked` in the release directory before starting it. Runtime configuration lives in
the host's `.env` and the service environment, separately from the development checkout. Before
stopping the running service, run `.venv\Scripts\python.exe -m dental_practice_admin.config` in
the new release directory: it names any setting the release needs that the host lacks, so the
old release keeps serving until the host is ready.

[`scripts/verify.ps1`](scripts/verify.ps1) is the gate — service identity, data directory outside
the release, health endpoint naming its configured Principle, scheduled task registered without
interactive logon, the launcher interval, and recent application schedule-check evidence.
[`deploy/ACCEPTANCE.md`](deploy/ACCEPTANCE.md) holds what only a person can sign off:
the reboot, the unattended run and the restore drill.

Register `deploy/task-runner.xml` as `Massey Smiles Admin\Task runner` under the designated
unattended account. Disable and remove the old `Massey Smiles Admin\Daily diary` Windows task
before enabling application schedules; its diary command is not provided by this application.
Approve and install each real practice task through its private PR, then create its schedule
in the app. Reboot verification must include a due approved task, not just an empty poll.

Caddy fronts the application; [`deploy/Caddyfile`](deploy/Caddyfile) is the configuration it runs.
Access is open to the internet so staff can work from home, which makes the Google sign-in
allowlist the only access control.
