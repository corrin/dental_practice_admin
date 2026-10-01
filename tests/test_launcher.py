"""Normal launch and diagnostic overrides must resolve to the same settings in every child."""

import argparse
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from scripts import run

from dental_practice_admin.config import ConfigurationError, Environment, Settings, SignIn
from dental_practice_admin.storage import Coverage, Outcome, Storage


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "PRINCIPLE_API_KEY": "synthetic-key",
        "PRINCIPLE_PRACTICE_ID": "synthetic-practice",
        "OPENAI_API_KEY": "fake-ai-key",
        "ADMIN_SESSION_SECRET": "fake-session",
        "ADMIN_GOOGLE_CLIENT_ID": "fake-google-client",
        "ADMIN_GOOGLE_CLIENT_SECRET": "fake-google-secret",
        "ADMIN_STAFF_EMAILS": "staff@fake.invalid",
        "ADMIN_CHATKIT_DOMAIN_KEY": "fake-registered-domain",
    }.items():
        monkeypatch.setenv(key, value)


def arguments(**overrides: object) -> argparse.Namespace:
    return argparse.Namespace(
        **({"preset": None, "principle": None, "sign_in": None, "ai": None} | overrides)
    )


def test_normal_run_is_staging_with_google_and_real_ai(credentials: None) -> None:
    settings = run.configuration(arguments())
    assert settings.environment is Environment.STAGING
    assert settings.api_base_url == run.PRINCIPLE_URLS[Environment.STAGING]
    assert settings.sign_in is SignIn.GOOGLE
    assert settings.openai_base_url == ""
    assert settings.public_base_url == run.STAGING_ORIGIN


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
    with pytest.raises(ConfigurationError, match="Production requires Google"):
        run.configuration(arguments(principle=Environment.PRODUCTION, sign_in=SignIn.DEVELOPER))


def test_fake_preset_still_requires_explicit_authentication_opt_out() -> None:
    with pytest.raises(ConfigurationError, match="sign_in=google"):
        run.configuration(arguments(preset=Environment.FAKE))
    settings = run.configuration(arguments(preset=Environment.FAKE, sign_in=SignIn.DEVELOPER))
    assert settings.api_key.get_secret_value() == "fake-principle-key"
    assert settings.openai_api_key.get_secret_value() == "fake-openai-key"


def test_dotenv_choices_survive_without_cli_overrides(
    credentials: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    (tmp_path / ".env").write_text(
        "ADMIN_SIGN_IN=developer\nOPENAI_BASE_URL=http://fake.invalid/v1\n"
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
    assert env["OPENAI_API_KEY"] == "fake-ai-key"


def test_existing_fake_history_is_not_reseeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        environment=Environment.FAKE, data_root=tmp_path, openai_api_key=SecretStr("fake-key")
    )
    store = Storage(settings.database_path)
    run_id = store.start_run("daily_diary", "fake-test", "fake")
    store.finish_run(run_id, Outcome.SUCCEEDED, Coverage.COMPLETE, "fake-result")
    store.close()
    operation = AsyncMock()
    monkeypatch.setattr(run, "run_daily_diary", operation)
    run.seed_runs(settings)
    operation.assert_not_called()


@pytest.mark.parametrize("failure", ["startup", "running", "interrupt"])
def test_launcher_stops_its_children_on_failure_or_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    settings = Settings(
        environment=Environment.FAKE, data_root=tmp_path, public_base_url="http://localhost:8080"
    )
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
