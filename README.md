# Principle_admin

An administrative tool for dental practices running [Principle
Dental](https://principle.dental). Staff get a small web page of task results and a chat
interface; Windows Task Scheduler runs the tasks.

It is small on purpose — 2,000 lines, enforced by a test — because a tool a single practice has to
maintain should be a tool one person can read. The budget counts what runs in production, which is
the only code that can break for staff. Tests, comments and tooling are reported but not budgeted:
none of them has ever caused a phone call.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and its constraints.

> **Status: walking skeleton.** The daily diary report exists to prove the channels work —
> scheduled task, CLI, web page, chat — not because anyone needs that particular report. Expect
> to replace it with whatever your practice actually needs.

## Licence, and why AGPL

[GNU AGPL-3.0](LICENSE).

This is meant to become a shared tool for anyone running Principle Dental, and the AGPL is
chosen deliberately over the GPL. A practice that self-hosts this never *distributes* it, so
the GPL's trigger would never fire and improvements could stay private indefinitely. AGPL
section 13 closes that: if you run a modified version as a network service your staff log into,
you owe them its source. Improve it and the next practice gets your improvements.

Not affiliated with Principle Dental. The API is used as a customer. `tests/spec/fingerprint.json`
is a derived description of published endpoints — parameter names and response shape for the
three operations this project calls — not a copy of Principle's specification, which is
deliberately not redistributed here.

## Three Principles

The application code is identical against all three; only the transport's bottom inch changes,
so auth, paging and error handling are exercised for real everywhere.

| | What it is | Used by |
| --- | --- | --- |
| **fake** | `tests/fake/` — a real implementation with its own SQLite state, no network | the default suite, the E2E tier, local development |
| **staging** | `api.staging.principle.dental` | the integration tier, the recorder |
| **production** | `api.principle.dental` | the practice |

Configuration refuses to address production unless it says `PRINCIPLE_ENVIRONMENT=production`,
and refuses to call itself production while addressing anything else.

## Running the tests

```powershell
uv sync
uv run pytest                          # fake only: hermetic, fast, needs no credentials
uv run pytest -m e2e                   # fake, task and web app as separate processes, in a browser
uv run pytest -m integration           # the real staging API; refuses if unconfigured
uv run python -m tests.test_budget     # the 2,000-line budget, counted
```

The integration tier needs `PRINCIPLE_API_KEY` and `PRINCIPLE_PRACTICE_ID` for a **staging**
workspace. It is a release gate, deliberately absent from CI, which stays hermetic.

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

Each is pinned in `tests/integration/test_pagination_contract.py`, so if Principle changes any
of them a test fails rather than a report going quietly wrong. The third one is the dangerous
one: because a stale cursor is ignored rather than refused, a long walk can silently restart and
double-count, so `PrincipleClient.rows` refuses on a repeated row id.

These are observations against one staging tenant on 2026-10-01, offered so the next person does
not have to rediscover them. If Principle has since changed, the tests will say so.

## Recordings are not distributed

`tests/recordings/` holds wire bodies captured from a real tenant, and the success bodies are
**gitignored**. They derive from real patient records, and no key-based anonymiser deserves the
confidence needed to publish its output as health information.

Run `scripts/record_principle_wire.py` against your own staging tenant to get your own. The
anonymiser denies by default — an unrecognised field is replaced and reported rather than passed
through — and it is still not a basis for publishing patient-derived data.

Committed: `tests/recordings/refusals/`, which are error bodies. Those are Principle's own
wording and carry no patient data, and the fake needs them so it can only refuse in words nobody
invented. See [tests/recordings/README.md](tests/recordings/README.md).

## Layout

```text
src/principle_admin/     the application (budgeted with deploy/: 2,000 lines)
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
deploy/                  what runs in production: Caddy, WinSW, Task Scheduler (budgeted)
```

## Deployment

Windows, natively: one Uvicorn process under WinSW, tasks under Task Scheduler.

`scripts/verify.ps1` is the gate — service identity, data directory outside the release, health
endpoint naming which Principle it reached, scheduled task registered without interactive logon.
`deploy/ACCEPTANCE.md` holds what only a person can sign off: the reboot, the unattended run and
the restore drill.

Caddy fronts the application; `deploy/Caddyfile` is the configuration it runs. Access is open to
the internet so staff can work from home, which makes the Google sign-in allowlist the only
access control.
