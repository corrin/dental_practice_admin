# 0007 — Record every interface

Every operation in the released Principle interface has a recording from Principle and a route in
the fake, and the fake answers from a snapshot of production.

## Rules

- The fake is a working Principle, not a replay: it holds state, and every answer, write and
  refusal is computed from that state. The state is loaded from a snapshot of production that
  the recorder takes, so the fake's patients, diary and messages are the practice's own.
- The snapshot and the recordings derive from patient records, so they live under
  `ADMIN_DATA_ROOT`, never in this checkout. The recorder also writes an anonymised copy of the
  snapshot into `tests/snapshot/`, which is committed, and GitHub's `hermetic` check runs the
  whole suite on it. The release gate runs the suite on both.
- The anonymised copy is deny-by-default. Every name, phone, email, address, note and message
  text is invented, and every id and timestamp is remapped. Only relations, counts, statuses
  and Principle's own catalogue values (treatment and appointment-type names) survive. The leak
  scan refuses any value the recorder read from production.
- A test states behaviour over whatever the snapshot holds ("every appointment in the diary is
  on the report"), never a value from it, so a new snapshot changes no test. A test that needs
  a case the snapshot lacks fails, naming the case.
- An operation without a fake route, or without a recording, fails the suite, so a new
  interface cannot be merged without both.
- Reads are recorded from production and writes on staging ([ADR 0006](0006-read-from-production.md)).
  A write goes only to the staging patient the recorder creates, whose name carries `Fake` and
  whose phone and email are the owner's, read from `ADMIN_OWNER_MOBILE` and `ADMIN_OWNER_EMAIL`
  in `.env`. The recorder reads that patient back before each write and refuses unless both
  still match, so no text, email or call from it reaches a real patient.
- The release gate re-records every read and the writes the practice makes, such as text
  messages, and takes a new snapshot. A write the practice does not make keeps its recording
  until the interface changes it.

## Observed

- At `d84add4` the fake served 8 of 57 operations, and `listSMSMessages` had no recording when
  Principle made `patientId` required on `SMSMessage`.
- At `d84add4` the fake's state is invented (`tests/fake/store.py`, `seed()`), so a test passes
  on data shaped as the author imagined rather than as the practice's records are.
