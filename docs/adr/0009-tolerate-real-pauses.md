# 0009 — Tolerate real pauses

Every wait on an external system has a limit taken from that system's measured behaviour, and
every run has one deadline that all its waits share, so a realistic pause never fails and a
stuck one never blocks the queue.

## Rules

- Each operation's timeout is the p99.9 of its measured durations, padded by half again, and
  never under 60 seconds. Durations come from the recorder, which keeps how long each exchange
  took, and from the run history. Delays between operations are measured the same way, such as
  how long after `createPatient` the name search finds the patient. Every measurement is kept,
  not only the last.
- A run gets one absolute deadline when it starts, and every wait inside it, timeouts and
  retries included, gets the lesser of its own limit and the time remaining. A scheduled run's
  deadline is five minutes, the interval at which Windows launches the next one
  ([ADR 0005](0005-promote-working-scripts.md)). A run staff start, such as a chat turn or a
  report, has 60 seconds, raised for a task when its own run history shows it needs longer.
- A wait that reaches its limit fails with the system's name and how long it waited, in the chat
  or the run history, never silently. A retry happens only within the deadline, and only while
  the system is otherwise answering.
- Every fake runs at full speed by default, for rapid testing. Started with
  `SIMULATE_<SYSTEM>_SPEED=1` (for example `SIMULATE_PRINCIPLE_SPEED=1`), it pauses as the real
  system was measured pausing, using the slowest recorded measurement. Tests about pauses run
  with it set. The integration tier fails when a new measurement is slower than any limit or
  simulation derived from earlier ones, until they are derived again.

Sources: AWS Builders' Library, "Timeouts, retries and backoff with jitter" (timeouts from a
measured latency percentile); Google SRE book, "Addressing Cascading Failures" (one deadline per
request, propagated).
