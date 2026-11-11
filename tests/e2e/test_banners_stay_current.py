"""A page reception leaves open shows a problem that arose after it was loaded."""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from dental_practice_admin.schedules import POLL_SECONDS
from dental_practice_admin.storage import Coverage, Outcome, Storage
from tests.test_automation import settings

pytestmark = pytest.mark.e2e

# Milliseconds of page time in which an open page has fetched its banners once.
AFTER_ONE_CHECK = (POLL_SECONDS + 1) * 1000


def test_a_page_left_open_shows_a_new_problem_within_one_launcher_check_without_reloading(
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
        page.clock.fast_forward(AFTER_ONE_CHECK)
        expect(page.get_by_test_id("staff-attention")).to_contain_text(
            "Text check: 1 for staff to fix")
        expect(page.locator(f'#staff-banners a[href="/runs/{run_id}"]')).to_have_count(1)
        assert page.evaluate("window.leftOpen") is True
    finally:
        store.db.execute("DELETE FROM task_runs WHERE run_id=?", (run_id,))
        store.close()


def test_an_answer_without_banners_counts_as_lost_touch_and_later_checks_still_run(
    page: Page, spine: dict[str, str],
) -> None:
    page.clock.install()
    page.goto(f"{spine['APP_URL']}/history")
    page.route("**/banners", lambda route: route.fulfill(status=200, body="<p>proxy page</p>"))
    page.clock.fast_forward(AFTER_ONE_CHECK)
    expect(page.get_by_test_id("banners-stale")).to_be_visible()
    page.unroute("**/banners")
    page.clock.fast_forward(AFTER_ONE_CHECK)
    expect(page.get_by_test_id("banners-stale")).to_have_count(0)


def test_an_unchanged_banner_is_left_as_it_is(page: Page, spine: dict[str, str]) -> None:
    page.clock.install()
    page.goto(f"{spine['APP_URL']}/history")
    """An open "Technical details" stays open: nothing inside an unchanged banner is replaced."""
    page.evaluate("document.getElementById('staff-banners')"
                  ".insertAdjacentHTML('beforeend', '<span data-left=\"yes\"></span>')")
    page.clock.fast_forward(AFTER_ONE_CHECK)
    page.clock.fast_forward(AFTER_ONE_CHECK)
    expect(page.locator("#staff-banners [data-left=yes]")).to_have_count(1)


def test_a_page_that_cannot_reach_the_app_says_so_until_it_can_again(
    page: Page, spine: dict[str, str],
) -> None:
    page.clock.install()
    page.goto(f"{spine['APP_URL']}/history")
    page.route("**/banners", lambda route: route.abort())
    page.clock.fast_forward(AFTER_ONE_CHECK)
    expect(page.get_by_test_id("banners-stale")).to_be_visible()
    page.unroute("**/banners")
    page.clock.fast_forward(AFTER_ONE_CHECK)
    expect(page.get_by_test_id("banners-stale")).to_have_count(0)
