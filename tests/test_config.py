"""Configuration must refuse to address production unless it says it is production."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from principle_admin.config import (
    FAKE_API_URL,
    STAGING_API_URL,
    ConfigurationError,
    Environment,
    Settings,
)

PRODUCTION_API_URL = "https://api.principle.dental"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Build settings from the arguments alone.

    A developer's .env or exported PRINCIPLE_* variables would otherwise decide what these
    assertions are testing, and the failure would look like a bug in the guard.
    """
    for name in list(os.environ):
        if name.startswith("PRINCIPLE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def _settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


@pytest.mark.parametrize("environment", [Environment.FAKE, Environment.STAGING])
def test_non_production_environment_may_not_address_production(
    environment: Environment,
) -> None:
    """Config labelled staging while pointing at production is the mistake that matters.

    Relaxing the cross-check would let a staging-labelled scheduled task read and write
    live patient records, which no later test would notice.
    """
    with pytest.raises((ConfigurationError, ValidationError), match="production"):
        _settings(environment=environment, api_base_url=PRODUCTION_API_URL)


def test_production_environment_must_actually_address_production() -> None:
    """The inverse is also a misconfiguration: a production run silently hitting staging.

    It would report success having changed nothing the practice can see.
    """
    with pytest.raises((ConfigurationError, ValidationError), match="must say so"):
        _settings(environment=Environment.PRODUCTION, api_base_url=STAGING_API_URL)


def test_staging_addressing_staging_is_accepted() -> None:
    """The guard must not be so broad that the intended configuration fails."""
    settings = _settings(environment=Environment.STAGING, api_base_url=STAGING_API_URL)
    assert settings.api_base_url == STAGING_API_URL


def test_real_environment_refuses_missing_credentials() -> None:
    """An unconfigured integration run must fail loudly rather than do nothing.

    A skip or an empty result is indistinguishable from a pass in a summary line.
    """
    settings = _settings(environment=Environment.STAGING, api_base_url=STAGING_API_URL)
    with pytest.raises(ConfigurationError, match="PRINCIPLE_API_KEY"):
        settings.require_credentials()


def test_fake_environment_needs_no_credentials() -> None:
    """The default suite must run with no secrets present at all."""
    settings = _settings(environment=Environment.FAKE, api_base_url=FAKE_API_URL)
    settings.require_credentials()


def test_environments_do_not_share_state() -> None:
    """Staging and production must not share a database or a browser session file.

    Collapsing these paths would let a staging run reuse a production Principle session.
    """
    staging = _settings(environment=Environment.STAGING, api_base_url=STAGING_API_URL)
    production = _settings(
        environment=Environment.PRODUCTION,
        api_base_url=PRODUCTION_API_URL,
        api_key=SecretStr("k"),
        practice_id="p",
    )
    assert staging.database_path != production.database_path
    assert staging.browser_state_path != production.browser_state_path
