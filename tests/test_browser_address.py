"""Boundaries and independently checked outcomes for the manual browser experiment."""

from typing import cast
from unittest.mock import MagicMock

import pytest
from scripts.check_browser_address import (
    ADDRESS,
    EXCLUDED,
    STREET,
    BrowserTest,
    check_location,
    guard_navigation,
    run_phase,
)
from scripts.check_staging import UI_URL

PATIENT_ID = "Ab00hnl8R3EZpPHzcB0G"


def browser_test() -> BrowserTest:
    """Represent an identified dummy's visible address form."""
    page = MagicMock()
    page.url = UI_URL + "/massey-smiles/patients/" + PATIENT_ID
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.is_visible.return_value = True
    page.locator.return_value.input_value.return_value = "Original street"
    field = page.locator.return_value
    body = MagicMock()
    body.inner_text.return_value = "Annette (Annie) Dummy\n12th Dec 1990"
    dialog = MagicMock()
    dialog.count.return_value = 0
    field.inner_text.return_value = "Address: Original street"
    page.locator.side_effect = lambda selector: body if selector == "body" else (
        dialog if selector == "pr-update-patient:visible" else field
    )
    return BrowserTest(page)


def action(kind: str, selector: str = ADDRESS, text: str = "") -> dict[str, str]:
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
    cast(MagicMock, browser.page).locator("body").inner_text.return_value = identity
    with pytest.raises(ValueError):
        browser.execute(action("capture"))
    assert browser.original is None


def test_capture_reads_saved_profile_despite_blank_manual_input() -> None:
    browser = browser_test()
    cast(MagicMock, browser.page).locator.return_value.input_value.return_value = ""
    browser.execute(action("capture"))
    assert browser.original == "Original street"
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
    cast(MagicMock, browser.page).locator.return_value.inner_text.return_value = "Address: " + value
    with pytest.raises(ValueError):
        browser.execute(action("verify"))
    assert not browser.complete


def test_persisted_exact_value_completes_verification() -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "edit", "CUA TEST"
    browser.complete, browser.reloaded = False, True
    cast(MagicMock, browser.page).locator.return_value.inner_text.return_value = "Address: CUA TEST"
    browser.execute(action("verify"))
    assert browser.complete


def test_restoration_does_not_overwrite_an_external_change() -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "restore", "Original street"
    field = cast(MagicMock, browser.page).locator.return_value
    field.inner_text.return_value = "Address: Someone else's edit"
    button = MagicMock()
    button.count.return_value = 1
    button.is_visible.return_value = True
    button.inner_text.return_value = "Edit"
    cast(MagicMock, browser.page).get_by_text.return_value = button
    with pytest.raises(ValueError):
        browser.execute(action("click", selector="text=Edit"))
    cast(MagicMock, browser.page).locator.return_value.fill.assert_not_called()


def test_capture_refuses_an_unsaved_dialog() -> None:
    browser = browser_test()
    cast(MagicMock, browser.page).locator("pr-update-patient:visible").count.return_value = 1
    with pytest.raises(ValueError):
        browser.execute(action("capture"))
    assert browser.original is None


def test_only_street_field_can_be_filled_after_capture() -> None:
    browser = browser_test()
    browser.execute(action("capture"))
    browser.phase, browser.expected = "edit", browser.test_value
    browser.execute(action("fill", STREET, browser.test_value))
    cast(MagicMock, browser.page).locator.return_value.press_sequentially.assert_called_once_with(
        browser.test_value, delay=40,
    )


def test_model_keeps_page_history_and_stops_repeating_actions() -> None:
    browser = MagicMock()
    browser.phase = "discovery"
    browser.expected = ""
    browser.original = None
    browser.test_value = "CUA TEST"
    browser.observation.return_value = [
        {"type": "input_text", "text": "Rendered page"},
        {"type": "input_image", "image_url": "data:image/png;base64,example"},
    ]
    client = MagicMock()
    call = MagicMock()
    call.type = "function_call"
    call.arguments = '{"action":"observe","selector":"","text":"","patient_id":""}'
    client.responses.create.return_value.output = [call]
    client.responses.create.return_value.usage = None
    stats = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "diagnostic": False}
    with pytest.raises(RuntimeError, match="Repeated browser action"):
        run_phase(client, "test-model", browser, stats)
    assert browser.execute.call_count == 2
    third_input = client.responses.create.call_args_list[2].kwargs["input"]
    assert sum(item == {"role": "user", "content": [browser.observation.return_value[0]]}
               for item in third_input) == 3
