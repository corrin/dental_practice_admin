"""The pinned browser server owns login, structured results and exclusive workflow access."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from dental_practice_admin import browser
from dental_practice_admin.config import PRINCIPLE_WEB_URLS, Environment, Settings
from tests.settings import fake_settings

pytestmark = pytest.mark.e2e


@pytest.fixture
def browser_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    page = """<html><body><script>
    if (!localStorage.auth) {
      document.body.innerHTML = '<form><input placeholder="Email">' +
        '<input placeholder="Password" type="password"><button>Login</button></form>';
      document.querySelector('form').onsubmit = e => {
        e.preventDefault(); localStorage.auth='yes'; location.reload();
      };
    } else if (!localStorage.workspace) {
      document.body.innerHTML = '<input placeholder="Search Workspaces">' +
        '<button role="option">Fake workspace</button>';
      document.querySelector('button').onclick = () => {
        localStorage.workspace='yes'; location.href='/fake/schedule/timeline';
      };
    } else if (location.pathname === '/login') location.replace('/fake/schedule/timeline');
    else document.body.innerHTML = '<pr-sidebar><nav>Fake practice navigation</nav></pr-sidebar>';
    </script></body></html>"""
    initializer = tmp_path / "fixture.cjs"
    initializer.write_text(
        "const login = require(" + json.dumps(str(browser.LOGIN.resolve())) + ").default;\n"
        "exports.default = async ({page}) => {\n"
        " await page.route('https://fake.invalid/**', route => route.fulfill({"
        "contentType:'text/html', body:" + json.dumps(page) + "}));\n"
        " await login({page});\n};",
        encoding="utf-8",
    )
    monkeypatch.setattr(browser, "LOGIN", initializer)
    monkeypatch.setitem(PRINCIPLE_WEB_URLS, Environment.FAKE, "https://fake.invalid")
    return fake_settings(
        tmp_path,
        ui_email="fake@fake.invalid",
        ui_password=SecretStr("fake-password"),
        workspace="Fake workspace",
        workspace_slug="fake",
    )


async def signed_out_profile_recovers_and_preserves_structured_results(
    browser_settings: Settings,
) -> None:
    for _ in range(2):
        async with browser.session(browser_settings) as server:
            result = await browser.code(
                server,
                """async(page, inputs) => ({
                summary: await page.getByRole('navigation').innerText(),
                detail: {total: inputs.values.reduce((a,b)=>a+b,0)}, coverage:'complete'
            })""",
                {"values": [2, 5]},
            )
            assert result == {
                "summary": "Fake practice navigation",
                "detail": {"total": 7},
                "coverage": "complete",
            }
            await browser.code(
                server,
                "async(page) => {await page.evaluate(()=>localStorage.clear()); return true}",
                {},
            )


async def waiting_browser_resumes_after_owner_is_cancelled(browser_settings: Settings) -> None:
    entered, waiting, resumed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        async with browser.session(browser_settings):
            entered.set()
            await asyncio.Event().wait()

    async def follower() -> None:
        async def show_wait() -> None:
            waiting.set()

        async with browser.session(browser_settings, show_wait):
            resumed.set()

    first = asyncio.create_task(owner())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 30)
        second = asyncio.create_task(follower())
        await asyncio.wait_for(waiting.wait(), 10)
        assert not resumed.is_set()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, 30)
        assert resumed.is_set()
    finally:
        first.cancel()
        if second is not None:
            second.cancel()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )


@pytest.mark.parametrize("scenario", [signed_out_profile_recovers_and_preserves_structured_results,
                                     waiting_browser_resumes_after_owner_is_cancelled])
def test_browser_lifecycle(browser_settings: Settings,
                           scenario: Callable[[Settings], Coroutine[Any, Any, None]]) -> None:
    """Use a separate loop from pytest-playwright's session-scoped synchronous browser."""
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(asyncio.run, scenario(browser_settings)).result(timeout=120)
