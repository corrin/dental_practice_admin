# 0007 — Record every interface

Every operation of every external system the application uses (Principle's API, Firestore and
Firebase, Akahu, Google, OpenAI, GitHub) has a recording of exactly what that system sent and a
route in its fake, and the fakes answer from a snapshot of production.

## Rules

- A recording is what the external system sent, bit for bit: the status, the headers and the
  body bytes as they came off the wire, captured at the transport before anything parses them.
  Nothing is redacted, remapped or reformatted, so a check against it is a check against the
  system itself. Only the anonymised copy committed to GitHub is derived from recordings.

- The fake is a working Principle, not a replay: it holds state, and every answer, write and
  refusal is computed from that state. The state is loaded from a snapshot of production that
  the recorder takes, so the fake's patients, diary and messages are the practice's own.
- The snapshot and the recordings derive from patient records, so they live under
  `ADMIN_DATA_ROOT`, never in this checkout. The same recorder run writes an anonymised copy of
  both into `tests/snapshot/`, which is committed and names the day it was taken. GitHub's
  `hermetic` check runs the whole suite on the copy; the release gate runs it on both.
- The anonymised copy represents production exactly except for names and numbers. Every time,
  date, duration, status, amount, treatment and appointment type is kept as Principle sent it.
  Replaced, the same way wherever it appears: names of people and the practice, phone numbers,
  emails, addresses, dates of birth, ids, and the words of free text (notes, message bodies),
  which can hold any name or number; free text keeps its length and line breaks. Accuracy comes
  before privacy for anything else: a value is replaced only when the owner agrees it could
  identify a patient. Before writing the copy the recorder checks every replaced value: if any
  original still appears anywhere in the copy, it refuses to write. The commit hook's leak scan
  then checks the copy as it checks any staged file.
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
- Recordings and the snapshot are taken when someone runs the recorder, never automatically. It
  records the operations it is named, or every one when none is named; writes only with
  `--write`. Each recording carries the day it was taken, and `--older-than N` (60 by default)
  lists those older than `N` days and fails if there are any. The release gate runs that check.
