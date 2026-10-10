"""Staging only: which fields an updatePatient PATCH changes, and whether it messages anyone.

    uv run python -m scripts.patient_write_probe PATIENT_ID '[{"address": "1 Example St"}]'

Applies each change in turn to one staging patient, prints every API field that changed, then
restores every field it changed. Use a dummy patient: the PATCH is
real. docs/principle/patient-writes.md records what it showed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx2 as httpx

from dental_practice_admin.config import Environment, Settings

REQUIRED = ("name", "dateOfBirth", "gender", "email")


async def probe(patient: str, changes: list[dict[str, Any]]) -> None:
    """Apply each change, print what changed, then restore every field it touched."""
    settings = Settings(environment=Environment.STAGING)
    path = f"/v1/patients/{patient}"
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=60, headers={
            "X-API-Key": settings.api_key.get_secret_value()}) as api:

        async def read() -> dict[str, Any]:
            return dict((await api.get(path)).raise_for_status().json())

        async def messages() -> tuple[int, int]:
            sms = (await api.get(path + "/sms-messages")).raise_for_status().json()
            mail = (await api.get(path + "/emails")).raise_for_status().json()
            return len(sms["data"]), len(mail["data"])

        async def patch(change: dict[str, Any]) -> bool:
            before = await read()
            body = {key: before[key] for key in REQUIRED if key in before}
            body.update(practiceId=settings.practice_id, **change)
            response = await api.patch(path, json=body)
            after = await read()
            changed = sorted(k for k in before.keys() | after.keys()
                             if before.get(k) != after.get(k))
            print(f"PATCH {sorted(change)}: {response.status_code} {response.text[:200]}"
                  if response.is_error else f"PATCH {sorted(change)}: changed {changed}")
            return not response.is_error

        print("messages before (sms, email):", await messages())
        original = await read()
        touched = {key for change in changes for key in change}
        try:
            for change in changes:
                await patch(change)
        finally:
            empty = {"contactNumbers": [], "address": ""}
            if not await patch({key: original.get(key, empty.get(key)) for key in touched}):
                raise SystemExit("Restore failed: the patient still holds the probe's changes")
        print("messages after (sms, email):", await messages())


def main() -> int:
    """Parse arguments and probe the given staging patient."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patient_id")
    parser.add_argument("changes", help="JSON list of field changes, applied in turn")
    args = parser.parse_args()
    asyncio.run(probe(args.patient_id, json.loads(args.changes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
