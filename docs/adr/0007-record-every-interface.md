# 0007 — Record every interface

Every operation in the released Principle interface has a recording from Principle and a route in
the fake, and the fake answers from a snapshot of production.

## Rules

- The fake is a working Principle, not a replay: it holds state, and every answer, write and
  refusal is computed from that state. The state is loaded from a snapshot of production that
  the recorder takes, so the fake's patients, diary and messages are the practice's own.
- The snapshot and the recordings derive from patient records, so they live under
  `ADMIN_DATA_ROOT`, never in this checkout. The same recorder run writes an anonymised copy of
  both into `tests/snapshot/`, which is committed and names the day it was taken. GitHub's
  `hermetic` check runs the whole suite on the copy; the release gate runs it on both.
- The anonymised copy is deny-by-default: every value in it is invented, including the
  practice's own names for treatments and appointment types, and every id and timestamp is
  remapped. Only structure survives: relations, counts, and the values Principle's interface
  enumerates, such as statuses. Before writing the copy the recorder checks it against every
  value it read from production, snapshot and recordings alike, and refuses to write it if any
  appears other than an enumerated one. The commit hook's leak scan then checks the copy as it
  checks any staged file.
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
