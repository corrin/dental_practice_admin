# 0005 — Promote working scripts

Chat develops local task files; reviewed private-repository exports become installed tasks.

## Rules

- Minimise ongoing maintenance. Libraries own generic protocols; our code owns Principle facts.
- Prefer the official API, verified browser scripts, verified Firestore reads, then AI browsing.
  Inspect an uncertain write before retrying elsewhere.
- Chat can execute trusted Python and Playwright drafts under the automation account. This is
  trusted code execution, not a sandbox. Authentication and conversation ownership still apply.
- Draft source is saved before execution and committed on local branches. Development history,
  conversations, patient inputs and execution audits stay on the practice host.
- Standalone reuse and scheduling require a merged PR in the practice's private repository.
  Export only source, its input contract and synthetic tests onto a clean review branch.
  Install the approved revision locally without releasing the public application.
- All execution records source, inputs, calls and outcomes in local files without credentials.
  Scheduled tasks run the installed revision through the same integrations without a model,
  GitHub access or Git commands.
- The application owns schedules and saved inputs in its existing SQLite database. Staff edit
  them without a PR; changes are audited. Windows launches the due-task runner every five minutes.
  Missed occurrences are skipped, failures are visible, and uncertain writes are never retried.
- Schedules pin approved revisions. Updates are explicit. Draft branches expire after 90 days
  without edits or runs; execution records survive branch cleanup.
- FastMCP consumes one released OpenAPI snapshot. Chat and scripts share its implementation.
  Preserve verified pagination, practice scope, completeness and incompatibility observations.
- Firestore supplies scoped reads. Direct writes require a verified complete operation;
  permission to PATCH a document does not establish its business behaviour.
- Browser scripts and AI fallback share automatic login and an environment-specific profile.
  One cross-process lock covers each complete browser workflow, including cleanup.
- Required configuration is checked at startup. Authentication expiry is recoverable without a
  developer's browser. Irrecoverable authentication and uncertain writes are explicit failures.
- Use the existing run history, staff pages, database and warnings. Do not add a workflow
  designer, job service or a second source of Principle documentation.

## Observed

None yet.
