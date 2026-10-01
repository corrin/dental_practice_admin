"""The running application and its two simulations, as pytest fixtures.

Shared by the end-to-end and smoke tiers: both need the same three processes, and starting a
separate pair per tier would double the runtime to prove the same thing.

`dental_practice_admin.app:create_app --factory` is the production entry point. These fixtures
configure simulated providers and explicitly opt out of Google login.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.fake.store import FAKE_API_KEY, FAKE_PRACTICE_ID
from tests.fake_ai import FAKE_AI_KEY

REPO = Path(__file__).resolve().parent.parent

# What `seed()` holds: the diary's first day, in practice-local time, and its size. Every tier
# that reports on the seeded practice reads these, so a change to the seed fails them together.
DIARY_DATE = "2026-09-28"
EXPECTED_BOOKED = 8
EXPECTED_CANCELLED = 2

STARTUP_TIMEOUT = 45.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(target: str, port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            target,
            *(["--factory"] if target.endswith(":create_app") else []),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            # The same flags deploy/dental-practice-admin.xml uses. A spine that started uvicorn
            # differently from the service would test an arrangement nothing ever runs.
            "--proxy-headers",
            "--forwarded-allow-ips",
            "127.0.0.1",
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _await_http(url: str, process: subprocess.Popen[bytes], expect_status: set[int]) -> None:
    """Wait for a server to answer, failing with its own output rather than a bare timeout."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last = "no response yet"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode(errors="replace") if process.stdout else ""
            pytest.fail(f"{url} server exited with {process.returncode}:\n{output}")
        try:
            response = httpx.get(url, timeout=3)
        except httpx.HTTPError as error:
            last = str(error)
        else:
            if response.status_code in expect_status:
                return
            last = f"status {response.status_code}"
        time.sleep(0.25)
    pytest.fail(f"{url} was not ready within {STARTUP_TIMEOUT}s: {last}")


@pytest.fixture(scope="session")
def spine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """The fake Principle and the web application, each in its own process."""
    data_root = tmp_path_factory.mktemp("e2e-data")
    fake_port = _free_port()
    fake_ai_port = _free_port()
    app_port = _free_port()

    env = dict(os.environ)
    env.update(
        {
            "PRINCIPLE_ENVIRONMENT": "fake",
            "PRINCIPLE_API_BASE_URL_FAKE": f"http://127.0.0.1:{fake_port}",
            "PRINCIPLE_API_KEY_FAKE": FAKE_API_KEY,
            "PRINCIPLE_PRACTICE_ID_FAKE": FAKE_PRACTICE_ID,
            "ADMIN_DATA_ROOT": str(data_root),
            "ADMIN_SIGN_IN": "developer",
            "ADMIN_PUBLIC_BASE_URL": "",
            "ADMIN_CHATKIT_DOMAIN_KEY": "domain_pk_localhost",
            "PYTHONPATH": str(REPO),
            # OpenAI's own documented overrides, so the application needs no knowledge that its
            # model is simulated. No production code branches on being under test.
            "OPENAI_BASE_URL": f"http://127.0.0.1:{fake_ai_port}/v1",
            "OPENAI_API_KEY": FAKE_AI_KEY,
        }
    )

    fake = _serve("tests.fake.server:app", fake_port, env)
    fake_ai = _serve("tests.fake_ai.server:app", fake_ai_port, env)
    application = _serve("dental_practice_admin.app:create_app", app_port, env)
    try:
        # Principle's authentication refusal proves its request handler is ready.
        _await_http(f"http://127.0.0.1:{fake_port}/v1/practices", fake, {401, 403, 500})
        _await_http(f"http://127.0.0.1:{fake_ai_port}/health", fake_ai, {200})
        _await_http(f"http://127.0.0.1:{app_port}/health", application, {200})
        yield {**env, "APP_URL": f"http://127.0.0.1:{app_port}"}
    finally:
        for process in (application, fake_ai, fake):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
