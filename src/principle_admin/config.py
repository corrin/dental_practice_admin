"""Which Principle we are talking to, and where our own data lives.

Three environments, and the difference between them is the whole safety story:

    fake        the local simulation (tests/fake_principle.py) -- no network at all
    staging     api.staging.principle.dental -- a migrated copy of the real practice
    production  api.principle.dental -- live patient records

`environment` is declared, not inferred, because the interesting mistake is config that
claims to be staging while addressing production. `Settings` cross-checks the declared
environment against the host actually configured and refuses a mismatch either way.

Environment variable names match SMS_Bridge and od_data (`PRINCIPLE_API_BASE_URL`,
`PRINCIPLE_API_KEY`, `PRINCIPLE_PRACTICE_ID`) so an existing .env carries over.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request

PRODUCTION_API_HOSTS = frozenset({"api.principle.dental", "app.principle.dental"})

STAGING_API_URL = "https://api.staging.principle.dental"

# The fake answers in-process; the URL exists only so httpx can build a request, and the
# host makes a stray real call obvious in a log or a traceback.
FAKE_API_URL = "https://fake.principle.invalid"


class Environment(StrEnum):
    """Which Principle a process is addressing."""

    FAKE = "fake"
    STAGING = "staging"
    PRODUCTION = "production"


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


class Settings(BaseSettings):
    """Resolved runtime configuration for one process."""

    model_config = SettingsConfigDict(
        env_prefix="PRINCIPLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.FAKE
    api_base_url: str = FAKE_API_URL
    api_key: SecretStr = SecretStr("")
    practice_id: str = ""

    # Staff sign-in. Google holds the credentials; this application holds only the list of
    # people allowed in, so there is no password store to leak or reset.
    #
    # `developer` is the default so the test suite and a local run need no OAuth round-trip. It is
    # refused outright against production, below: there is no configuration in which live patient
    # records are served to an unauthenticated visitor.
    sign_in: SignIn = SignIn.DEVELOPER
    session_secret: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")

    # Either mechanism admits a user: a named address, or any address at a Workspace domain.
    # Both empty means nobody, which is why production refuses to start that way.
    #
    # A plain comma-separated string, not a set. pydantic-settings JSON-decodes complex types from
    # environment and dotenv sources before any validator runs, so a `frozenset` field could only
    # ever be set from Python -- PRINCIPLE_STAFF_EMAILS=someone@example.com failed to parse and the
    # application refused to start. Use `allowed_emails` to read it.
    staff_emails: str = ""
    staff_domain: str = ""

    openai_api_key: SecretStr = SecretStr("")
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
    data_root: Path = Field(default=Path.home() / "principle_admin_data")

    @property
    def data_dir(self) -> Path:
        """Per-environment directory for the database, logs and browser session state."""
        return self.data_root / self.environment.value

    @property
    def database_path(self) -> Path:
        """SQLite file holding run history and conversations."""
        return self.data_dir / "principle_admin.db"

    @property
    def browser_state_path(self) -> Path:
        """Playwright storage_state for the automation-owned Principle session."""
        return self.data_dir / "principle.storage_state.json"

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
        if self.environment is Environment.PRODUCTION and self.sign_in is not SignIn.GOOGLE:
            raise ConfigurationError(
                f"environment=production with sign_in={self.sign_in.value}: live patient records"
                " would be served to anyone who found the address. Production requires Google."
            )
        if self.sign_in is not SignIn.GOOGLE:
            return
        missing: list[str] = []
        if not self.session_secret.get_secret_value():
            missing.append("PRINCIPLE_SESSION_SECRET")
        if not self.google_client_id:
            missing.append("PRINCIPLE_GOOGLE_CLIENT_ID")
        if not self.google_client_secret.get_secret_value():
            missing.append("PRINCIPLE_GOOGLE_CLIENT_SECRET")
        if not self.allowed_emails and not self.staff_domain:
            missing.append("PRINCIPLE_STAFF_EMAILS or PRINCIPLE_STAFF_DOMAIN")
        if missing:
            raise ConfigurationError(f"sign_in=google needs {', '.join(missing)}")

    @model_validator(mode="after")
    def _environment_matches_host(self) -> Settings:
        """Refuse config whose declared environment disagrees with its host."""
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
            name
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


def current_settings() -> Settings:
    """Configuration for one request.

    A FastAPI dependency so tests can override it. Anything reading `app.state` instead would
    bypass that override and quietly test a different configuration from the one it set up.
    """
    return Settings()
