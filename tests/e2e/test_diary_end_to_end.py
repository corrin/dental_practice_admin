"""The whole spine, in separate processes, driven through a browser.

Nothing is stubbed in-process here. The fake Principle runs under its own Uvicorn on its own
port, the task runs as the command Windows Task Scheduler will invoke, the web application
runs under a second Uvicorn, and a real browser reads the page. What this proves that the
in-process tests cannot: the CLI entry point exists and exits correctly, configuration
arrives through the environment, the database is shared between two processes, real HTTP and
real TLS-less sockets carry the calls, and the page renders in a browser rather than in a
test client.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from playwright.sync_api import Page, expect

from tests.servers import DIARY_DATE, EXPECTED_BOOKED, EXPECTED_CANCELLED, REPO

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def completed_run(spine: dict[str, str]) -> str:
    """Run the diary task as Task Scheduler will, over real HTTP to the fake."""
    finished = subprocess.run(
        [sys.executable, "-m", "dental_practice_admin.tasks", "diary", "--date", DIARY_DATE],
        cwd=REPO,
        env=spine,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert finished.returncode == 0, (
        f"the scheduled command failed with {finished.returncode}\n"
        f"stdout:\n{finished.stdout}\nstderr:\n{finished.stderr}"
    )
    return finished.stdout


def test_the_scheduled_command_reports_the_day(completed_run: str) -> None:
    """The command Windows invokes must succeed and say what it found.

    Exit code and one summary line are all Task Scheduler and an operator can see; a task
    that logged nothing useful would be indistinguishable from one that did nothing.
    """
    assert "succeeded complete" in completed_run
    assert DIARY_DATE in completed_run
    assert f"of {EXPECTED_BOOKED} booked" in completed_run


def test_the_run_appears_on_the_page_with_the_right_numbers(
    page: Page, spine: dict[str, str], completed_run: str
) -> None:
    """A run written by one process must be readable by the other, in a browser.

    Two processes sharing one SQLite file is the arrangement most likely to work in tests and
    fail in service: this asserts the web process sees what the task wrote.
    """
    page.goto(spine["APP_URL"])
    expect(page.get_by_test_id("fake-banner")).to_be_visible()
    expect(page.get_by_test_id("task-name")).to_have_text("Daily diary")

    rows = page.get_by_test_id("run-row")
    expect(rows).to_have_count(1)
    expect(page.get_by_test_id("run-outcome")).to_have_text("succeeded")
    expect(page.get_by_test_id("run-coverage")).to_have_count(0)

    page.get_by_test_id("run-link").click()
    expect(page.get_by_test_id("run-title")).to_have_text("daily_diary")
    expect(page.get_by_test_id("not-complete-warning")).to_have_count(0)
    expect(page.get_by_test_id("run-principle")).to_have_text("fake")

    summary = page.get_by_test_id("run-summary").inner_text()
    assert f"of {EXPECTED_BOOKED} booked" in summary
    assert f"{EXPECTED_BOOKED - EXPECTED_CANCELLED} attending" in summary

    expect(page.get_by_test_id("practitioner-row")).to_have_count(2)
    status_table = page.get_by_test_id("status-table").inner_text()
    assert "cancelled" in status_table


def test_the_page_never_offers_a_next_run_time(page: Page, spine: dict[str, str]) -> None:
    """Windows owns the schedule, and the rendered page must not imply otherwise."""
    page.goto(spine["APP_URL"])
    assert "next run" not in page.content().lower()


def test_an_unknown_run_is_not_rendered_as_an_empty_report(
    page: Page, spine: dict[str, str]
) -> None:
    """A stale or mistyped link must not look like a day with nothing in it."""
    response = page.goto(f"{spine['APP_URL']}/runs/deadbeef")
    assert response is not None
    assert response.status == 404
