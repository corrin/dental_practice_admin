"""Boundaries and independently checked outcomes for the manual browser experiment."""

from typing import cast
from unittest.mock import MagicMock

import pytest
from scripts.check_browser_address import (
    EXCLUDED,
    PATIENT,
    BrowserTest,
    check_location,
    guard_navigation,
)
from scripts.check_staging import UI_URL

PATIENT_ID = "abcdefghijklmnopqrst"


def browser_test() -> BrowserTest:
    """Represent an identified dummy's visible address form."""
    page = MagicMock()
    page.url = UI_URL + "/massey-smiles/patients/" + PATIENT_ID
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.is_visible.return_value = True
    page.locator.return_value.input_value.return_value = "Original street"
    page.locator.return_value.inner_text.return_value = PATIENT + "\n28th Jan, 1980"
    return BrowserTest(page)


def action(kind: str, selector: str = "#address", text: str = "") -> dict[str, str]:
    return {"action": kind, "selector": selector, "text": text, "patient_id": PATIENT_ID}


@pytest.mark.parametrize("url", [
    "https://app.principle.dental/patients/example",
    "http://staging.principle.dental/patients/example",
    "https://staging.principle.dental.evil.invalid/patients/example",
    *[UI_URL + "/patients/" + record for record in EXCLUDED],
])
def test_unsafe_locations_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        check_location(url)


def test_patient_lock_rejects_another_record() -> None:
    with pytest.raises(ValueError):
        check_location(UI_URL + "/patients/other", PATIENT_ID)


def test_authentication_iframe_can_load_without_allowing_external_main_navigation() -> None:
    route = MagicMock()
    route.request.is_navigation_request.return_value = True
    route.request.url = "https://principle-staging.firebaseapp.com/__/auth/iframe"
    guard_navigation(route)
    route.continue_.assert_called_once()
    route.abort.assert_not_called()
    route.reset_mock()
    route.request.frame.page.main_frame = route.request.frame
    guard_navigation(route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


def test_an_unrelated_external_iframe_is_refused() -> None:
    route = MagicMock()
    route.request.is_navigation_request.return_value = True
    route.request.url = "https://unrelated.invalid/"
    guard_navigation(route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


@pytest.mark.parametrize("identity", [
    "Crash Test Dummy\n28th Jan, 1980", "Blocking Dummy\n22nd Jan, 1992",
])
def test_wrong_identity_cannot_be_captured(identity: str) -> None:
    browser = browser_test()
    cast(MagicMock, browser.page).locator.return_value.inner_text.return_value = identity
    with pytest.raises(ValueError):
        browser.execute(action("capture"))
    assert browser.original is None


def test_capture_reads_the_actual_input_and_preserves_empty_addresses() -> None:
    browser = browser_test()
    cast(MagicMock, browser.page).locator.return_value.input_value.return_value = ""
    browser.execute(action("capture"))
    assert browser.original == ""
    assert browser.patient_id == PATIENT_ID


def test_capture_refuses_a_matching_name_in_another_workspace() -> None:
    browser = browser_test()
    cast(MagicMock, browser.page).url = UI_URL + "/principle-platform/patients/" + PATIENT_ID
    with pytest.raises(ValueError):
        browser.execute(action("capture"))
    assert browser.original is None


def test_navigation_cannot_switch_to_another_workspace() -> None:
    browser = browser_test()
    with pytest.raises(ValueError):
        browser.execute(action("navigate", text=UI_URL + "/principle-platform/patients"))
    cast(MagicMock, browser.page).goto.assert_not_called()


@pytest.mark.parametrize("selector,text", [("#email", "CUA TEST"), ("#address", "other")])
def test_edit_cannot_fill_another_field_or_value(selector: str, text: str) -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "edit", "CUA TEST"
    with pytest.raises(ValueError):
        browser.execute(action("fill", selector, text))
    cast(MagicMock, browser.page).locator.return_value.fill.assert_not_called()


def test_discovery_cannot_save() -> None:
    browser = browser_test()
    cast(MagicMock, browser.page).locator.return_value.inner_text.return_value = "Save"
    with pytest.raises(ValueError):
        browser.execute(action("click", "button"))
    cast(MagicMock, browser.page).locator.return_value.click.assert_not_called()


@pytest.mark.parametrize("reloaded,value", [(False, "CUA TEST"), (True, "Original street")])
def test_unsaved_or_incorrect_address_is_not_success(reloaded: bool, value: str) -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "edit", "CUA TEST"
    browser.complete, browser.reloaded = False, reloaded
    cast(MagicMock, browser.page).locator.return_value.input_value.return_value = value
    with pytest.raises(ValueError):
        browser.execute(action("verify"))
    assert not browser.complete


def test_persisted_exact_value_completes_verification() -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "edit", "CUA TEST"
    browser.complete, browser.reloaded = False, True
    cast(MagicMock, browser.page).locator.return_value.input_value.return_value = "CUA TEST"
    browser.execute(action("verify"))
    assert browser.complete


def test_restoration_does_not_overwrite_an_external_change() -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "restore", "Original street"
    field = cast(MagicMock, browser.page).locator.return_value
    field.input_value.return_value = "Someone else's edit"
    with pytest.raises(ValueError):
        browser.execute(action("fill", text="Original street"))
    cast(MagicMock, browser.page).locator.return_value.fill.assert_not_called()
