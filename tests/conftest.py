"""Fixtures for every tier, and the guard that keeps tests away from production.

The default suite talks only to the fake. The integration tier talks to staging and
**refuses rather than skips** when it is unconfigured: a skip and a pass look the same in a
summary line, which is exactly how an integration suite quietly stops testing anything.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from pydantic import SecretStr

from dental_practice_admin.config import (
    STAGING_API_URL,
    ConfigurationError,
    Environment,
    Settings,
    is_production_host,
)
from dental_practice_admin.principle import PrincipleClient
from tests.fake import FAKE_API_KEY, FAKE_PRACTICE_ID, FakeStore, seed, transport


@pytest.fixture(autouse=True)
def _never_production() -> None:
    """Fail any test whose ambient configuration addresses production.

    Guards the case where a developer's shell still holds the production API key from an
    afternoon of operational work.
    """
    configured = os.environ.get("PRINCIPLE_API_BASE_URL")
    if configured and is_production_host(configured):
        pytest.fail(
            f"PRINCIPLE_API_BASE_URL={configured!r} addresses production; "
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
        if name.startswith(("ADMIN_", "PRINCIPLE_", "OPENAI_")):
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def fake_store() -> Iterator[FakeStore]:
    """A seeded fake practice, one per test."""
    store = seed()
    yield store
    store.close()


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(
        environment=Environment.FAKE,
        api_key=SecretStr(FAKE_API_KEY),
        practice_id=FAKE_PRACTICE_ID,
    )


@pytest.fixture
async def fake_client(
    fake_settings: Settings, fake_store: FakeStore
) -> AsyncIterator[PrincipleClient]:
    """The production client with the fake installed where its socket would be."""
    client = PrincipleClient(fake_settings, transport=transport(fake_store))
    yield client
    await client.aclose()


@pytest.fixture
def staging_settings() -> Settings:
    """Staging configuration, refusing when credentials are absent."""
    settings = Settings(
        environment=Environment.STAGING,
        api_base_url=os.environ.get("PRINCIPLE_API_BASE_URL", STAGING_API_URL),
    )
    if is_production_host(settings.api_base_url):
        raise ConfigurationError("the integration tier must not address production")
    settings.require_credentials()
    return settings


@pytest.fixture
async def staging_client(staging_settings: Settings) -> AsyncIterator[PrincipleClient]:
    client = PrincipleClient(staging_settings)
    yield client
    await client.aclose()
