# 0008 — Every change proves itself

A pull request merges only when its automated tests show, on the practice's own data, that it
does what the practice needs and that no bug which has tripped us before has come back.

## Rules

- The tests state the business requirement the change is for, as scenarios in the practice's
  terms ("a text sent to a patient is in that patient's messages, with the patient's number"),
  written before the code and failing until it exists. They assert what staff, patients or
  Principle see, never which lines ran.
- Every bug found gets a test of the behaviour it broke, written to fail before the fix, and it
  stays in the suite.
- The tests run on the anonymised copy of production and Principle's recordings
  ([ADR 0007](0007-record-every-interface.md)), so they meet the data the practice really has.
- `hermetic` runs a pull request's new and changed tests against `main`'s code as well as its
  own, and fails unless at least one of them fails on `main`. A test that passes before the
  change was made tests nothing the change did.
