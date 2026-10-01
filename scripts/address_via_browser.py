"""Set one staging patient's address through the Principle web UI, as staff would.

    uv run python -m scripts.address_via_browser PATIENT_ID "1 Example St" "Palmerston North" 4410

The edit form verifies addresses: free text is rejected until an autocomplete suggestion
is picked or "Manually Enter Address" is used. Manual entry has structured fields, but
Principle stores only the joined string, e.g. "1 Example St, Palmerston North, 4410,
New Zealand". Saving submits the whole form, so other fields can be rewritten too.

Navigation gotchas: the staging login offers several "Massey Smiles" workspaces, so the
option must match exactly; patient URLs use the workspace slug, and a wrong slug leaves
the app on its splash screen indefinitely; elements have no test ids, so selectors rely on
formcontrolname, placeholders and visible text.
"""

from __future__ import annotations

import argparse
import re
from urllib.parse import quote

from dotenv import dotenv_values
from scripts.check_staging import UI_URL, staging_browser

WORKSPACE = re.compile(r"^\s*Massey Smiles Dental\s*massey-smiles-dental\s*$")
SLUG = "massey-smiles"


def main() -> int:
    """Parse arguments, act on staging, and print the before and after values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patient_id")
    parser.add_argument("street")
    parser.add_argument("city")
    parser.add_argument("postcode")
    parser.add_argument("--ui-env-file", default="../od_data/.env")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    env = dotenv_values(args.ui_env_file)
    with staging_browser(args.headed) as session:
        page = session.page
        target = f"/{SLUG}/patients/{args.patient_id}"
        page.goto(f"{UI_URL}/login?redirectTo={quote(target, safe='')}")
        page.get_by_placeholder("Email", exact=True).fill(env["PRINCIPLE_UI_EMAIL"] or "")
        password = page.get_by_placeholder("Password", exact=True)
        password.fill(env["PRINCIPLE_UI_PASSWORD"] or "")
        password.press("Enter")
        page.get_by_placeholder("Search Workspaces").press_sequentially("Massey")
        page.get_by_role("option", name=WORKSPACE).click()
        page.wait_for_url(lambda url: "/login" not in url, timeout=60000)
        page.goto(UI_URL + target)
        page.locator("pr-patient-address").wait_for(timeout=60000)
        print("before:", page.locator("pr-patient-address").inner_text().strip())

        page.get_by_role("button", name="Edit").click()
        page.get_by_text("Manually Enter Address").click()
        form = page.locator("pr-address-input")
        form.locator("[formcontrolname=streetName]").fill(args.street)
        form.locator("[formcontrolname=subpremise]").fill("")
        form.locator("[formcontrolname=city]").fill(args.city)
        form.locator("[formcontrolname=state]").fill("")
        form.locator("[formcontrolname=postalCode]").fill(args.postcode)
        form.locator("[formcontrolname=country]").fill("New Zealand")
        page.get_by_role("option", name="New Zealand", exact=True).click()
        page.get_by_role("button", name="Update Patient").click()
        page.locator("pr-update-patient").wait_for(state="detached", timeout=30000)
        page.get_by_text(args.street).wait_for(timeout=30000)
        print("after: ", page.locator("pr-patient-address").inner_text().strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
