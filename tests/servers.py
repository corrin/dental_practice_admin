"""The running application and its simulations, as pytest fixtures.

The fake Principle runs in its own process and is reached only at its URL, as Principle is.
Every tier shares that process; the end-to-end and smoke tiers add the application beside it.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass
class FakePrincipleServer:
    """One running fake Principle: its address, and its log for what it refused."""

    url: str
    log: Path
    read_to: int = 0

    def reset(self) -> None:
        """Back to the seed, with the log read from here on."""
        httpx.post(f"{self.url}/__fake/reset", timeout=10).raise_for_status()
        self.read_to = self.log.stat().st_size

    def sql(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """One statement against the fake's database, to set up or inspect its state."""
        response = httpx.post(f"{self.url}/__fake/sql", json={"sql": sql, "params": list(params)},
                              timeout=10)
        response.raise_for_status()
        rows: list[dict[str, Any]] = response.json()["rows"]
        return rows

    def take_log(self) -> str:
        """What the fake has logged since this was last read.

        A refusal (FakeUnhandledRouteError and the rest) reaches the client as a bare 500 and
        is named only here, so a test expecting one reads it, and every other test is failed if
        one appears.
        """
        with self.log.open("rb") as log:
            log.seek(self.read_to)
            text = log.read()
        self.read_to += len(text)
        return text.decode(errors="replace")


def _fake_principle(tmp_path_factory: pytest.TempPathFactory,
                    speed: dict[str, str]) -> Iterator[FakePrincipleServer]:
    folder = tmp_path_factory.mktemp("fake-principle")
    port = _free_port()
    log = folder / "fake.log"
    env = ({name: value for name, value in os.environ.items()
            if name != "SIMULATE_PRINCIPLE_SPEED"}
           | {"PYTHONPATH": str(REPO), "PYTHONUNBUFFERED": "1"} | speed)
    process = _serve("tests.fake.server:app", port, env, log)
    try:
        # Principle's authentication refusal proves its request handler is ready.
        _await_http(f"http://127.0.0.1:{port}/v1/practices", process, {401, 403}, log)
        yield FakePrincipleServer(f"http://127.0.0.1:{port}", log)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="session")
def principle_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FakePrincipleServer]:
    """The fake Principle at full speed, shared by the whole session."""
    yield from _fake_principle(tmp_path_factory, {})


@pytest.fixture(scope="session")
def slow_principle_server(
        tmp_path_factory: pytest.TempPathFactory) -> Iterator[FakePrincipleServer]:
    """The fake Principle pausing as Principle was measured pausing (ADR 0009)."""
    yield from _fake_principle(tmp_path_factory, {"SIMULATE_PRINCIPLE_SPEED": "1"})


@pytest.fixture(scope="session")
def spine_principle_server(
        tmp_path_factory: pytest.TempPathFactory) -> Iterator[FakePrincipleServer]:
    """The fake Principle behind the running application, apart from the one tests reset."""
    yield from _fake_principle(tmp_path_factory, {})


@pytest.fixture(scope="session")
def spine(tmp_path_factory: pytest.TempPathFactory,
          spine_principle_server: FakePrincipleServer) -> Iterator[dict[str, str]]:
    """The web application and its simulations, each in its own process."""
    data_root = tmp_path_factory.mktemp("e2e-data")
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
        api_base_url=spine_principle_server.url,
        openai_base_url=f"http://127.0.0.1:{fake_ai_port}/v1",
        akahu_base_url=f"http://127.0.0.1:{fake_bank_port}/v1",
    ) | {"PYTHONPATH": str(REPO)}

    logs = {name: data_root / f"{name}.log" for name in ("fake_ai", "fake_bank", "app")}
    fake_ai = _serve("tests.fake_ai.server:app", fake_ai_port, env, logs["fake_ai"])
    fake_bank = _serve("tests.fake_akahu.server:app", fake_bank_port, env, logs["fake_bank"])
    application = _serve("dental_practice_admin.app:create_app", app_port, env, logs["app"])
    try:
        _await_http(f"http://127.0.0.1:{fake_ai_port}/health", fake_ai, {200}, logs["fake_ai"])
        _await_http(f"http://127.0.0.1:{fake_bank_port}/v1/me", fake_bank, {401},
                    logs["fake_bank"])
        _await_http(f"http://127.0.0.1:{app_port}/health", application, {200}, logs["app"])
        yield {**env, "APP_URL": f"http://127.0.0.1:{app_port}"}
    finally:
        for process in (application, fake_bank, fake_ai):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
