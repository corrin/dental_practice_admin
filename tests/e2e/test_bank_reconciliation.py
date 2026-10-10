"""Staff fetch deposits from the bank and match one by hand, in the browser."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_staff_match_a_transfer_to_the_payment_already_in_principle(
    page: Page, spine: dict[str, str],
) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(spine["APP_URL"] + "/")
    page.get_by_test_id("reconcile-link").click()
    page.get_by_test_id("fetch-now").click()
    rows = page.get_by_test_id("deposit-row")
    expect(rows).to_have_count(3, timeout=60000)
    expect(page.get_by_test_id("fetch-problems")).to_have_count(0)

    rows.filter(has_text="FAKE PAYER SMITH").get_by_role("link", name="Find & Match").click()
    ok = page.get_by_test_id("match-ok")
    expect(ok).to_be_disabled()
    page.get_by_role("checkbox", name="Match Lily Fake").check()
    expect(ok).to_be_enabled()
    ok.click()

    expect(rows).to_have_count(2)
    page.get_by_role("link", name="Reconciled").click()
    expect(rows.filter(has_text="FAKE PAYER SMITH")).to_contain_text("Lily Fake")
    assert not errors
