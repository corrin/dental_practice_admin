"""A streamed chat action prepares files which staff test, save and rerun in the browser."""
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_chat_to_saved_script(page: Page, spine: dict[str, str], tmp_path: Path) -> None:
    base = spine["APP_URL"]
    response = page.request.post(base + "/chatkit", data={"type": "threads.create", "params": {
        "input": {"content": [{"type": "input_text", "text": "List our practitioners"}],
                  "attachments": [], "quoted_text": None, "inference_options": {}}}})
    assert response.ok
    events = [json.loads(line[6:]) for line in response.text().splitlines()
              if line.startswith("data: ")]
    widget = next(event["item"] for event in events
                  if event.get("item", {}).get("type") == "widget")
    action = widget["widget"]["children"][0]["onClickAction"]
    response = page.request.post(base + "/chatkit", data={"type": "threads.custom_action",
        "params": {"thread_id": widget["thread_id"], "item_id": widget["id"],
                   "action": {"type": action["type"], "payload": action["payload"]}}})
    assert response.ok
    match = re.search(r"/tasks/prepare/[0-9a-f]{32}", response.text())
    assert match, response.text()[-1500:]
    page.goto(base + match[0])
    expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
    marker = tmp_path / "execution-count"
    page.locator('#inputs input[name="root[marker]"]').fill(str(marker))
    page.get_by_role("button", name="Test run", exact=True).click()
    expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled(timeout=60000)
    assert marker.read_text() == "x"
    page.get_by_label("Name", exact=True).fill("Shared synthetic script")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/tasks/manage")
    assert marker.read_text() == "x"
    page.locator("#task").select_option(label="Shared synthetic script")
    expect(page.locator("#schedule-editor")).to_be_hidden()
    page.locator('#inputs input[name="root[marker]"]').fill(str(marker))
    page.get_by_role("button", name="Run now", exact=True).click()
    expect(page.get_by_test_id("run-summary")).to_have_text("Synthetic change complete",
                                                          timeout=60000)
    assert marker.read_text() == "xx"
    page.goto(base + "/tasks/manage")
    page.locator("#task").select_option(label="Shared synthetic script")
    page.get_by_role("button", name="Request review for scheduling").click()
    expect(page.locator("#review-status")).to_contain_text("Awaiting review")
