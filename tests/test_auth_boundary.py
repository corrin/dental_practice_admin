"""Authentication protects every route, independently of the data and model providers."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.responses import RedirectResponse

from dental_practice_admin.app import create_app
from dental_practice_admin.config import ConfigurationError, Environment, Settings, SignIn
from dental_practice_admin.storage import Coverage, Outcome, Storage
from tests.settings import API_URLS, fake_settings


def configured(tmp_path: Path, environment: Environment = Environment.FAKE) -> Settings:
    return fake_settings(
        tmp_path,
        environment=environment,
        api_base_url=API_URLS[environment],
        api_key=SecretStr("fake-review-key"),
        practice_id="fake-review-practice",
        sign_in=SignIn.GOOGLE,
        session_secret=SecretStr("fake-session-secret"),
        google_client_id="fake-google-client",
        google_client_secret=SecretStr("fake-secret"),
        staff_emails="staff@fake.invalid",
        openai_api_key=SecretStr("fake-ai-key"),
        ui_email="fake@fake.invalid",
        ui_password=SecretStr("fake-password"),
        firebase_key="fake-key",
        firebase_project="principle-staging" if environment is Environment.STAGING else "principle",
        firestore_root="organisations/fake/brands/fake",
        workspace="Synthetic workspace",
        workspace_slug="fake",
    )


def test_every_route_requires_identity_including_new_routes(tmp_path: Path) -> None:
    settings = configured(tmp_path)
    store = Storage(settings.database_path)
    run = store.start_run("daily_diary", "fake-test", "fake")
    store.finish_run(run, Outcome.SUCCEEDED, Coverage.COMPLETE, "fake-private-report")
    store.close()
    app = create_app(settings)

    @app.get("/future-feature")
    def future_feature() -> dict[str, str]:
        return {"content": "fake-private-data"}

    with TestClient(app) as client:
        for path in ["/", "/chat", f"/runs/{run}", "/future-feature", "/docs", "/openapi.json"]:
            assert client.get(path).status_code == 401
        assert client.post("/chatkit", json={}).status_code == 401
        assert client.post("/health").status_code == 401
        assert client.get("/auth/logout").status_code == 401
        health = client.get("/health")
        assert health.status_code == 200
        assert set(health.json()) == {"status", "principle", "baseUrl"}
        assert (
            client.get("/", headers={"Accept": "text/html"}, follow_redirects=False).headers[
                "location"
            ]
            == "/auth/login"
        )


@pytest.mark.parametrize("environment", list(Environment))
def test_google_login_works_independently_of_principle(
    tmp_path: Path, environment: Environment
) -> None:
    app = create_app(configured(tmp_path, environment))
    google = app.state.oauth.google
    google.authorize_redirect = AsyncMock(
        return_value=RedirectResponse("https://accounts.google.com")
    )
    google.authorize_access_token = AsyncMock(
        return_value={
            "userinfo": {
                "email": "staff@fake.invalid",
                "email_verified": True,
                "name": "Fake staff",
            }
        }
    )
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/auth/login", follow_redirects=False).status_code == 307
        assert client.get("/auth/callback", follow_redirects=False).status_code == 307
        assert client.get("/").status_code == 200
        assert client.get("/auth/logout", follow_redirects=False).status_code == 307
        assert client.get("/").status_code == 401


def test_developer_identity_is_explicit_visible_and_forbidden_in_production(tmp_path: Path) -> None:
    settings = configured(tmp_path).model_copy(update={"sign_in": SignIn.DEVELOPER})
    with TestClient(create_app(settings)) as client:
        for path in ["/", "/chat"]:
            page = client.get(path)
            assert "GOOGLE LOGIN DISABLED" in page.text
            assert "Sign out" not in page.text
    settings = configured(tmp_path, Environment.PRODUCTION).model_copy(
        update={"sign_in": SignIn.DEVELOPER}
    )
    with pytest.raises(ConfigurationError):
        create_app(settings)


def test_settings_do_not_change_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_SIGN_IN", "google")
    app = create_app(configured(tmp_path))
    with TestClient(app) as client:
        monkeypatch.setenv("ADMIN_SIGN_IN", "developer")
        assert client.get("/").status_code == 401


def test_sign_in_has_no_default(tmp_path: Path) -> None:
    complete = fake_settings(tmp_path).model_dump()
    complete.pop("sign_in")
    with pytest.raises(ValidationError):
        Settings(**complete)


@pytest.mark.parametrize(
    "email,verified", [("outsider@fake.invalid", True), ("staff@fake.invalid", False)]
)
def test_google_identity_must_be_verified_and_allowed(
    tmp_path: Path, email: str, verified: bool
) -> None:
    app = create_app(configured(tmp_path))
    app.state.oauth.google.authorize_access_token = AsyncMock(
        return_value={
            "userinfo": {"email": email, "email_verified": verified, "name": "Fake staff"}
        }
    )
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/auth/callback").status_code == 403
        assert client.get("/").status_code == 401
