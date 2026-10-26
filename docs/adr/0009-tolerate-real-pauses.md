# 0009 — Tolerate real pauses

Every wait on an external system is limited to 60 seconds and every run to five minutes, so a
realistic pause never fails and a stuck one never blocks the queue.

## Rules

- A wait on an external system (a request, a retry, waiting for a read to see a write) gives up
  after 60 seconds.
- A run has one deadline, five minutes from its start, shared by every wait in it: the interval
  at which Windows launches the next scheduled run ([ADR 0005](0005-promote-working-scripts.md)),
  so no run holds up the queue.
- Reaching either limit fails with the system's name and how long it waited, in the chat or the
  run history, never silently.
- The recorder keeps how long each exchange took and how long a read took to see a write, every
  run. The integration tier fails when one reaches half its limit, while there is still room.
- Every fake runs at full speed by default. Started with `SIMULATE_<SYSTEM>_SPEED=1` (for
  example `SIMULATE_PRINCIPLE_SPEED=1`), it pauses as long as the slowest measurement recorded.
  Tests about pauses run with it set.

Sources: AWS Builders' Library, "Timeouts, retries and backoff with jitter"; Google SRE book,
"Addressing Cascading Failures" (one deadline per request, shared by everything it calls).
