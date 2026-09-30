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

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.fake.store import FAKE_API_KEY, FAKE_PRACTICE_ID

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parent.parent.parent

# The seeded diary's first day, in practice-local time.
DIARY_DATE = "2026-09-28"
EXPECTED_BOOKED = 8
EXPECTED_CANCELLED = 2

STARTUP_TIMEOUT = 45.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(target: str, port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _await_http(url: str, process: subprocess.Popen[bytes], expect_status: set[int]) -> None:
    """Wait for a server to answer, failing with its own output rather than a bare timeout."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last = "no response yet"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode(errors="replace") if process.stdout else ""
            pytest.fail(f"{url} server exited with {process.returncode}:\n{output}")
        try:
            response = httpx.get(url, timeout=3)
        except httpx.HTTPError as error:
            last = str(error)
        else:
            if response.status_code in expect_status:
                return
            last = f"status {response.status_code}"
        time.sleep(0.25)
    pytest.fail(f"{url} was not ready within {STARTUP_TIMEOUT}s: {last}")


@pytest.fixture(scope="module")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """The fake Principle and the web application, each in its own process."""
    data_root = tmp_path_factory.mktemp("e2e-data")
    fake_port = _free_port()
    app_port = _free_port()

    env = dict(os.environ)
    env.update(
        {
            "PRINCIPLE_ENVIRONMENT": "fake",
            "PRINCIPLE_API_BASE_URL": f"http://127.0.0.1:{fake_port}",
            "PRINCIPLE_API_KEY": FAKE_API_KEY,
            "PRINCIPLE_PRACTICE_ID": FAKE_PRACTICE_ID,
            "PRINCIPLE_DATA_ROOT": str(data_root),
            "PYTHONPATH": str(REPO),
        }
    )

    fake = _serve("tests.fake.server:app", fake_port, env)
    application = _serve("principle_admin.app:app", app_port, env)
    try:
        # The fake refuses an unauthenticated call, which is itself the readiness signal.
        _await_http(f"http://127.0.0.1:{fake_port}/v1/practices", fake, {401, 403, 500})
        _await_http(f"http://127.0.0.1:{app_port}/health", application, {200})
        yield {**env, "APP_URL": f"http://127.0.0.1:{app_port}"}
    finally:
        for process in (application, fake):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.fixture(scope="module")
def completed_run(spine: dict[str, str]) -> str:
    """Run the diary task as Task Scheduler will, over real HTTP to the fake."""
    finished = subprocess.run(
        [sys.executable, "-m", "principle_admin.tasks", "diary", "--date", DIARY_DATE],
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
