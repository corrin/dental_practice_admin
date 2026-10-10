"""The fake Akahu bank account, over the real transport."""

from typing import Any

from pydantic import SecretStr

from dental_practice_admin.config import Settings
from tests.fake_akahu.server import (
    FAKE_AKAHU_ACCOUNT_ID,
    FAKE_AKAHU_APP_TOKEN,
    FAKE_AKAHU_USER_TOKEN,
    FakeAkahu,
    app,
    seed,
    transaction,
    transport,
)

# Settings for the fake bank: by field, for Settings(...) and model_copy, and by environment
# variable, for child processes.
FAKE_AKAHU_SETTINGS: dict[str, Any] = {"akahu_app_token": SecretStr(FAKE_AKAHU_APP_TOKEN),
                       "akahu_user_token": SecretStr(FAKE_AKAHU_USER_TOKEN),
                       "akahu_account_id": FAKE_AKAHU_ACCOUNT_ID}
FAKE_AKAHU_ENV = {
    str(Settings.model_fields[name].validation_alias):
        value.get_secret_value() if isinstance(value, SecretStr) else value
    for name, value in FAKE_AKAHU_SETTINGS.items()}

__all__ = ["FAKE_AKAHU_ACCOUNT_ID", "FAKE_AKAHU_APP_TOKEN", "FAKE_AKAHU_ENV",
           "FAKE_AKAHU_SETTINGS",
           "FAKE_AKAHU_USER_TOKEN", "FakeAkahu", "app", "seed", "transaction", "transport"]
