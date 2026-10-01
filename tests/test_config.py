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
    SignIn,
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


def test_production_refuses_developer_sign_in() -> None:
    """Live patient records must never be served to an unauthenticated visitor.

    Separating sign-in from the Principle environment created this combination, which the old
    design made impossible. Without this guard, one environment variable is the difference between
    a login page and an open door.
    """
    settings = _settings(
        environment=Environment.PRODUCTION,
        api_base_url=PRODUCTION_API_URL,
        sign_in=SignIn.DEVELOPER,
        api_key=SecretStr("k"),
        practice_id="p",
    )
    with pytest.raises(ConfigurationError, match="Production requires Google"):
        settings.require_sign_in_configured()


def test_google_sign_in_refuses_missing_credentials() -> None:
    """A deployment nobody can sign in to must fail at startup, not on the first visitor."""
    settings = _settings(sign_in=SignIn.GOOGLE)
    with pytest.raises(ConfigurationError, match="PRINCIPLE_GOOGLE_CLIENT_ID"):
        settings.require_sign_in_configured()


def test_google_sign_in_works_against_the_fake_principle() -> None:
    """The combination this separation exists for: a real gate with nothing real behind it.

    If this were refused, Google sign-in could only ever be exercised with patient data already
    exposed, which is the wrong order to find out the allowlist is wrong.
    """
    settings = _settings(
        environment=Environment.FAKE,
        sign_in=SignIn.GOOGLE,
        session_secret=SecretStr("s"),
        google_client_id="id",
        google_client_secret=SecretStr("secret"),
        staff_emails="someone@practice.nz",
    )
    settings.require_sign_in_configured()


def test_the_fake_banner_follows_principle_not_sign_in() -> None:
    """The banner answers "is this data real", which sign-in has nothing to do with.

    Tying them together is what made the obvious workaround -- label it staging, point it at the
    fake -- quietly remove the banner while the page still showed invented numbers.
    """
    settings = _settings(environment=Environment.FAKE, sign_in=SignIn.GOOGLE)
    assert settings.environment is Environment.FAKE


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
