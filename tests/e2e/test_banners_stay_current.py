"""A page reception leaves open shows a problem that arose after it was loaded."""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from dental_practice_admin.storage import Coverage, Outcome, Storage
from tests.test_automation import settings

pytestmark = pytest.mark.e2e


def test_a_page_left_open_shows_a_new_problem_within_five_minutes_without_reloading(
    page: Page, spine: dict[str, str],
) -> None:
    page.clock.install()
    page.goto(f"{spine['APP_URL']}/history")
    expect(page.get_by_test_id("staff-attention")).to_have_count(0)
    page.evaluate("window.leftOpen = true")

    store = Storage(settings(Path(spine["ADMIN_DATA_ROOT"])).database_path)
    run_id = store.start_run("text_check", "scheduler", "fake")
    try:
        store.finish_run(run_id, Outcome.SUCCEEDED, Coverage.COMPLETE, "1 text did not go",
                         {"for_staff": [{"patient": "Fake Patient", "failed": "today"}]})
        page.clock.fast_forward("05:01")
        expect(page.get_by_test_id("staff-attention")).to_contain_text(
            "Text check: 1 for staff to fix")
        expect(page.locator(f'#staff-banners a[href="/runs/{run_id}"]')).to_have_count(1)
        assert page.evaluate("window.leftOpen") is True
    finally:
        store.db.execute("DELETE FROM task_runs WHERE run_id=?", (run_id,))
        store.close()


def test_a_page_that_cannot_reach_the_app_says_so(page: Page, spine: dict[str, str]) -> None:
    page.clock.install()
    page.goto(f"{spine['APP_URL']}/history")
    page.route("**/banners", lambda route: route.abort())
    page.clock.fast_forward("05:01")
    expect(page.get_by_test_id("banners-stale")).to_be_visible()
