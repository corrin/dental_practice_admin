# Recordings

Wire bodies captured from a real Principle tenant, used as the oracle for the fake: they are
what proves the fake's computed answers carry the shapes Principle actually sends.

## Success bodies are not in this repository

`practices.json`, `practitioners.json` and `appointments.json` are gitignored. They are derived
from real patient records in a staging tenant that holds a migrated copy of a live practice,
and no key-based anonymiser deserves the confidence needed to publish its output as health
information.

The anonymiser in `scripts/record_principle_wire.py` denies by default — an unrecognised field
is replaced and reported rather than passed through — and it is still not the basis on which
anyone should publish patient-derived data.

To get your own:

```powershell
$env:PRINCIPLE_API_KEY_STAGING = '...'
$env:PRINCIPLE_PRACTICE_ID_STAGING = '...'
uv run python scripts/record_principle_wire.py
```

They land here, stay here, and are yours.

## What *is* committed

`refusals/` — error bodies, which are Principle's own wording and contain no patient data.
`tests/fake/server.py` reads them so the fake can only refuse in words captured from the real
API. A refusal it authored itself would be a belief about Principle that every test then
asserted.

## Nothing in the automated suite depends on the success bodies

Shape is verified where it can be verified honestly: `tests/integration/` asserts it against
the live staging API. Comparing against a stored file would only prove the file had not
changed. The recordings' remaining job is to be read by a person adding a route to the fake.

## What the anonymiser preserves, and what it does not

Preserved, because these are what a recording is read for:

- equality — the same patient in two rows stays the same patient
- chronological order — `createdAt` still descends the way Principle's does
- the `nextOffsetId == last row's createdAt` relationship the fake is built on
- wire format, including that Principle answers `+00:00` and never `Z`

Not preserved: durations. Synthetic instants are evenly spaced, so a 30-minute appointment and
a 60-minute one both render as one minute. Nothing reads a duration out of a recording, and
real gaps would leak the shape of a real day.
