"""Staff can execute installed tasks and edit schedules through the actual browser UI."""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.test_task_lifecycle import install_fake

pytestmark = pytest.mark.e2e


def test_task_page_runs_and_manages_an_approved_local_task(page: Page,
                                                         spine: dict[str, str]) -> None:
    install_fake(Path(spine["ADMIN_DATA_ROOT"]))
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(spine["APP_URL"] + "/tasks/manage")
    page.locator("#inputs").fill('{"numbers":[2,5]}')
    page.get_by_role("button", name="Run now", exact=True).click()
    expect(page.get_by_test_id("run-summary")).to_have_text("Synthetic report", timeout=60000)
    expect(page.get_by_role("link", name="Download local execution audit")).to_be_visible()
    page.goto(spine["APP_URL"] + "/tasks/manage")
    page.locator("#inputs").fill('{"numbers":[2,5]}')
    page.locator("#at").fill("15:25")
    page.get_by_role("button", name="Save schedule").click()
    row = page.get_by_role("row").filter(has_text="fake_report /")
    expect(row.get_by_role("button", name="Edit", exact=True)).to_be_visible()
    row.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator("#at")).to_have_value("15:25")
    row.get_by_role("button", name="Pause", exact=True).click()
    expect(row.get_by_role("cell", name="Paused", exact=True)).to_be_visible()
    row.get_by_role("button", name="Remove", exact=True).click()
    expect(row).to_have_count(0)
    assert not errors
