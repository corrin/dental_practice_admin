"""Set one staging patient's address through the official Principle API.

    uv run python -m scripts.address_via_api PATIENT_ID "1 Example St, Palmerston North 4410"

PATCH requires practiceId, name, dateOfBirth, gender and email even when only the address
changes; omitted contactNumbers are kept. The API accepts any address string: the
verification the web form applies is not enforced here.
"""

from __future__ import annotations

import argparse

import httpx
from dotenv import dotenv_values

from dental_practice_admin.config import STAGING_API_URL

REQUIRED = ("name", "dateOfBirth", "gender", "email")


def main() -> int:
    """Parse arguments, act on staging, and print the before and after values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patient_id")
    parser.add_argument("address")
    args = parser.parse_args()
    env = dotenv_values(".env")
    with httpx.Client(
        base_url=STAGING_API_URL,
        headers={"X-API-Key": env["PRINCIPLE_API_KEY_STAGING"] or ""},
        timeout=60,
    ) as client:
        path = f"/v1/patients/{args.patient_id}"
        before = client.get(path).raise_for_status().json()
        print("before:", repr(before["address"]), before["updatedAt"])
        body = {key: before[key] for key in REQUIRED}
        body.update(practiceId=env["PRINCIPLE_PRACTICE_ID_STAGING"], address=args.address)
        client.patch(path, json=body).raise_for_status()
        after = client.get(path).raise_for_status().json()
        print("after: ", repr(after["address"]), after["updatedAt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
