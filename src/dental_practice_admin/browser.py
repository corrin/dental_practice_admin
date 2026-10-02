"""One authenticated browser workflow at a time across chat and scheduled processes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import portalocker
from agents.mcp import MCPServerStdio

from dental_practice_admin.config import PRINCIPLE_WEB_URLS, Settings

LOGIN = Path(__file__).with_name("browser_login.js")
CODE_TOOL = "browser_run_code_unsafe"
BROWSER_TOOLS = {"browser_click", "browser_fill_form", "browser_type", "browser_press_key",
                 "browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_find",
                 "browser_wait_for", "browser_select_option", "browser_handle_dialog",
                 "browser_hover", "browser_drag", "browser_tabs", "browser_take_screenshot"}


async def code(server: MCPServerStdio, source: str, inputs: dict[str, Any]) -> Any:
    """Execute the same Playwright function for drafts and released tasks."""
    result = await server.call_tool(CODE_TOOL, {"code":
        f"async (page) => await ({source})(page, {json.dumps(inputs)})"})
    if result.is_error:
        raise RuntimeError("Browser operation failed; saved state may need inspection")
    output = "\n".join(item.text for item in result.content if item.type == "text")
    return json.JSONDecoder().raw_decode(output.split("### Result\n", 1)[1])[0]


@asynccontextmanager
async def session(settings: Settings, waiting: Callable[[], Awaitable[None]] | None = None
                  ) -> AsyncIterator[MCPServerStdio]:
    """Own login, workspace, profile and cleanup until the entire workflow finishes."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / "browser.lock").open("a") as lock:
        try:
            portalocker.lock(lock, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.LockException:
            if waiting is not None:
                await waiting()
            while True:
                await asyncio.sleep(0.1)
                try:
                    portalocker.lock(lock, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    break
                except portalocker.LockException:
                    continue
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP",
                                  "USERPROFILE", "LOCALAPPDATA", "PLAYWRIGHT_BROWSERS_PATH"}}
        env.update(PRINCIPLE_UI_EMAIL=settings.ui_email,
                   PRINCIPLE_UI_PASSWORD=settings.ui_password.get_secret_value(),
                   PRINCIPLE_UI_URL=PRINCIPLE_WEB_URLS[settings.environment],
                   PRINCIPLE_WORKSPACE=settings.workspace,
                   PRINCIPLE_WORKSPACE_SLUG=settings.workspace_slug)
        try:
            async with MCPServerStdio(
                params={"command": sys.executable, "args": ["-m", "dental_practice_admin.processes",
                    "node", str(settings.playwright_mcp_path.resolve()),
                    "--headless", "--browser", "chromium",
                    "--user-data-dir", str(settings.data_dir / "browser-profile"),
                    "--init-page", str(LOGIN.resolve()),
                    "--output-dir", str(settings.data_dir / "browser-output")], "env": env},
                client_session_timeout_seconds=None, max_retry_attempts=0,
                tool_filter={"allowed_tool_names": list(BROWSER_TOOLS)},
            ) as server:
                await code(server, "async () => ({ready: true})", {})
                try:
                    yield server
                finally:
                    await server.call_tool("browser_close", {})
        finally:
            portalocker.unlock(lock)
