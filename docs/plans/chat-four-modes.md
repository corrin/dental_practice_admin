# Chat drives Principle through four generic modes, all working from day 1

## Context

Staff should be able to ask chat to do anything in Principle, including tasks nobody
anticipated, like "move this appointment to 3pm".

Each mode gets one generic tool surface, not per-task code. The agent prefers the most
structured mode that can do the job:

1. **API:** structured and validated, so the safest.
2. **Firestore:** structured, but undocumented and it bypasses Principle's handling.
3. **Scripted browser:** the model writes a fixed list of steps from the documentation, and
   they run deterministically.
4. **AI-driven browser:** the model looks at the page and acts, step by step.

The address scripts (`scripts/address_via_*.py`, and the browser address test on PRs #8/#11)
were a vague proof of concept. This plan deletes them (section 6).

## Knowledge: one documentation folder per mode

The docs ship with the app, in `src/dental_practice_admin/principle_docs/`:

| Folder | Holds | Seeded from |
|---|---|---|
| `api/` | Verified behaviour the spec doesn't state. For example, `updatePatient` must resend name, date of birth, gender and email; paging and `meta.total` differ from the spec. | The README pagination table |
| `firestore/` | Collections, paths and field meanings; the write caveat | `docs/principle/firestore.md` |
| `browser_scripted/` | Routes, login and workspace, selectors, form quirks | `docs/principle/website.md` |
| `browser_ai/` | Goal-level procedures and gotchas | The generic parts of `SKILL.md` |

Each fact lives in exactly one of these files. Nothing is copied into `compatibility.json`,
the instructions, or code comments.

**How the agent uses them:**
- **Index:** the instructions list each folder's files, built at startup. They don't include
  the files' contents.
- **Reading:** a tool, `principle_docs(mode, file)`, returns one file.
- **Pointers:** each mode's tool descriptions say to read that mode's folder first.

`docs/principle/README.md` stays where it is. It keeps the just-in-time rule (each task adds
what it learned to its mode's folder) and points at the package folder.
`docs/principle/firestore.md` and `website.md` move into it.

## Modules

### 1. API: the existing generator, widened
- **`scripts/refresh_spec.py`:**
  - generate every JSON operation, 57 in all: reads, writes and `{patientId}` paths. Skip
    OAuth, webhooks and the multipart `uploadPatientFile`.
  - keep schema `description`s
  - add a `body` argument from the request body
  - fill in `practiceId` from settings wherever it appears: path, query or body
  - regenerate the snapshot and the artifact
- **`principle.py`:**
  - `PrincipleClient.get` becomes `call()`, which uses the operation's method and sends
    `json=body`.
  - A `404` on a path with an ID returns "not found", not an interface incompatibility.
  - Responses are checked against the response schema only. Per-patient responses aren't
    wrapped in `data`, so the "must have `data`" check goes.
- **`principle_tools.py`:** pass the body through, and turn strict schemas off for tools with
  a body (the spec's bodies aren't strict-compatible). Server-side validation still runs.

### 2–4. One web session module: `src/dental_practice_admin/principle_web.py`
One login serves all three web modes:
- **`PrincipleWeb(settings)`:** async Playwright, so the async chat agent calls it directly.
  - Logs in once and selects the workspace by its exact option, then uses the workspace slug
    in routes.
  - Keeps the `Page` open for the chat process's lifetime, and logs in again when it expires.
  - Keeps the Firebase ID token captured at login, replacing `check_staging.StagingBrowser`.
- **Config:**
  - `config.py`: per-environment web URL (`PRINCIPLE_WEB_URLS` already exists), workspace
    name, slug, and Firestore org/brand path.
  - `Settings`: `ui_email`, `ui_password`.

Generic tools on that session, added in `chat.build_tools`:
- **Firestore:**
  - `firestore_query(collection, filters, select, limit)`: a `runQuery` under the
    workspace's org and brand.
  - `firestore_get(document_path)`
  - `firestore_aggregate(collection, filters, function, field)`: Firestore's
    `runAggregationQuery`, which computes count, sum or average on the server. This is what
    makes reports work, for example "average lab fee".
    - Lab data isn't in the API; the website has Labs and Lab Jobs pages backed by Firestore.
    - The answer is exact over every matching record, not one page, and the model never does
      the arithmetic.
  - `firestore_update(document_path, fields)`: an update with a field mask. Its description
    says:
    - `updatedAt`/`updatedBy` aren't stamped, so prefer the API.
    - A direct write changes only stored data. Anything the web app does when a button is
      pressed (automations, sends, recalculations) doesn't run, so actions like those go
      through the browser modes.
- **Scripted browser:** `browser_steps(steps)`.
  - Each step is one of `goto(path)`, `click(selector)`, `fill(selector, value)`,
    `select_option(text)`, `wait_for(selector)` and `read(selector)`.
  - The steps run in order; it stops at the first failure and returns what it read and where
    it stopped.
- **AI browser:** `browser_task(goal)`. The observe/act loop from PR #11's
  `check_browser_address.py`, generalised:
  - **Kept:** `observation()` (URL, accessibility tree, visible inputs, screenshot),
    `run_phase`'s repeat and budget limits, and the stay-on-this-workspace URL check.
  - **Removed:** `capture`/`verify`, the fill whitelist, and the hardcoded patient.
  - Returns a summary of what it did and saw, including the steps it took. A developer can
    then record those steps in `browser_scripted/`, so the same task runs next time as the
    faster, repeatable scripted mode.

### 5. `chat.py`
- **`INSTRUCTIONS`:**
  - drop "read-only" and "cannot change anything"
  - state the mode order and why
  - ask which record is meant when a search finds several
  - include the docs index
- **`MAX_TURNS`:** raise to about 20 for multi-step tasks.

### 6. Delete the address proof of concept
- **On `main`:** `scripts/address_via_api.py`, `scripts/address_via_firestore.py`,
  `scripts/address_via_browser.py`.
- **From PRs #8/#11, after merging their branch** (see Delivery):
  `scripts/check_browser_address.py`, `scripts/browser_address_procedure.md`,
  `scripts/browser_address_test.md`, `tests/test_browser_address.py`, and the address parts of
  `skills/principle-staging-browser/SKILL.md`.

## Size

About 250 new lines of application code. The budget has 310 to spare (1690 of 2000 used). If
it runs over, raise it rather than squeezing code.

## Verification

- **Generator tests** (`tests/test_generated_principle.py`): writes are generated with a body,
  `practiceId` is filled in from settings, and `uploadPatientFile` is skipped.
- **One scripted-model chat turn per mode** in `tests/test_chat_turn.py`:
  - the API and Firestore turns run against stub HTTP transports
  - the browser turns run real Playwright against a local HTML page
- **Day-1 acceptance**, once all four PRs have merged:
  - On staging (`scripts/run.py --preset staging`), change the same Crash Test Dummy's
    address four times, once per mode, asking for the mode explicitly each time.
  - Check each change, then restore the original address.
  - Then ask "which patients changed today?". It should list that patient.
  - Then ask "create a report on the average lab fee". It should find the lab collection,
    read a document to learn the fee field, and answer with `firestore_aggregate`.

## Delivery

One PR per mode, in this order, each draft first and then auto-merge:

1. **API mode and the docs folders.** PR #12, `chat-full-api`.
2. **`principle_web.py` session and Firestore mode.**
3. **Scripted browser mode.**
4. **AI browser mode.** Its branch first merges `origin/test/english-browser-address` (PR #11,
   which contains #8), so that work is carried in, not lost. It then generalises the loop and
   deletes the address proof of concept. PRs #8 and #11 are then closed as superseded.
