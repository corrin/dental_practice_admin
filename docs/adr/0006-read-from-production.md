# 0006 — Read from production

Anything that establishes what Principle returns reads it from production; staging receives only
writes.

## Rules

- Staging is a copy of production taken on one day and then left. Its data can be stale or
  wrong, so a read from staging is not evidence of what Principle returns now. Recordings, the
  checks that compare the fake with Principle, and investigations of data all read production.
- A write is never sent to production to learn how Principle answers it. It goes to staging,
  only behind an explicit flag, as `--create-patient` does for `createPatient`.
- Reading production changes nothing else: recordings are still scrubbed before they are written
  and stay out of version control, and findings enter `docs/` as counts and shapes (AGENTS.md).

## Observed

- At `770c2eb`, `scripts/record_principle_wire.py` found no booked appointment in staging's last
  30 days and recorded nothing: staging's copy was more than a month old.
