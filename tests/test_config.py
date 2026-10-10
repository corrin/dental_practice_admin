"""Configuration must refuse to address production unless it says it is production."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from dental_practice_admin.config import (
    FAKE_API_URL,
    PRODUCTION_API_URL,
    STAGING_API_URL,
    ConfigurationError,
    Environment,
    Settings,
    SignIn,
    environment_suffix,
)
from dental_practice_admin.principle import PrincipleClient
from tests.fake import FakeStore
from tests.settings import API_URLS, fake_environment, fake_settings


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start from the complete fake environment, and nothing from this machine.

    A developer's .env or exported variables would otherwise decide what these assertions are
    testing, and the failure would look like a bug in the guard. Every setting is present, so
    a test changes only the one it is about.
    """
    for name in list(os.environ):
        if name.startswith(("ADMIN_", "PRINCIPLE_", "OPENAI_", "AKAHU_")):
            monkeypatch.delenv(name)
    scoped = tuple(f"_{environment_suffix(e)}" for e in Environment)
    for name, value in fake_environment(tmp_path / "data").items():
        if name.startswith(("ADMIN_", "PRINCIPLE_", "OPENAI_", "AKAHU_")) and not name.endswith(
                scoped):
            monkeypatch.setenv(name, value)
    for environment, url in API_URLS.items():
        monkeypatch.setenv(f"PRINCIPLE_API_BASE_URL_{environment_suffix(environment)}", url)
    monkeypatch.chdir(tmp_path)


def _settings(**overrides: Any) -> Settings:
    return fake_settings(Path("data"), **overrides)


@pytest.mark.parametrize("name", sorted(
    name for name, field in Settings.model_fields.items() if field.is_required()))
def test_a_setting_without_a_value_is_refused(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing falls back to a value in the code: `sign_in`, for one, is never assumed."""
    alias = Settings.model_fields[name].validation_alias or f"ADMIN_{name.upper()}"
    monkeypatch.delenv(str(alias))
    complete = _settings().model_dump()
    complete.pop(name)
    with pytest.raises(ValidationError):
        Settings(**complete)


def test_no_setting_has_a_value_written_in_the_code() -> None:
    """A default may only be empty, meaning unset, for a `require_*` method to refuse."""
    for name, field in Settings.model_fields.items():
        if field.is_required():
            continue
        default = field.default
        value = default.get_secret_value() if isinstance(default, SecretStr) else default
        assert value == "", f"{name} defaults to {value!r}"


def test_missing_automation_settings_are_named_as_dotenv_names_them() -> None:
    settings = _settings(environment=Environment.STAGING, api_base_url=STAGING_API_URL,
                         ui_email="fake@fake.invalid", ui_password=SecretStr("fake-password"),
                         firebase_key="fake-key", firebase_project="principle-staging",
                         firestore_root="organisations/fake/brands/fake", workspace_slug="fake")
    with pytest.raises(ConfigurationError, match=r"PRINCIPLE_WORKSPACE_STAGING$"):
        settings.require_automation_configured()


def test_a_principle_address_must_be_set() -> None:
    with pytest.raises(ConfigurationError, match="PRINCIPLE_API_BASE_URL_STAGING"):
        _settings(environment=Environment.STAGING, api_base_url="")


@pytest.mark.parametrize("missing", ["task_repository", "github_token", "openai_base_url"])
def test_the_application_refuses_to_start_without_what_its_features_need(
    missing: str,
) -> None:
    with pytest.raises(ConfigurationError):
        _settings(**{missing: SecretStr("") if missing == "github_token" else ""}
                  ).require_web_configured()


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
    settings = _settings(environment=Environment.STAGING, api_base_url=STAGING_API_URL,
                         api_key=SecretStr(""), practice_id="")
    with pytest.raises(ConfigurationError, match="PRINCIPLE_API_KEY"):
        settings.require_credentials()


def test_the_allowlist_can_be_set_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration must survive arriving as environment variables, which is how it arrives.

    Every other test here builds Settings from Python keyword arguments, which skips the
    environment source entirely. That is how `staff_emails` shipped as a frozenset that
    pydantic-settings tried to JSON-decode: ADMIN_STAFF_EMAILS=someone@example.com raised at
    startup, the application would not boot, and nobody could sign in.
    """
    monkeypatch.setenv("ADMIN_STAFF_EMAILS", "One@Practice.NZ, two@practice.nz")
    monkeypatch.setenv("ADMIN_STAFF_DOMAIN", "@Example.COM")
    monkeypatch.setenv("ADMIN_SIGN_IN", "google")

    settings = Settings()

    assert settings.allowed_emails == {"one@practice.nz", "two@practice.nz"}
    assert settings.admits("ONE@practice.nz", email_verified=True)
    assert settings.admits("anyone@example.com", email_verified=True)
    assert not settings.admits("stranger@elsewhere.com", email_verified=True)
    assert settings.sign_in is SignIn.GOOGLE


def test_openai_settings_use_the_names_openai_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENAI_API_KEY must be read under that name, not ADMIN_OPENAI_API_KEY.

    `env_prefix` applies to every field, so without an alias these read as empty however plainly
    the environment sets them -- and the application quietly falls back to the SDK's own lookup,
    which is the second source of truth this is meant to remove.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8899/v1")

    settings = Settings()

    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert settings.openai_base_url == "http://127.0.0.1:8899/v1"


@pytest.mark.parametrize(
    ("environment", "url"),
    [(Environment.STAGING, STAGING_API_URL), (Environment.PRODUCTION, PRODUCTION_API_URL)],
)
def test_real_patient_data_refuses_developer_sign_in(environment: Environment, url: str) -> None:
    """Patient records must never be served to an unauthenticated visitor.

    Staging counts: it is a migrated copy of the real practice. Separating sign-in from the
    Principle environment created this combination. Without this guard, one environment variable
    is the difference between a login page and an open door.
    """
    settings = _settings(
        environment=environment,
        api_base_url=url,
        sign_in=SignIn.DEVELOPER,
        api_key=SecretStr("k"),
        practice_id="p",
    )
    with pytest.raises(ConfigurationError):
        settings.require_sign_in_configured()


def test_google_sign_in_refuses_missing_credentials() -> None:
    """A deployment nobody can sign in to must fail at startup, not on the first visitor."""
    settings = _settings(sign_in=SignIn.GOOGLE)
    with pytest.raises(ConfigurationError, match="ADMIN_GOOGLE_CLIENT_ID"):
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
    assert staging.data_dir / "browser-profile" != production.data_dir / "browser-profile"


@pytest.mark.parametrize(
    "environment,suffix", [("fake", "FAKE"), ("staging", "STAGING"), ("production", "PROD")]
)
def test_selected_section_and_child_handoff(
    environment: str, suffix: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.run import child_environment

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            f"PRINCIPLE_{field}_{section}=fake-{section}-{field}"
            for section in ("FAKE", "STAGING", "PROD")
            for field in ("API_KEY", "PRACTICE_ID")
        )
    )
    monkeypatch.setitem(Settings.model_config, "env_file", str(dotenv))
    monkeypatch.setenv("PRINCIPLE_ENVIRONMENT", "fake")
    settings = Settings(environment=Environment(environment))
    assert settings.api_key.get_secret_value() == f"fake-{suffix}-API_KEY"
    assert settings.practice_id == f"fake-{suffix}-PRACTICE_ID"
    for key, value in child_environment(settings).items():
        monkeypatch.setenv(key, value)
    dotenv.write_text("")
    child = Settings()
    assert child.environment == settings.environment
    assert child.api_key == settings.api_key
    assert child.practice_id == settings.practice_id
    assert child.api_base_url == settings.api_base_url


