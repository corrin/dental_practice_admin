"""Staging only: the address the website's verified-address picker saves.

    uv run python -m scripts.address_picker_probe PATIENT_ID "575 Don Buck Road, Massey"

Types the address into a staging patient's edit dialog, picks the first suggestion, saves, and
prints the address the API then holds and the names of any other fields the save changed,
before restoring the original address through the API. Type a business address, not a person's.
Use a dummy patient: the save is real and resubmits the whole profile. docs/principle/patient-
writes.md records what it showed.
"""
from __future__ import annotations

import argparse
import re

import httpx2 as httpx
from scripts.check_staging import UI_URL, staging_browser

from dental_practice_admin.config import Environment, Settings

WORKSPACE = re.compile(r"^\s*Massey Smiles Dental\s*massey-smiles-dental\s*$")
REQUIRED = ("name", "dateOfBirth", "gender", "email")


def main() -> int:
    """Save one picked address through the website, print it, and restore the original."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patient_id")
    parser.add_argument("address", help="what to type; the first suggestion is picked")
    args = parser.parse_args()
    settings = Settings(environment=Environment.STAGING)
    path = f"/v1/patients/{args.patient_id}"
    with httpx.Client(base_url=settings.api_base_url, timeout=60, headers={
            "X-API-Key": settings.api_key.get_secret_value()}) as api:
        original = api.get(path).raise_for_status().json()
        try:
            with staging_browser() as session:
                page = session.page
                target = f"/{settings.workspace_slug}/patients/{args.patient_id}"
                session.login(settings.ui_email, settings.ui_password.get_secret_value(),
                              target)
                page.get_by_placeholder("Search Workspaces").press_sequentially("Massey")
                page.get_by_role("option", name=WORKSPACE).click()
                page.wait_for_url(lambda url: "/login" not in url, timeout=60000)
                page.goto(UI_URL + target)
                page.locator("pr-patient-address").wait_for(timeout=60000)
                page.get_by_role("button", name="Edit").click()
                dialog = page.locator("pr-update-patient")
                field = dialog.get_by_placeholder("Enter address")
                field.click()
                field.fill("")
                field.press_sequentially(args.address, delay=80)
                # Suggestions come from Google Places Autocomplete, outside the dialog.
                page.locator(".pac-item").first.click(timeout=30000)
                page.wait_for_timeout(1500)
                page.locator('button:has-text("Update Patient")').click()
                dialog.wait_for(state="detached", timeout=30000)
            saved = api.get(path).raise_for_status().json()
            print("saved:", repr(saved["address"]))
            print("other fields the save changed:", sorted(
                k for k in original.keys() | saved.keys()
                if k not in ("address", "updatedAt") and original.get(k) != saved.get(k)))
        finally:
            current = api.get(path).raise_for_status().json()
            body = {key: current[key] for key in REQUIRED if key in current}
            body.update(practiceId=settings.practice_id, address=original.get("address", ""))
            api.patch(path, json=body).raise_for_status()
            if api.get(path).raise_for_status().json().get("address") != original.get(
                    "address"):
                raise SystemExit("Restore failed: the patient still holds the picked address")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
