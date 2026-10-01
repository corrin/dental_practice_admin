# 0003 — Unhappy case first

Refusals, errors and empty results are handled first and leave the function, and the main path
follows unindented.

## Rules

- Open with guard clauses: `if not allowed: raise ...`, then the work.
- An `if` whose body neither returns nor raises needs an `else`: `if ok: do_thing()` alone is a
  bug. State what happens otherwise, even if it is `raise`, because the silent fall-through is the
  failure nobody notices for a week. A guard clause leaves the function, so it needs none.

## Observed

None yet.
