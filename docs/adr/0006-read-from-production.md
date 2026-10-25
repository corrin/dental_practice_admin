# 0006 — Read from production

Anything that establishes what Principle returns reads it from production; staging receives only
writes.

## Rules

- Staging is a copy of production taken on one day and then left. Its data can be stale or
  wrong, so a read from staging is not evidence of what Principle returns now. Recordings, the
  checks that compare the fake with Principle, and investigations of data all read production.
- A write is never sent to production to learn how Principle answers it. It goes to staging,
  only behind an explicit flag, as `--create-patient` does for `createPatient`. Reading staging
  to see what that write did is part of the write, behind the same flag.
- Data read from production follows AGENTS.md, "Keep patient data out of this repository".
