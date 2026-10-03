"""Installed source runs in a separate process and its results reach the staff browser."""
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from dental_practice_admin import schedules
from dental_practice_admin.storage import Storage
from tests.servers import REPO
from tests.test_automation import settings
from tests.test_task_lifecycle import REVISION, install_fake

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def completed_run(spine: dict[str, str]) -> str:
    configured = settings(Path(spine["ADMIN_DATA_ROOT"]))
    install_fake(Path(spine["ADMIN_DATA_ROOT"]), "scheduled_report")
    folder = configured.data_dir / "installed" / "scheduled_report" / REVISION
    source = """async def run(services, inputs):
    rows = [row async for row in services.api.rows('listPractitioners')]
    return {'summary': f'{len(rows)} practitioners', 'coverage': 'complete',
            'detail': {'names': [row['name'] for row in rows]}}
"""
    (folder / "source.txt").write_text(
        source, encoding="utf-8")
    schedule = schedules.Schedule(name="scheduled_report", revision=REVISION,
                                  inputs={"numbers": []})
    schedules.save(configured, schedule, "fake-staff")
    with schedules.open_schedules(configured) as scheduler:
        scheduler.get_job(schedule.id).modify(
            next_run_time=datetime.now(UTC) - timedelta(seconds=1))
    finished = subprocess.run([sys.executable, "-m", "dental_practice_admin.schedules"],
        cwd=REPO, env=spine, capture_output=True, text=True, timeout=120)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    store = Storage(configured.database_path)
    try:
        result = next(run for run in store.recent_runs() if run.initiator == "scheduler")
        assert result.is_trustworthy
        assert result.summary == "2 practitioners"
        return result.run_id
    finally:
        store.close()


def test_the_launcher_records_a_local_audit(spine: dict[str, str], completed_run: str) -> None:
    configured = settings(Path(spine["ADMIN_DATA_ROOT"]))
    assert (configured.data_dir / "audits" / f"{completed_run}.jsonl").is_file()


def test_the_run_appears_on_the_page_with_its_generic_results(
    page: Page, spine: dict[str, str], completed_run: str,
) -> None:
    page.goto(spine["APP_URL"])
    expect(page.get_by_test_id("fake-banner")).to_be_visible()
    page.locator(f'a[href="/runs/{completed_run}"]').click()
    expect(page.get_by_test_id("run-title")).to_have_text("scheduled_report")
    expect(page.get_by_test_id("not-complete-warning")).to_have_count(0)
    expect(page.get_by_test_id("run-summary")).to_have_text("2 practitioners")
    expect(page.locator("pre")).to_contain_text("Dr ")


def test_results_link_to_application_schedules(page: Page, spine: dict[str, str]) -> None:
    page.goto(spine["APP_URL"])
    page.get_by_role("link", name="Manage task files, reviews and schedules").click()
    expect(page.get_by_role("columnheader", name="Next due")).to_be_visible()


def test_an_unknown_run_is_not_rendered_as_an_empty_report(
    page: Page, spine: dict[str, str],
) -> None:
    response = page.goto(f"{spine['APP_URL']}/runs/deadbeef")
    assert response is not None and response.status == 404
