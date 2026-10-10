"""Bank reconciliation: deposits from Akahu, matched by hand against Principle."""
from __future__ import annotations

from datetime import date, timedelta

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin import akahu
from dental_practice_admin.config import ConfigurationError, Environment, Settings, SignIn
from tests.fake_akahu import (
    FAKE_AKAHU_ENV,
    FAKE_AKAHU_SETTINGS,
    FakeAkahu,
    transaction,
    transport,
)


@pytest.fixture
def bank_settings() -> Settings:
    return Settings(environment=Environment.FAKE, **FAKE_AKAHU_SETTINGS)


async def test_statement_has_every_deposit_and_no_payments_out(bank_settings: Settings) -> None:
    bank = FakeAkahu()
    found = await akahu.statement(bank_settings, date.today() - timedelta(days=14),
                                  transport(bank))
    assert [d.akahu_id for d in found.deposits] == [
        "trans_fake_transfer", "trans_fake_card", "trans_fake_cheque"]
    transfer = found.deposits[0]
    assert transfer.date == (date.today() - timedelta(days=3)).isoformat()
    assert transfer.amount_cents == 18500
    assert (transfer.particulars, transfer.code, transfer.reference) == ("SMITH", None, "LILY")
    assert found.refreshed == bank.refreshed


async def test_statement_starts_at_the_day_asked_for(bank_settings: Settings) -> None:
    bank = FakeAkahu([transaction("old", 20, 10.0, "OLD"), transaction("new", 2, 20.0, "NEW")])
    found = await akahu.statement(bank_settings, date.today() - timedelta(days=14),
                                  transport(bank))
    assert [d.akahu_id for d in found.deposits] == ["new"]


async def test_refused_credentials_raise(bank_settings: Settings) -> None:
    wrong = bank_settings.model_copy(update={"akahu_user_token": SecretStr("revoked")})
    with pytest.raises(httpx.HTTPStatusError):
        await akahu.statement(wrong, date.today(), transport())


def test_the_bank_settings_use_akahus_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FAKE_AKAHU_ENV.items():
        monkeypatch.setenv(name, value)
    assert Settings().model_dump(include=set(FAKE_AKAHU_SETTINGS)) == FAKE_AKAHU_SETTINGS


@pytest.mark.parametrize("missing", sorted(FAKE_AKAHU_SETTINGS))
def test_the_application_refuses_to_start_without_the_bank(missing: str) -> None:
    configured = Settings(environment=Environment.FAKE, sign_in=SignIn.DEVELOPER,
                          openai_api_key=SecretStr("fake-ai-key"),
                          **{k: v for k, v in FAKE_AKAHU_SETTINGS.items() if k != missing})
    with pytest.raises(ConfigurationError):
        configured.require_web_configured()
