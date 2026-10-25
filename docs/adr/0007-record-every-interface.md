# 0007 — Record every interface

Every operation in the released Principle interface has a recording from Principle and a route in
the fake, and the fake answers from a snapshot of production.

## Rules

- The fake is a working Principle, not a replay: it holds state, and every answer, write and
  refusal is computed from that state. The state is loaded from a snapshot of production that
  the recorder takes, so the fake's patients, diary and messages are the practice's own.
- The snapshot and the recordings derive from patient records, so they live under
  `ADMIN_DATA_ROOT` and never in this checkout or on GitHub. GitHub's `hermetic` check runs
  only the tests that need no patient records, and its logs, which are public, never show one.
  The tests that need the snapshot run in the pre-push hook, which refuses a push they fail,
  and in the release gate.
- A test states behaviour over whatever the snapshot holds ("every appointment in the diary is
  on the report"), never a value from it, so a new snapshot changes no test. A test that needs
  a case the snapshot lacks fails, naming the case. The recorder takes a new snapshot at each
  release gate and when run by hand.
- An operation without a fake route fails the default suite. One without a recording fails the
  integration tier in the release gate, so a new interface cannot be released without both.
- Reads are recorded from production and writes on staging ([ADR 0006](0006-read-from-production.md)).
  A write goes only to the staging patient the recorder creates, whose name carries `Fake` and
  whose phone and email are the owner's (`ADMIN_OWNER_MOBILE`, `ADMIN_OWNER_EMAIL`). The
  recorder reads that patient back before each write and refuses unless both still match, so
  no text, email or call from it reaches a real patient.
- The release gate re-records every read and the writes the practice makes, such as text
  messages. A write the practice does not make keeps its recording until the interface
  changes it.

## Observed

- At `d84add4` the fake served 8 of 57 operations, and `listSMSMessages` had no recording when
  Principle made `patientId` required on `SMSMessage`.
- At `d84add4` the fake's state is invented (`tests/fake/store.py`, `seed()`), so a test passes
  on data shaped as the author imagined rather than as the practice's records are.
