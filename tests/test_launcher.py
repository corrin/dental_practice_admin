"""Normal launch and diagnostic overrides must resolve to the same settings in every child."""

import argparse
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from scripts import run

from dental_practice_admin.config import (
    FAKE_PRACTICE_ID,
    STAGING_API_URL,
    ConfigurationError,
    Environment,
    Settings,
    SignIn,
)
from tests.fake.store import FAKE_API_KEY
from tests.fake_ai import FAKE_AI_KEY
from tests.settings import API_URLS, fake_settings, use_fake_environment

PUBLIC_ORIGIN = "https://admin.fake.invalid"


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A complete .env, as a developer's holds one, through the environment."""
    use_fake_environment(
        monkeypatch, tmp_path / "data",
        environment=Environment.STAGING, api_base_url=STAGING_API_URL, sign_in=SignIn.GOOGLE,
        openai_base_url="https://api.openai.com/v1", public_base_url=PUBLIC_ORIGIN,
        chatkit_domain_key="fake-registered-domain",
        session_secret=SecretStr("fake-session"), google_client_id="fake-google-client",
        google_client_secret=SecretStr("fake-google-secret"), staff_emails="staff@fake.invalid",
        api_key=SecretStr("synthetic-staging-key"), practice_id="synthetic-staging-practice",
        ui_email="fake@fake.invalid", ui_password=SecretStr("fake-password"),
        firebase_key="fake-key", firebase_project="principle-staging",
        firestore_root="organisations/fake/brands/fake", workspace="Synthetic workspace",
        google_maps_api_key=SecretStr("fake-maps-key"),
        workspace_slug="fake")
    # The other environments' sections, which a .env holds beside the selected one.
    for name, value in {
        "PRINCIPLE_API_BASE_URL_PROD": API_URLS[Environment.PRODUCTION],
        "PRINCIPLE_API_KEY_PROD": "synthetic-production-key",
        "PRINCIPLE_PRACTICE_ID_PROD": "synthetic-production-practice",
        "PRINCIPLE_UI_EMAIL_PROD": "fake@fake.invalid",
        "PRINCIPLE_UI_PASSWORD_PROD": "fake-password",
        "PRINCIPLE_FIREBASE_KEY_PROD": "fake-key",
        "PRINCIPLE_FIREBASE_PROJECT_PROD": "principle",
        "PRINCIPLE_FIRESTORE_ROOT_PROD": "organisations/fake/brands/fake",
        "PRINCIPLE_WORKSPACE_PROD": "Synthetic workspace",
        "PRINCIPLE_WORKSPACE_SLUG_PROD": "fake",
        "PRINCIPLE_API_BASE_URL_FAKE": "http://127.0.0.1:8898",
        "PRINCIPLE_API_KEY_FAKE": FAKE_API_KEY,
        "PRINCIPLE_PRACTICE_ID_FAKE": FAKE_PRACTICE_ID,
    }.items():
        monkeypatch.setenv(name, value)


def arguments(**overrides: object) -> argparse.Namespace:
    return argparse.Namespace(
        **({"preset": None, "principle": None, "sign_in": None, "ai": None} | overrides)
    )


def test_a_run_without_options_uses_the_configuration_as_it_is(credentials: None) -> None:
    settings = run.configuration(arguments())
    assert settings.environment is Environment.STAGING
    assert settings.api_base_url == STAGING_API_URL
    assert settings.sign_in is SignIn.GOOGLE
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.public_base_url == PUBLIC_ORIGIN


@pytest.mark.parametrize("principle", list(Environment))
@pytest.mark.parametrize("ai", ["real", "fake"])
def test_principle_and_ai_do_not_choose_authentication(
    credentials: None, principle: Environment, ai: str
) -> None:
    settings = run.configuration(arguments(principle=principle, ai=ai))
    assert settings.environment is principle
    assert settings.sign_in is SignIn.GOOGLE
    assert (settings.openai_base_url == "http://127.0.0.1:8899/v1") == (ai == "fake")


def test_explicit_developer_login_cannot_reach_production(credentials: None) -> None:
    with pytest.raises(ConfigurationError):
        run.configuration(arguments(principle=Environment.PRODUCTION, sign_in=SignIn.DEVELOPER))


def test_the_fake_preset_never_turns_sign_in_off_by_itself(credentials: None) -> None:
    assert run.configuration(arguments(preset=Environment.FAKE)).sign_in is SignIn.GOOGLE
    settings = run.configuration(arguments(preset=Environment.FAKE, sign_in=SignIn.DEVELOPER))
    assert settings.sign_in is SignIn.DEVELOPER
    assert settings.api_key.get_secret_value() == FAKE_API_KEY
    assert settings.openai_api_key.get_secret_value() == "fake-openai-key"


def test_dotenv_choices_survive_without_cli_overrides(
    credentials: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for chosen in ("PRINCIPLE_ENVIRONMENT", "ADMIN_SIGN_IN", "OPENAI_BASE_URL"):
        monkeypatch.delenv(chosen)
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    (tmp_path / ".env").write_text(
        "PRINCIPLE_ENVIRONMENT=fake\nADMIN_SIGN_IN=developer\n"
        "OPENAI_BASE_URL=http://fake.invalid/v1\n"
    )
    before = dict(os.environ)
    settings = run.configuration(arguments())
    assert settings.sign_in is SignIn.DEVELOPER
    assert settings.openai_base_url == "http://fake.invalid/v1"
    assert dict(os.environ) == before


def test_children_receive_the_resolved_configuration(credentials: None) -> None:
    settings = run.configuration(arguments(principle=Environment.FAKE, ai="real"))
    env = run.child_environment(settings)
    assert env["PRINCIPLE_ENVIRONMENT"] == "fake"
    assert env["ADMIN_SIGN_IN"] == "google"
    assert env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert env["OPENAI_API_KEY"] == FAKE_AI_KEY


@pytest.mark.parametrize("failure", ["startup", "running", "interrupt"])
def test_launcher_stops_its_children_on_failure_or_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    settings = fake_settings(tmp_path, api_base_url="http://127.0.0.1:8898",
                             public_base_url="http://localhost:8080")
    child = MagicMock()
    child.poll.return_value = None
    monkeypatch.setattr("scripts.run.subprocess.Popen", MagicMock(return_value=child))
    monkeypatch.setattr("scripts.run.socket.socket", MagicMock())
    readiness = MagicMock()
    if failure == "startup":
        readiness.side_effect = TimeoutError("fake startup failure")
    elif failure == "interrupt":
        readiness.side_effect = KeyboardInterrupt()
    else:
        child.poll.return_value = 1
    monkeypatch.setattr(run, "wait_for_server", readiness)
    with pytest.raises((TimeoutError, RuntimeError, KeyboardInterrupt)):
        run.run(settings)
    child.wait.assert_called()
    if failure != "running":
        child.terminate.assert_called_once()


def test_cli_principle_overrides_preset_and_dotenv(
    credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRINCIPLE_ENVIRONMENT", "production")
    monkeypatch.setenv("PRINCIPLE_API_BASE_URL_STAGING", "https://staging.fake.invalid")
    settings = run.configuration(arguments(preset=Environment.FAKE, principle=Environment.STAGING))
    assert settings.environment is Environment.STAGING
    assert settings.api_key.get_secret_value() == "synthetic-staging-key"
    assert settings.practice_id == "synthetic-staging-practice"
    assert settings.api_base_url == "https://staging.fake.invalid"
    assert settings.sign_in is SignIn.GOOGLE
    assert settings.openai_base_url == "http://127.0.0.1:8899/v1"
