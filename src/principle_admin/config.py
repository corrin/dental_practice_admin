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

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
