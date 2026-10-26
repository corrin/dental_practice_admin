# 0009 — Tolerate real pauses

Every single wait on an external system gives up after 60 seconds, so a realistic pause never
fails and a stuck system fails within a minute; a run that is still getting answers is never
stopped for taking long.

## Rules

- A wait on an external system (a request, a retry, waiting for a read to see a write) gives up
  after 60 seconds, and fails with the system's name and how long it waited, in the chat or the
  run history, never silently.
- A long run, such as reconciliation's first sync, runs to the end while each of its waits is
  answered. A long run does not hold up others: the scheduler starts each due run whether or
  not another is still going.
- The recorder keeps how long each exchange took and how long a read took to see a write, every
  run. The integration tier fails when one reaches half the limit, while there is still room.
- Every fake runs at full speed by default. Started with `SIMULATE_<SYSTEM>_SPEED=1` (for
  example `SIMULATE_PRINCIPLE_SPEED=1`), it pauses as long as the slowest measurement recorded.
  Tests about pauses run with it set.

Sources: AWS Builders' Library, "Timeouts, retries and backoff with jitter" (a timeout on each
call); Google SRE book, "Addressing Cascading Failures" (a deadline belongs to a request someone
is waiting on).
