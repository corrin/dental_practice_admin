"""Which browser the browser server starts: the release's own, or Playwright's usual one."""
from pathlib import Path

import pytest

from dental_practice_admin import browser
from tests.settings import fake_settings


def test_a_release_runs_the_browser_it_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the server, the install puts the browser in ms-playwright beside the app."""
    (tmp_path / "ms-playwright").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    env = browser.server_environment(fake_settings(tmp_path / "data"))

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "ms-playwright")


def test_without_a_bundled_browser_playwright_finds_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A development checkout has no ms-playwright; Playwright then uses its per-user install."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    env = browser.server_environment(fake_settings(tmp_path / "data"))

    assert "PLAYWRIGHT_BROWSERS_PATH" not in env


def test_an_explicit_browser_location_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ms-playwright").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "elsewhere"))

    env = browser.server_environment(fake_settings(tmp_path / "data"))

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "elsewhere")
