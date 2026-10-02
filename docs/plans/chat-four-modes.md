# Reliable chat workflows that can be promoted to scheduled tasks

## Outcome

Explore in chat, produce a working script, review and test it, then schedule the same source.
Prefer the official API, proven Playwright scripts, verified Firestore reads, then AI browsing.
Inspect uncertain writes before retry or fallback. Maintenance is the goal; code size is a
constraint. Preserve the diary and existing task/results pages.

## Implementation

- FastMCP consumes one released, example-free OpenAPI snapshot. Chat, scripts and the diary use
  one server factory/client. Retain trusted practice scope, pagination, completeness, response
  validation and patient-free incompatibility warnings. Delete the bespoke generator/catalogue
  and duplicate adapter after migrating consumers; keep a small explicit snapshot update command.
- Firestore supports scoped document reads, queries and aggregations. Renew Firebase tokens;
  after a rejected refresh, sign in once and fail clearly if that fails. Ship no direct writes.
- Both browser modes use pinned Microsoft Playwright MCP, an automation-owned profile and one
  cross-process lock covering the entire workflow. Start/close the server inside that lifetime.
  Login and workspace selection are automatic and deterministic. API/Firestore work stays concurrent.
  An inner browser agent supplies the last fallback; scheduled jobs do not invoke a model.
- Chat saves immutable Python and Playwright drafts with owner, thread, inputs and results.
  Python executes in supervised worker processes; Playwright uses the server's code runner.
  Cancellation/shutdown stops the process tree. This is trusted automation, not a sandbox.
- Remove custom turn/result caps, explicitly set max_turns=None and retain transport timeouts.
  Before a change, state and check the intended action; afterward read back. Preserve truthful
  partial/uncertain results. Use docs/principle as the only operational knowledge location.

## Promotion

Export the working source, input contract and synthetic acceptance examples into a PR. Test,
register a named task and release. Chat and CLI execute the same released script with explicit
inputs; Windows Task Scheduler alone owns scheduling. No model or silent AI fallback runs in a
scheduled task. Preserve the diary command and output. Exploratory tool calls require a complete
deterministic script before promotion. Keep credentials and patient-derived inputs out of Git.

## Acceptance

- Equal outcomes from chat and scheduled execution of the same source/inputs; no scheduled model.
- Startup rejects missing/inconsistent config; environments cannot share credentials/profiles.
- Expired tokens and signed-out profiles recover; invalid credentials fail explicitly.
- Browser workflows cannot interleave across chats/jobs; cancellation releases locks/stops children.
- Preserve diary, pagination, completeness, authentication and conversation isolation behaviour.
- Check uncertain writes before retries; verify persisted edits, unrelated fields and restoration.
- Promote and execute a synthetic draft unchanged through the CLI.
- Staging proves API edit/restoration, Firestore changed-patients query, scripted browser and AI
  fallback. No Firestore write is required. Check reporting arithmetic against known fixtures.
- Run the release gate and 2,000-line budget. Verify service-account execution and unattended login
  after restart before staff deployment.

## Delivery

ADR 0005 is a separate commit. Work stays in PR #12. Migrate API/diary first, then Firestore,
browser, drafts and promotion. Move experimental knowledge into existing docs/tests before
deleting the address experiments; reconcile PRs #8/#11 without importing disposable code.
Install pinned dependencies at deployment; do not download packages/specs at startup. Reuse the
existing history, pages and warnings. Commit/push each working step; merge after acceptance and
remove the worktree only after confirmed merge.