def test_missing_selected_section_cannot_use_shared_or_other_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRINCIPLE_API_KEY", "fake-shared-key")
    monkeypatch.setenv("PRINCIPLE_PRACTICE_ID", "fake-shared-practice")
    monkeypatch.setenv("PRINCIPLE_API_KEY_PROD", "fake-production-key")
    monkeypatch.setenv("PRINCIPLE_PRACTICE_ID_PROD", "fake-production-practice")
    with pytest.raises(ConfigurationError, match="PRINCIPLE_API_KEY_STAGING"):
        Settings(environment=Environment.STAGING).require_credentials()


def test_scoped_shell_overrides_dotenv_and_explicit_values_override_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "PRINCIPLE_API_KEY_STAGING=fake-file-key\nPRINCIPLE_API_BASE_URL_STAGING=https://file.fake.invalid\n"
    )
    monkeypatch.setitem(Settings.model_config, "env_file", str(dotenv))
    monkeypatch.setenv("PRINCIPLE_ENVIRONMENT", "staging")
    monkeypatch.setenv("PRINCIPLE_API_KEY_STAGING", "fake-shell-key")
    monkeypatch.setenv("PRINCIPLE_API_BASE_URL_STAGING", STAGING_API_URL)
    settings = Settings()
    assert settings.api_key.get_secret_value() == "fake-shell-key"
    assert settings.api_base_url == STAGING_API_URL
    explicit = Settings(
        api_key=SecretStr("fake-explicit-key"), api_base_url="https://explicit.fake.invalid"
    )
    assert explicit.api_key.get_secret_value() == "fake-explicit-key"
    assert explicit.api_base_url == "https://explicit.fake.invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "fake-principle-key", "00000000-0000-4000-8000-000000000002"])
async def test_fake_server_rejects_incorrect_keys(key: str, fake_store: FakeStore) -> None:
    from dental_practice_admin.principle import PrincipleClient, PrincipleError
    from tests.fake import transport

    settings = _settings(api_key=SecretStr(key))
    async with PrincipleClient(settings, transport=transport(fake_store)) as client:
        with pytest.raises(PrincipleError) as error:
            await client.get("listPractices")
    assert error.value.status in {401, 403}


@pytest.mark.asyncio
async def test_fake_server_accepts_its_synthetic_uuid(fake_client: PrincipleClient) -> None:
    from uuid import UUID

    from tests.fake import FAKE_API_KEY

    assert str(UUID(FAKE_API_KEY)) == FAKE_API_KEY
    assert (await fake_client.get("listPractices"))["data"]
