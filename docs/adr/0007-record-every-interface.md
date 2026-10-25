# 0007 — Record every interface

Every operation in the released Principle interface has a recording from Principle and a route in
the fake that answers from the fake's own state.

## Rules

- An operation without a recording or a fake route fails the default suite, so updating the
  interface (`scripts/refresh_spec.py --update`) brings the recording and the route with it.
- Reads are recorded from production and writes on staging ([ADR 0006](0006-read-from-production.md)).
- The release gate re-records every read, and the writes the practice makes, such as text
  messages, so what the practice relies on is checked against Principle as it is now. A write
  the practice does not make keeps its recording until the interface changes it.
- The fake computes every answer from its state. A recording checks the shape of the fake's
  answer and never supplies it (README, "Why the fake is built the way it is").

## Observed

- At `d84add4` the fake served 8 of 57 operations, and `listSMSMessages` had no recording when
  Principle made `patientId` required on `SMSMessage`.
