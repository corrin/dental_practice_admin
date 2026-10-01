# tests/spec/

## fingerprint.json

A normalized interface snapshot: method, path, operation description, declared parameters and
response schemas. Examples and patient records are excluded. The snapshot drives both the
generated operation catalogue and chat tool schemas.

`uv run python -m scripts.refresh_spec --update` fetches, shows the structural diff, regenerates
and runs focused tests. `--generate` regenerates offline; `--check` checks local consistency;
`--staged` checks staged inputs in isolation; `--upstream-check` performs a read-only live check.
Interface changes reach production through a release.

**The specification itself is not committed here.** It carries no licence, no terms of service
and no contact, so redistribution rights are unstated, and it could not be sublicensed under
this project's AGPL-3.0 in any case. `scripts/refresh_spec.py` fetches it from
<https://api.principle.dental/assets/api.yml>. Local copies at `principle-api.yml` are gitignored.

## `info.version` is not a change signal

The published specification carries 631 more lines and four more paths (`/v1/invoices`,
`/v1/leads`, `/v1/transactions`, `/v1/practices/{practiceId}/call-events`) than the copy saved
in SMS_Bridge -- while both declare version **1.1.0**. Pinning or comparing the version would
have detected nothing. `tests/integration/test_spec_drift.py` compares content, scoped to the
operations in the released snapshot. Unrelated additions do not fail the live check.

## compatibility.json

Verified exceptions, coverage rules and interpretation notes belong here. Patches must match an
expected upstream value before generation applies them. An upstream change to that value fails
generation and requires review. Generated wrappers themselves are never edited.

## The specification is not the contract

Where the specification and the live API disagree, the live API wins and the fake follows it.
Five such disagreements are pinned in `tests/integration/test_pagination_contract.py`; the
README tabulates them.
