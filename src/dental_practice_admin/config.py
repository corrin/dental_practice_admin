"""Which Principle we are talking to, and where our own data lives.

Three environments, and the difference between them is the whole safety story:

    fake        the local simulation (tests/fake_principle.py) -- no network at all
    staging     api.staging.principle.dental -- a migrated copy of the real practice
    production  api.principle.dental -- live patient records

`environment` is declared, not inferred, because the interesting mistake is config that
claims to be staging while addressing production. `Settings` cross-checks the declared
environment against the host actually configured and refuses a mismatch either way.

Principle credentials are scoped by environment. ADMIN_* controls this application;
Google sign-in and OpenAI configuration are independent of the Principle environment.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from starlette.requests import Request

PRODUCTION_API_HOSTS = frozenset({"api.principle.dental", "app.principle.dental"})

STAGING_API_URL = "https://api.staging.principle.dental"

# The fake answers in-process; the URL exists only so httpx can build a request, and the
# host makes a stray real call obvious in a log or a traceback.
FAKE_API_URL = "https://fake.principle.invalid"
FAKE_API_KEY = "00000000-0000-4000-8000-000000000001"
FAKE_PRACTICE_ID = "fake-practice-0001"


class Environment(StrEnum):
    """Which Principle a process is addressing."""

    FAKE = "fake"
    STAGING = "staging"
    PRODUCTION = "production"


PRINCIPLE_URLS = {
    Environment.FAKE: FAKE_API_URL,
    Environment.STAGING: STAGING_API_URL,
    Environment.PRODUCTION: "https://api.principle.dental",
}


# Principle's web origins; the fake uses an unroutable origin unless a test supplies one.
PRINCIPLE_WEB_URLS = {
    Environment.FAKE: "https://fake.principle.invalid",
    Environment.STAGING: "https://staging.principle.dental",
    Environment.PRODUCTION: "https://app.principle.dental",
}


def environment_suffix(environment: str) -> str:
    """The suffix identifying one Principle configuration section."""
    return "PROD" if environment == "production" else environment.upper()


class SignIn(StrEnum):
    """How a visitor becomes a staff member.

    Separate from `Environment` because they are different questions: which Principle holds the
    data, and who is allowed to see it. Welding them together meant Google sign-in could only be
    exercised with real patient records already behind it.
    """

    GOOGLE = "google"
    DEVELOPER = "developer"


def api_host(url: str) -> str:
    """The bare hostname of an API base URL, lowercased and without port."""
    return (urlsplit(url).hostname or "").lower()


def is_production_host(url: str) -> bool:
    """True when this URL addresses live patient records."""
    return api_host(url) in PRODUCTION_API_HOSTS


class ConfigurationError(Exception):
    """Configuration that would send a request somewhere it must not go."""


ENVIRONMENT_FIELDS = ("api_base_url", "api_key", "practice_id", "ui_email", "ui_password",
                      "firebase_key", "firebase_project", "firestore_root",
                      "workspace", "workspace_slug")


class Settings(BaseSettings):
    """Resolved runtime configuration for one process."""

    model_config = SettingsConfigDict(
        env_prefix="ADMIN_",
        # An aliased field is otherwise settable only by its alias, so Settings(api_base_url=...)
        # would be silently ignored and the production guard would never fire.
        populate_by_name=True,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Field(
        default=Environment.STAGING, validation_alias="PRINCIPLE_ENVIRONMENT"
    )
    api_base_url: str = Field(default="", validation_alias="PRINCIPLE_API_BASE_URL")
    api_key: SecretStr = Field(default=SecretStr(""), validation_alias="PRINCIPLE_API_KEY")
    practice_id: str = Field(default="", validation_alias="PRINCIPLE_PRACTICE_ID")
    ui_email: str = Field(default="", validation_alias="PRINCIPLE_UI_EMAIL")
    ui_password: SecretStr = Field(default=SecretStr(""), validation_alias="PRINCIPLE_UI_PASSWORD")
    firebase_key: str = Field(default="", validation_alias="PRINCIPLE_FIREBASE_KEY")
    firebase_project: str = Field(default="", validation_alias="PRINCIPLE_FIREBASE_PROJECT")
    firestore_root: str = Field(default="", validation_alias="PRINCIPLE_FIRESTORE_ROOT")
    workspace: str = Field(default="", validation_alias="PRINCIPLE_WORKSPACE")
    workspace_slug: str = Field(default="", validation_alias="PRINCIPLE_WORKSPACE_SLUG")
    playwright_mcp_path: Path = Path("node_modules/@playwright/mcp/cli.js")
    task_repository: str = ""
    github_token: SecretStr = SecretStr("")
    # Geocodes patient addresses for the contact-details clean-up. Only the address string is
    # sent; the owner approved sending it to Google on 2026-10-10.
    google_maps_api_key: SecretStr = SecretStr("")

    # Staff sign-in. Google holds the credentials; this application holds only the list of
    # people allowed in, so there is no password store to leak or reset.
    #
    # Developer identity requires an explicit opt-out and is forbidden against production.
    sign_in: SignIn = SignIn.GOOGLE
    session_secret: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")

    # Either mechanism admits a user: a named address, or any address at a Workspace domain.
    # Both empty means nobody, which is why production refuses to start that way.
    #
    # A plain comma-separated string, not a set. pydantic-settings JSON-decodes complex types from
    # environment and dotenv sources before any validator runs, so a `frozenset` field could only
    # ever be set from Python -- ADMIN_STAFF_EMAILS=someone@example.com failed to parse and the
    # application refused to start. Use `allowed_emails` to read it.
    staff_emails: str = ""
    staff_domain: str = ""

    # Read here rather than left to the OpenAI SDK's own environment lookup, so .env is the one
    # place configuration lives. Otherwise every launcher needs code to copy .env into the process
    # environment before the SDK will see it.
    #
    # Aliased to the names OpenAI documents, not ADMIN_OPENAI_*: anyone who has used the SDK
    # already knows them, and `env_prefix` would otherwise invent a second spelling that silently
    # reads as empty.
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OPENAI_API_KEY",
    )
    openai_base_url: str = Field(
        default="",
        validation_alias="OPENAI_BASE_URL",
    )
    # Confirmed present on /v1/models. A default that names a retired model is a chat box that
    # breaks for staff on the day it is retired, so this is worth keeping current.
    agent_model: str = "gpt-6.1-sol"

    # Registered with OpenAI for the domain the chat page is served from, and required by the
    # ChatKit component alongside the endpoint URL. Not a secret: it is rendered into the page.
    chatkit_domain_key: str = "domain_pk_localhost"

    # The origin staff reach, when it cannot be read from the request -- for example a scheduled
    # task building a link. Behind a proxy the request carries it; set this only to override.
    public_base_url: str = ""

    # Runtime data sits outside the source checkout on a real host (ARCHITECTURE.md,
    # Storage and configuration). Production and staging must not share a database or a
    # browser session file, so the environment name is part of the path.
    data_root: Path = Field(default=Path.home() / "dental_practice_admin_data")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        def resolved() -> dict[str, Any]:
            initial = init_settings()
            sources = [dotenv_settings, env_settings]
            values: dict[str, Any] = {}
            for source in sources:
                values.update(source())
            values.update(initial)
            environment = values.get("environment", values.get("PRINCIPLE_ENVIRONMENT", "staging"))
            suffix = environment_suffix(environment)
            for field in ENVIRONMENT_FIELDS:
                alias = f"PRINCIPLE_{field.upper()}"
                values.pop(alias, None)
                for source in sources:
                    raw = getattr(source, "env_vars", {})
                    scoped = raw.get(f"{alias}_{suffix}".lower())
                    if scoped is not None:
                        values[alias] = scoped
                if field in initial or alias in initial:
                    values.pop(alias, None)
                    values[field] = initial.get(field, initial.get(alias))
            return values

        return (resolved,)

    @property
    def data_dir(self) -> Path:
        """Per-environment directory for the database, logs and browser session state."""
        return self.data_root / self.environment.value

    @property
    def database_path(self) -> Path:
        """SQLite file holding run history and conversations."""
        return self.data_dir / "dental_practice_admin.db"

    @property
    def allowed_emails(self) -> frozenset[str]:
        """The allowlist, lowercased.

        Google returns an address in whatever case the account was created with, so comparing raw
        would admit a capitalised address or refuse a legitimate one.
        """
        return frozenset(
            part.strip().lower() for part in self.staff_emails.split(",") if part.strip()
        )

    @field_validator("staff_domain", mode="before")
    @classmethod
    def _normalise_domain(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower().removeprefix("@")
        return value

    def admits(self, email: str, email_verified: bool) -> bool:
        """Whether this address may sign in.

        An unverified address is refused outright: Google will assert an `email` claim for an
        account that has not proven it owns the address, and matching that against the
        allowlist would let anyone who registers a lookalike address in.
        """
        if not email_verified:
            return False
        address = email.strip().lower()
        if address in self.allowed_emails:
            return True
        return bool(self.staff_domain) and address.endswith(f"@{self.staff_domain}")

    def public_origin(self, request: Request) -> str:
        """The scheme and host staff actually reach, with no trailing slash.

        `request.base_url` already reflects `X-Forwarded-Proto` and `X-Forwarded-Host` when uvicorn
        runs with `--proxy-headers`; without that flag it reports the socket, which is the bug this
        exists to make visible. An explicit `public_base_url` overrides both.
        """
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        return str(request.base_url).rstrip("/")

    def require_sign_in_configured(self) -> None:
        """Refuse a real deployment that nobody can sign in to, or that anybody can.

        An empty allowlist with no domain admits nobody, which is a broken deployment. A
        missing session secret means cookies are unsigned, which is worse than no sign-in at
        all: it looks protected and is not.
        """
        # Staging is a migrated copy of the real practice, so only the fake holds data that may be
        # served without sign-in.
        if self.environment is not Environment.FAKE and self.sign_in is not SignIn.GOOGLE:
            raise ConfigurationError(
                f"environment={self.environment.value} with sign_in={self.sign_in.value}: patient"
                " records would be served to anyone who found the address. Only the fake"
                " Principle may run without Google."
            )
        if self.sign_in is not SignIn.GOOGLE:
            return
        missing: list[str] = []
        if not self.session_secret.get_secret_value():
            missing.append("ADMIN_SESSION_SECRET")
        if not self.google_client_id:
            missing.append("ADMIN_GOOGLE_CLIENT_ID")
        if not self.google_client_secret.get_secret_value():
            missing.append("ADMIN_GOOGLE_CLIENT_SECRET")
        if not self.allowed_emails and not self.staff_domain:
            missing.append("ADMIN_STAFF_EMAILS or ADMIN_STAFF_DOMAIN")
        if missing:
            raise ConfigurationError(f"sign_in=google needs {', '.join(missing)}")

    @model_validator(mode="after")
    def _environment_matches_host(self) -> Settings:
        """Refuse config whose declared environment disagrees with its host."""
        if not self.api_base_url:
            self.api_base_url = PRINCIPLE_URLS[self.environment]
        host = api_host(self.api_base_url)
        production_host = is_production_host(self.api_base_url)
        if self.environment is Environment.PRODUCTION and not production_host:
            raise ConfigurationError(
                f"environment=production but api_base_url addresses {host!r}; "
                "a production run reads and writes live patient records and must say so"
            )
        if self.environment is not Environment.PRODUCTION and production_host:
            raise ConfigurationError(
                f"environment={self.environment.value} but api_base_url addresses {host!r}, "
                "which is production. Nothing but environment=production may reach it."
            )
        return self

    def require_credentials(self) -> None:
        """Refuse a real-Principle run with no key or practice configured.

        Refusing rather than skipping: an integration run that quietly does nothing is
        indistinguishable from a passing one in a summary line.
        """
        if self.environment is Environment.FAKE:
            return
        missing = [
            f"{name}_{environment_suffix(self.environment)}"
            for name, value in (
                ("PRINCIPLE_API_KEY", self.api_key.get_secret_value()),
                ("PRINCIPLE_PRACTICE_ID", self.practice_id),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"environment={self.environment.value} needs {', '.join(missing)}"
            )

    def require_web_configured(self) -> None:
        """Validate the complete web configuration before accepting requests."""
        self.require_sign_in_configured()
        self.require_credentials()
        self.require_automation_configured()
        if not self.openai_api_key.get_secret_value():
            raise ConfigurationError("Chat needs OPENAI_API_KEY")

    def require_automation_configured(self) -> None:
        """Validate automation settings once before accepting real work."""
        if self.environment is Environment.FAKE:
            return
        if any(not getattr(self, name) for name in ENVIRONMENT_FIELDS[3:]):
            raise ConfigurationError("Automation needs scoped credentials and workspace settings")
        if not self.google_maps_api_key.get_secret_value():
            raise ConfigurationError("Automation needs ADMIN_GOOGLE_MAPS_API_KEY")
        staging = self.environment is Environment.STAGING
        if (self.firebase_project == "principle-staging") != staging:
            raise ConfigurationError("Firebase project and environment disagree")
        import re
        root_pattern = r"organisations/[A-Za-z0-9_-]+/brands/[A-Za-z0-9_-]+"
        if not re.fullmatch(root_pattern, self.firestore_root):
            raise ConfigurationError("Firestore requires an organisation/brand root")
        if not re.fullmatch(r"[a-z0-9-]+", self.workspace_slug):
            raise ConfigurationError("Invalid workspace slug")
        if not self.playwright_mcp_path.is_file():
            raise ConfigurationError("Install the locked Playwright MCP package before startup")
        if which("node") is None:
            raise ConfigurationError("Install Node.js before startup")


def current_settings(request: Request) -> Settings:
    """The configuration captured when this application starts; restart to change it."""
    configured: Settings = request.app.state.settings
    return configured
