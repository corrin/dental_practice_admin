"""Fixtures for every tier, and the guards that keep tests from changing production.

The default suite talks only to the fake. The integration tier reads production (ADR 0006) and
**refuses rather than skips** when it is unconfigured: a skip and a pass look the same in a
summary line, which is exactly how an integration suite quietly stops testing anything.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from dental_practice_admin.config import (
    Environment,
    Settings,
    is_production_host,
)
from dental_practice_admin.principle import PrincipleClient
from tests import settings as test_settings
from tests.servers import FakePrincipleServer, principle_server, slow_principle_server

__all__ = ["principle_server", "slow_principle_server"]


@pytest.fixture(autouse=True)
def _never_production() -> None:
    """Fail any test whose ambient configuration addresses production.

    Guards the case where a developer's shell still holds the production API key from an
    afternoon of operational work.
    """
    configured = os.environ.get("PRINCIPLE_API_BASE_URL_STAGING")
    if configured and is_production_host(configured):
        pytest.fail(
            f"PRINCIPLE_API_BASE_URL_STAGING={configured!r} addresses production; "
            "no test may run against live patient records"
        )


@pytest.fixture(autouse=True)
def _isolate_local_tests(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, _never_production: None
) -> None:
    """Only explicit live tiers may read this machine's credentials or configuration."""
    if request.node.get_closest_marker("integration") or request.node.get_closest_marker("llm"):
        return
    for name in list(os.environ):
        if name.startswith(("ADMIN_", "PRINCIPLE_", "OPENAI_", "AKAHU_")):
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def _reset(server: FakePrincipleServer) -> Iterator[FakePrincipleServer]:
    server.reset()
    yield server
    log = server.take_log()
    if "Traceback" in log:
        pytest.fail(f"the fake Principle refused a request:\n{log}")


@pytest.fixture
def fake_principle(principle_server: FakePrincipleServer) -> Iterator[FakePrincipleServer]:
    """The fake Principle at its seed, failing the test if it refuses anything unread."""
    yield from _reset(principle_server)


@pytest.fixture
def slow_principle(slow_principle_server: FakePrincipleServer) -> Iterator[FakePrincipleServer]:
    """The fake Principle at Principle's measured speed, at its seed."""
    yield from _reset(slow_principle_server)


@pytest.fixture
def fake_settings(tmp_path: Path) -> Settings:
    return test_settings.fake_settings(tmp_path)


@pytest.fixture
def principle_settings(tmp_path: Path, fake_principle: FakePrincipleServer) -> Settings:
    """The fake environment's settings, addressing the running fake Principle."""
    return test_settings.fake_settings(tmp_path, api_base_url=fake_principle.url)


@pytest.fixture
async def fake_client(principle_settings: Settings) -> AsyncIterator[PrincipleClient]:
    """The production client, reaching the fake Principle at its URL."""
    client = PrincipleClient(principle_settings)
    yield client
    await client.aclose()


@pytest.fixture
def production_settings() -> Settings:
    """Production configuration, for reads only, refusing when credentials are absent."""
    settings = Settings(environment=Environment.PRODUCTION)
    settings.require_credentials()
    return settings


@pytest.fixture
async def production_client(production_settings: Settings) -> AsyncIterator[PrincipleClient]:
    client = PrincipleClient(production_settings, transport=test_settings.ReadingOnly())
    yield client
    await client.aclose()
