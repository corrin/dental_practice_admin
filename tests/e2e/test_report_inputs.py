"""Staff see a report's date as Today by default and pick its practitioner from Principle."""
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.test_report_inputs import install

pytestmark = pytest.mark.e2e


def test_the_date_defaults_to_today_and_a_schedule_keeps_it(page: Page,
                                                            spine: dict[str, str]) -> None:
    install(Path(spine["ADMIN_DATA_ROOT"]))
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(spine["APP_URL"])
    page.locator("#task").select_option(label="Day report")

    period, day = page.locator("#chosen-date"), page.get_by_label("Date (date)")
    expect(period).to_have_value("today")
    expect(day).to_have_value(date.today().isoformat())
    practitioner = page.locator("#chosen-practitioner")
    expect(practitioner).to_be_enabled()
    expect(practitioner).to_have_value("")
    expect(practitioner.locator("option")).to_have_text(
        ["All practitioners", "Dr Ada Whitwell", "Dr Bo Ferriter"])

    day.fill("2026-10-30")
    expect(period).to_have_value("")
    period.select_option("today")
    expect(day).to_have_value(date.today().isoformat())

    practitioner.select_option("Dr Bo Ferriter")
    page.get_by_text("Approval and automatic runs", exact=True).click()
    page.get_by_role("button", name="Save schedule").click()
    row = page.get_by_role("row").filter(has_text="Day report")
    row.get_by_role("button", name="Edit", exact=True).click()
    expect(period).to_have_value("today")
    expect(practitioner).to_have_value("Dr Bo Ferriter")
    row.get_by_role("button", name="Remove", exact=True).click()
    expect(row).to_have_count(0)
    assert not errors
