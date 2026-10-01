# Chat drives Principle through four generic modes, all working from day 1

## Context

Staff should be able to ask chat to do anything in Principle, including tasks nobody
anticipated, like "move this appointment to 3pm".

Each mode gets one generic tool surface, not per-task code. The `scripts/address_via_*.py`
files were first experiments that proved each mode works; they aren't the capabilities, and
they're deleted once the generic tools replace them.

The agent prefers the most structured mode that can do the job:

1. **API:** structured and validated, so the safest.
2. **Firestore:** structured, but undocumented and it bypasses Principle's handling.
3. **Scripted browser:** the model writes a fixed list of steps from the documentation, and
   they run deterministically.
4. **AI-driven browser:** the model looks at the page and acts, step by step.

### Knowledge: one documentation folder per mode

Each mode needs different knowledge, so each gets its own folder:
- **`docs/principle/api/`:** verified behaviour the spec doesn't state. For example,
  `updatePatient` must resend name, date of birth, gender and email, and paging works
  differently from the spec. This is the single place for those facts; it isn't also added to
  `compatibility.json`.
- **`docs/principle/firestore/`:** collections, paths and field meanings, and the write
  caveat. Seeded from today's `firestore.md`.
- **`docs/principle/browser_scripted/`:** routes, login and workspace, selectors, form quirks.
  Seeded from today's `website.md`.
- **`docs/principle/browser_ai/`:** goal-level procedures and gotchas for the AI-driven
  browser.

**How the agent uses them:**
- The instructions carry only a short index of the folders and their files.
- One tool, `principle_docs(mode, file)`, returns a page. It's a plain file read, so a growing
  folder never bloats every turn.
- Each mode's tool descriptions tell the agent to read its folder first.
- After any task that taught us something, the task adds it to that mode's folder: the
  just-in-time rule in `docs/principle/README.md`.

The docs ship with the app, so they move into the package
(`src/dental_practice_admin/principle_docs/`), and `docs/principle/` becomes a pointer to that
location.

## Modules

### 1. API: the existing generator, widened
- **`scripts/refresh_spec.py`:**
  - generate every JSON operation, 57 in all: reads, writes and `{patientId}` paths. Skip
    OAuth, webhooks and the multipart file upload.
  - keep schema `description`s
  - add a `body` argument
  - fill in `practiceId` from settings in the path, query or body
  - regenerate
- **`principle.py`:** `PrincipleClient.get` becomes `call()`, which sends `json=body`. A `404`
  on a path with an ID returns "not found", not an interface incompatibility.
- **`principle_tools.py`:** pass the body through, and turn strict schemas off for tools with
  a body.
- **`compatibility.json` `notes`:** `updatePatient` must resend name, date of birth, gender
  and email.

### 2–4. One web session module: `src/dental_practice_admin/principle_web.py`
One login serves all three web modes:
- **`PrincipleWeb(settings)`:** async Playwright, so the async chat agent can call it
  directly.
  - Logs in once and selects the workspace: exact option, slug route. The values come from
    the existing scripts and `website.md`.
  - Keeps the `Page` open for the chat process's lifetime, and logs in again when the session
    expires.
  - Keeps the Firebase ID token it captures, replacing `check_staging.StagingBrowser`.
- **Config:**
  - `config.py`: per-environment `PRINCIPLE_WEB_URLS`, which already exists; the workspace
    name and slug; the Firestore org/brand path.
  - `Settings`: `ui_email` and `ui_password`, so nothing is read from `../od_data/.env`.

Generic tools on that session, added in `chat.build_tools`:
- **Firestore:**
  - `firestore_query(collection, filters, select, limit)`: a `runQuery` under the
    workspace's org and brand.
  - `firestore_get(document_path)`
  - `firestore_update(document_path, fields)`: an update with a field mask. Its description
    states the verified fact that `updatedAt`/`updatedBy` aren't stamped.
- **Scripted browser:** `browser_steps(steps)`.
  - Each step is one of `goto(path)`, `click(selector)`, `fill(selector, value)`,
    `select_option(text)`, `wait_for(selector)` and `read(selector)`.
  - The steps run in order; it stops at the first failure and returns what it read and where
    it stopped.
  - Selectors follow `website.md`: `formcontrolname`, `pr-*` tags, visible text.
- **AI browser:** `browser_task(goal)`. The observe/act loop from PR #11's
  `check_browser_address.py`, generalised:
  - **Kept:** `observation()` (URL, accessibility tree, visible inputs, screenshot),
    `run_phase`'s repeat and budget limits, and the stay-on-this-workspace URL check.
  - **Removed:** the address- and patient-specific `capture`/`verify`, the fill whitelist,
    and the hardcoded patient.
  - Its result goes back to chat as a summary of what it did and what it saw.
  - Depends on PRs #8/#11 merging first, so the code comes from one place.

### 5. `chat.py`
- **`INSTRUCTIONS`:**
  - drop "read-only"
  - state the mode order and the reason for it
  - ask which record is meant when a search finds several
  - include `docs/principle/*.md`, read at startup
- **`MAX_TURNS`:** raise to about 20 for multi-step tasks.

### 6. Delete the address proof of concept

It was a vague proof of concept. It goes in this plan, not later. Files deleted:
- `scripts/address_via_api.py`, `scripts/address_via_firestore.py`,
  `scripts/address_via_browser.py`
- `scripts/check_browser_address.py`, `scripts/browser_address_procedure.md`,
  `scripts/browser_address_test.md`, `tests/test_browser_address.py`
- the address-specific parts of `skills/principle-staging-browser/SKILL.md`

The only things carried forward are the generic loop pieces named under the AI browser above.
They move into `principle_web.py`.

`docs/principle/*` "Verified by" lines then point at the tools and their tests.

## Size

About 250 new lines of application code against a budget that has 310 to spare (1690 of 2000
used). If that runs over, raise it rather than squeezing code.

## Verification

- **Hermetic:** one scripted-model chat turn per mode in `tests/test_chat_turn.py`.
  - The API and Firestore turns run against stub HTTP transports.
  - The browser turns run against a local HTML page served to real Playwright, the pattern
    `tests/e2e` already uses.
  - Generator tests cover writes, the `body` argument and `practiceId` injection.
- **Manual on staging, day-1 acceptance:** run `scripts/run.py --preset staging` and change
  the same Crash Test Dummy's address four times, once per mode, asking for the mode
  explicitly each time.
  - Check each change, then restore the original address.
  - Then ask "which patients changed today?". The Firestore query should list it.

## Delivery

- PR #12, `chat-full-api`, carries the API mode.
- Then one PR per mode: draft first, then auto-merge.
- The AI browser PR goes after #8/#11 merge.
