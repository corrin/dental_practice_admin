# tests/spec/

## fingerprint.json

A derived description of the Principle operations this project calls: method, path, declared
parameters and response shape, for the three entries in `dental_practice_admin.principle.CATALOGUE`.
Regenerate with `uv run python scripts/refresh_spec.py`.

**The specification itself is not committed here.** It carries no licence, no terms of service
and no contact, so redistribution rights are unstated, and it could not be sublicensed under
this project's AGPL-3.0 in any case. `scripts/refresh_spec.py` fetches it from
<https://api.principle.dental/assets/api.yml> to `principle-api.yml`, which is gitignored.

## `info.version` is not a change signal

The published specification carries 631 more lines and four more paths (`/v1/invoices`,
`/v1/leads`, `/v1/transactions`, `/v1/practices/{practiceId}/call-events`) than the copy saved
in SMS_Bridge -- while both declare version **1.1.0**. Pinning or comparing the version would
have detected nothing. `tests/integration/test_spec_drift.py` compares content, scoped to the
operations we call.

## The specification is not the contract

Where the specification and the live API disagree, the live API wins and the fake follows it.
Five such disagreements are pinned in `tests/integration/test_pagination_contract.py`; the
README tabulates them.
