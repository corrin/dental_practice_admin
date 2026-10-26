# 0009 — Tolerate real pauses

The application never refuses or fails because an external system pauses as it really does; it
gives up on a wait after one minute, and says so.

## Rules

- Every wait, timeout and retry limit on an external system is one minute: well past the pauses
  measured, and about as long as staff will watch a screen before they want an answer. A wait
  that reaches it fails with the system's name and how long it waited, in the chat or the run
  history, never silently.
- The recorder keeps how long each exchange took, and measures the delays the application can
  meet, such as how long after a write a read sees it. It keeps every measurement, not only the
  last. The integration tier fails when one reaches half the limit, while there is still room.
- Every fake runs at full speed by default, for rapid testing. Started with
  `SIMULATE_<SYSTEM>_SPEED` set (for example `SIMULATE_PRINCIPLE_SPEED`), it pauses as the real
  system was measured pausing, at the slowest measured, and the integration tier fails when a
  measurement is slower than the fake simulates. Tests about pauses run with it set.
