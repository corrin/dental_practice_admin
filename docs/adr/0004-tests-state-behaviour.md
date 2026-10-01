# 0004 — Tests state behaviour

A test asserts what the application does, so it stays valid when how it does it is rewritten.

## Rules

- Assert outcomes: the status code, the type of error raised, the rows stored, what the page
  shows. Do not assert error wording, private helpers or call order, because they change in
  rewrites that change no behaviour.
- Prove a guard's test by deleting the guard and watching the test fail. A test that still passes
  is testing something else.

## Observed

- Three guard tests matched the message "Production requires Google" and failed when it was
  reworded, with behaviour unchanged (fixed in `819cd6f`).
