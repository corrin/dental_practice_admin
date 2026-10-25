# Recordings

Wire bodies captured from Principle, used as the oracle for the fake: they are what proves
the fake's computed answers carry the shapes Principle actually sends.

## Success bodies are not in this repository

The success bodies (`practices.json`, `patient.json`, `create_patient.json` and the rest) and
`firestore/` live under `ADMIN_DATA_ROOT`, in `production/recordings/`. They are derived from
real patient records, and no key-based anonymiser deserves the confidence needed to publish its
output as health information.

The anonymiser in `scripts/record_principle_wire.py` denies by default — an unrecognised field
is replaced and reported rather than passed through — and it is still not the basis on which
anyone should publish patient-derived data.

To get your own, with the `_PROD` settings in `.env` (ADR 0006: reads come from production):

```powershell
uv run python scripts/record_principle_wire.py
```

`--create-patient` also records `createPatient`, by creating a `[TEST] Fake` patient on staging,
which the API cannot delete, so it needs the `_STAGING` settings too and runs last. The
patient carries your own mobile and email, from `ADMIN_OWNER_MOBILE` (in international
form, such as `+6421...`) and `ADMIN_OWNER_EMAIL` in `.env`, so whatever Principle sends it reaches you.

## What *is* committed

`refusals/` — error bodies, which are Principle's own wording and contain no patient data.
`tests/fake/server.py` reads them so the fake can only refuse in words captured from the real
API. A refusal it authored itself would be a belief about Principle that every test then
asserted.

## They are the oracle for the fake

`tests/integration/test_fake_conformance.py` checks that every answer the fake gives has a
shape Principle was recorded giving: no field and no type the recording never showed. It is in
the integration tier because it needs these files, and it fails rather than skips when one is
missing.

Firestore documents are recorded as shapes only (`firestore/*.json`): field names and value
types, joined over several documents, with no values at all.

## Why they stay local

DocketWorks commits its recordings so that a failed disk cannot lose them. Here the risk runs
the other way: losing these costs one run of the recorder, while publishing
them would put patient-derived data on GitHub.

## What the anonymiser preserves, and what it does not

Preserved, because these are what a recording is read for:

- equality — the same patient in two rows stays the same patient
- chronological order — `createdAt` still descends the way Principle's does
- the `nextOffsetId == last row's createdAt` relationship the fake is built on
- wire format, including that Principle answers `+00:00` and never `Z`

Not preserved: durations. Synthetic instants are evenly spaced, so a 30-minute appointment and
a 60-minute one both render as one minute. Nothing reads a duration out of a recording, and
real gaps would leak the shape of a real day.
