# 0008 — Every change proves itself

A pull request lists the automated tests that pass for it, and is not ready until every
requirement and bug fix in it has one.

## Rules

- The list is the pull request's description, written from
  [`.github/pull_request_template.md`](../../.github/pull_request_template.md): each requirement
  or bug fix, the test that proves it, where it ran, the command, and the result. The
  description is public, so a result is passed or failed with counts, never output.
- A test states the business requirement in the practice's terms ("a text sent to a patient is
  in that patient's messages") or the bug that tripped us, never which lines ran.
- A test runs where it means something: `hermetic` for the suite, the integration tier against
  production for reads, the recorder on staging for writes. Code that has not run there has no
  passing test, so it is listed under "Not yet passing" and the pull request stays a draft.
- A bug found gets a test that failed before the fix, and the test stays in the suite.
