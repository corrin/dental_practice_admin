"""Browser configuration for the smoke tier, which shares the end-to-end servers."""

from __future__ import annotations

import pytest
from playwright.sync_api import Playwright

from tests.servers import DIARY_DATE, REPO, spine

TEST_ID_ATTRIBUTE = "data-automation-id"

__all__ = ["DIARY_DATE", "REPO", "spine"]


@pytest.fixture(scope="session", autouse=True)
def _use_automation_ids(playwright: Playwright) -> None:
    playwright.selectors.set_test_id_attribute(TEST_ID_ATTRIBUTE)
