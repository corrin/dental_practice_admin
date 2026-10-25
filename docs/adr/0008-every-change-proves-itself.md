# 0008 — Every change proves itself

A pull request merges only when automated tests run every line it changes, fail when any of
those lines is broken, and run on Principle's real answers; machines check this, and an agent's
word is not evidence.

## Rules

- The `hermetic` suite runs every changed line in `src/`, `scripts/` and `tests/fake/`: coverage
  of the lines a pull request changes is 100%, measured against `main`. A new
  `pragma: no cover` fails the check.
- Every changed line is mutated (a condition flipped, a guard removed, a value changed), and a
  test fails for each mutation. This is [ADR 0004](0004-tests-state-behaviour.md)'s "delete the
  guard and watch the test fail", done by the check rather than trusted to whoever wrote it.
- Every Principle operation the code calls has a recording from Principle under the released
  interface, in the committed anonymised copy ([ADR 0007](0007-record-every-interface.md)), so
  code that calls Principle cannot merge without having been run against it.
- The checks, their thresholds and `.github/` belong to the owner through `CODEOWNERS`, so a pull
  request that weakens them waits for the owner instead of merging itself.
- Done means merged with `hermetic` green. A red pull request is never reported as done.

## Observed

- At `a499294`, `scripts/record_principle_wire.py --create-patient` gives the staging patient the
  owner's phone and email, and had never been run against staging. At `814fcf3`, ADR 0007
  describes an anonymiser that no code implements.
