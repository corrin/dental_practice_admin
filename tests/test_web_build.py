"""Staff are warned when Principle's web app is a build nobody here has checked."""

from __future__ import annotations

from pathlib import Path

import httpx2 as httpx
import pytest

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.principle import ACCEPTED_WEB_BUILDS, check_web_build
from dental_practice_admin.storage import Storage
from tests.settings import API_URLS, fake_settings

ACCEPTED = sorted(ACCEPTED_WEB_BUILDS)[0]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return fake_settings(tmp_path, environment=Environment.STAGING,
                         api_base_url=API_URLS[Environment.STAGING])


def serving(body: str, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, text=body))


def page(main_script: str) -> str:
    return f'<script src="runtime.1c01822b33c09bb3.js"></script><script src="{main_script}">'


def warnings(settings: Settings) -> list[dict[str, str]]:
    store = Storage(settings.database_path)
    try:
        return store.interface_warnings()
    finally:
        store.close()


def test_an_unchecked_build_warns_staff(settings: Settings) -> None:
    check_web_build(settings, serving(page("main.0123456789abcdef.js")))
    [warning] = warnings(settings)
    assert warning["reason"] == "new_web_build main.0123456789abcdef.js"


def test_an_accepted_build_does_not_warn(settings: Settings) -> None:
    assert check_web_build(settings, serving(page(ACCEPTED))) == ACCEPTED
    assert warnings(settings) == []


def test_an_unchecked_build_stays_warned_until_a_release_accepts_it(settings: Settings) -> None:
    """A later accepted read must not hide that an unchecked build was served."""
    check_web_build(settings, serving(page("main.0123456789abcdef.js")))
    check_web_build(settings, serving(page(ACCEPTED)))
    assert len(warnings(settings)) == 1


@pytest.mark.parametrize(
    "transport",
    [
        serving("<html>maintenance</html>"),
        serving(page(ACCEPTED), status=503),
        serving(page(ACCEPTED) + page("main.0123456789abcdef.js")),
    ],
    ids=["no main script", "server error", "two main scripts"],
)
def test_an_unreadable_page_warns_rather_than_passing(
    settings: Settings, transport: httpx.MockTransport
) -> None:
    assert check_web_build(settings, transport) is None
    [warning] = warnings(settings)
    assert warning["reason"] == "web_build_unreadable"


def test_an_unreadable_warning_clears_once_the_page_reads(settings: Settings) -> None:
    check_web_build(settings, serving("<html>maintenance</html>"))
    check_web_build(settings, serving(page(ACCEPTED)))
    assert warnings(settings) == []
