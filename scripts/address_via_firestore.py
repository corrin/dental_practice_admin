"""Read or set staging patient addresses directly in Principle's Firestore.

    uv run python -m scripts.address_via_firestore --changed-since 2026-10-01T00:00:00+13:00
    uv run python -m scripts.address_via_firestore --set PATIENT_ID "1 Example St, ..."

A staff login's Firebase token may query the whole patients collection and write to it.
A direct write bypasses Principle: updatedAt and updatedBy are not stamped, so the change
is invisible to an updatedAt query and to the record's audit fields, and no address
verification runs. The API reads the same document, so it reports the new value at once.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import httpx
from dotenv import dotenv_values
from scripts.check_staging import DOCUMENT_PREFIX, staging_browser

FIRESTORE = "https://firestore.googleapis.com/v1/"
# The "Massey Smiles Dental" staging workspace. Another organisation and brand on the
# same staging login also hold patients; these are the ones the staging API key reads.
PATIENTS_PARENT = (
    DOCUMENT_PREFIX + "organisations/odkoF1t86uClJUE1HA63/brands/dwVtF9Q9yAPhkQ4EulEC"
)


def changed_since(headers: dict[str, str], since: datetime) -> list[tuple[str, str]]:
    """Patient ids and updatedAt for every patient updated at or after `since`."""
    query = {"structuredQuery": {
        "from": [{"collectionId": "patients"}],
        "where": {"fieldFilter": {
            "field": {"fieldPath": "updatedAt"},
            "op": "GREATER_THAN_OR_EQUAL",
            "value": {"timestampValue": since.isoformat()},
        }},
        "select": {"fields": [{"fieldPath": "updatedAt"}]},
    }}
    response = httpx.post(
        f"{FIRESTORE}{PATIENTS_PARENT}:runQuery", json=query, headers=headers, timeout=60
    ).raise_for_status()
    return [
        (row["document"]["name"].rsplit("/", 1)[1],
         row["document"]["fields"]["updatedAt"]["timestampValue"])
        for row in response.json() if "document" in row
    ]


def set_address(headers: dict[str, str], patient_id: str, address: str) -> None:
    """Replace only the address field, refusing if the document changed since it was read."""
    url = f"{FIRESTORE}{PATIENTS_PARENT}/patients/{patient_id}"
    before = httpx.get(url, headers=headers, timeout=30).raise_for_status().json()
    print("before:", repr(before["fields"]["address"]["stringValue"]))
    httpx.patch(
        url,
        params={
            "updateMask.fieldPaths": "address",
            "currentDocument.updateTime": before["updateTime"],
        },
        json={"fields": {"address": {"stringValue": address}}},
        headers=headers,
        timeout=30,
    ).raise_for_status()
    after = httpx.get(url, headers=headers, timeout=30).raise_for_status().json()
    print("after: ", repr(after["fields"]["address"]["stringValue"]))


def main() -> int:
    """Parse arguments, act on staging, and print the before and after values."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--changed-since", type=datetime.fromisoformat)
    group.add_argument("--set", nargs=2, metavar=("PATIENT_ID", "ADDRESS"))
    parser.add_argument("--ui-env-file", default="../od_data/.env")
    args = parser.parse_args()
    env = dotenv_values(args.ui_env_file)
    with staging_browser() as session:
        session.login(env["PRINCIPLE_UI_EMAIL"] or "", env["PRINCIPLE_UI_PASSWORD"] or "")
        headers = {"Authorization": "Bearer " + session.token}
        if args.changed_since:
            rows = changed_since(headers, args.changed_since)
            for patient_id, updated in sorted(rows, key=lambda row: row[1]):
                print(patient_id, updated)
            print(len(rows), "patients")
        else:
            set_address(headers, *args.set)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
