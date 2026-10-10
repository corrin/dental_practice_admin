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

import httpx2 as httpx
import pytest

from tests.settings import fake_environment

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


def _serve(target: str, port: int, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    """Start one server, writing its output to `log`.

    A file, not a pipe: nothing reads a pipe while the tests run, so a server that logs more
    than the pipe holds blocks on its next write and every later request times out.
    """
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
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )


def _await_http(url: str, process: subprocess.Popen[bytes], expect_status: set[int],
                log: Path) -> None:
    """Wait for a server to answer, failing with its own output rather than a bare timeout."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last = "no response yet"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = log.read_text(errors="replace")
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
    fake_bank_port = _free_port()
    app_port = _free_port()

    # Every setting, as the fake environment has it, with only the simulations' real addresses
    # changed. OpenAI's own documented base URL selects the simulated model, so no production
    # code branches on being under test. The empty public address survives in the child's
    # environment block; tests/smoke/test_behind_a_proxy.py shows the app then reads the origin
    # from each request.
    env = dict(os.environ) | fake_environment(
        data_root,
        api_base_url=f"http://127.0.0.1:{fake_port}",
        openai_base_url=f"http://127.0.0.1:{fake_ai_port}/v1",
        akahu_base_url=f"http://127.0.0.1:{fake_bank_port}/v1",
    ) | {"PYTHONPATH": str(REPO)}

    logs = {name: data_root / f"{name}.log" for name in ("fake", "fake_ai", "fake_bank", "app")}
    fake = _serve("tests.fake.server:app", fake_port, env, logs["fake"])
    fake_ai = _serve("tests.fake_ai.server:app", fake_ai_port, env, logs["fake_ai"])
    fake_bank = _serve("tests.fake_akahu.server:app", fake_bank_port, env, logs["fake_bank"])
    application = _serve("dental_practice_admin.app:create_app", app_port, env, logs["app"])
    try:
        # Principle's authentication refusal proves its request handler is ready.
        _await_http(f"http://127.0.0.1:{fake_port}/v1/practices", fake, {401, 403, 500},
                    logs["fake"])
        _await_http(f"http://127.0.0.1:{fake_ai_port}/health", fake_ai, {200}, logs["fake_ai"])
        _await_http(f"http://127.0.0.1:{fake_bank_port}/v1/me", fake_bank, {401},
                    logs["fake_bank"])
        _await_http(f"http://127.0.0.1:{app_port}/health", application, {200}, logs["app"])
        yield {**env, "APP_URL": f"http://127.0.0.1:{app_port}"}
    finally:
        for process in (application, fake_bank, fake_ai, fake):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
