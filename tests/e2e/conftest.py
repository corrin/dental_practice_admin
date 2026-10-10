"""Browser configuration for the end-to-end tier."""

from __future__ import annotations

import pytest
from playwright.sync_api import Playwright

from tests.servers import DIARY_DATE, EXPECTED_BOOKED, EXPECTED_CANCELLED, REPO, spine

# The templates mark testable elements with `data-automation-id`; Playwright's default is
# `data-testid`, so without this every get_by_test_id silently matches nothing and the assertions
# fail with a page dump rather than a missing-attribute message.
TEST_ID_ATTRIBUTE = "data-automation-id"

__all__ = ["DIARY_DATE", "EXPECTED_BOOKED", "EXPECTED_CANCELLED", "REPO", "spine"]


@pytest.fixture(scope="session", autouse=True)
def _use_automation_ids(playwright: Playwright) -> None:
    playwright.selectors.set_test_id_attribute(TEST_ID_ATTRIBUTE)
