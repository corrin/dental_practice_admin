# tests/spec/principle-api.yml

Fetched from <https://api.principle.dental/assets/api.yml> on 2026-10-01.
Refresh with `uv run python scripts/refresh_spec.py`.

`info.version` is **not** a reliable change signal. This copy and the one saved in
SMS_Bridge both declare 1.1.0, but the published spec carries 631 more lines and four
paths the older copy lacks (`/v1/invoices`, `/v1/leads`, `/v1/transactions`,
`/v1/practices/{practiceId}/call-events`). The drift check therefore compares the
operations we call, not the version string.
