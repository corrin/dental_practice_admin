"""Bank reconciliation: deposits from Akahu, matched by hand against Principle."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin import akahu
from dental_practice_admin.config import ConfigurationError, Environment, Settings, SignIn
from dental_practice_admin.storage import MatchRefusedError, MatchRow, Storage
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


def _deposit(ident: str, cents: int) -> akahu.Deposit:
    return akahu.Deposit(akahu_id=ident, date="2026-10-01", amount_cents=cents,
                         description="FAKE PAYER", particulars=None, code=None, reference=None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Storage]:
    opened = Storage(tmp_path / "bank.db")
    opened.record_deposits([_deposit("dep-1", 18500), _deposit("dep-2", 3000)], "2026-10-01")
    yield opened
    opened.close()


def _payment(cents: int, ident: str = "pay-1", invoice: str = "inv-1") -> MatchRow:
    return MatchRow(patient_id="pat-1", invoice_id=invoice, amount_cents=cents,
                    principle_transaction_id=ident)


def test_fetching_again_neither_duplicates_nor_reopens_a_deposit(store: Storage) -> None:
    store.match_deposit("dep-1", [_payment(18500)], "staff@fake.invalid")
    store.record_deposits([_deposit("dep-1", 18500)], "2026-10-02")
    assert [d["akahu_id"] for d in store.deposits("matched")] == ["dep-1"]
    assert [d["akahu_id"] for d in store.deposits("open")] == ["dep-2"]
    assert store.bank_refreshed() == "2026-10-02"


@pytest.mark.parametrize("rows", [
    [_payment(18000)],
    [_payment(18500), _payment(100, "pay-2")],
    [MatchRow(patient_id="pat-1", invoice_id="inv-1", amount_cents=0,
              principle_transaction_id=None), _payment(18500)],
    [],
])
def test_a_match_must_add_up_to_the_deposit_exactly(store: Storage, rows: list[MatchRow]) -> None:
    with pytest.raises(MatchRefusedError):
        store.match_deposit("dep-1", rows, "staff@fake.invalid")
    assert store.deposit("dep-1")["status"] == "open"  # type: ignore[index]


def test_a_split_invoice_and_a_payment_can_make_up_one_deposit(store: Storage) -> None:
    store.match_deposit("dep-1", [
        _payment(15500),
        MatchRow(patient_id="pat-2", invoice_id="inv-2", amount_cents=3000,
                 principle_transaction_id=None)], "staff@fake.invalid")
    assert store.deposit("dep-1")["status"] == "matched"  # type: ignore[index]


def test_one_payment_cannot_cover_two_deposits(store: Storage) -> None:
    store.match_deposit("dep-2", [_payment(3000)], "staff@fake.invalid")
    with pytest.raises(MatchRefusedError):
        store.match_deposit("dep-1", [_payment(18500)], "staff@fake.invalid")


@pytest.mark.parametrize("reason", ["", "   "])
def test_exclude_needs_a_reason(store: Storage, reason: str) -> None:
    with pytest.raises(MatchRefusedError):
        store.exclude_deposit("dep-1", reason, "staff@fake.invalid")
    assert store.deposit("dep-1")["status"] == "open"  # type: ignore[index]


def test_unreconcile_frees_the_payment_and_reopens_the_deposit(store: Storage) -> None:
    store.match_deposit("dep-1", [_payment(18500)], "staff@fake.invalid")
    store.reopen_deposit("dep-1", "matched")
    assert store.deposit("dep-1")["status"] == "open"  # type: ignore[index]
    assert not store.claimed_payments()
    with pytest.raises(MatchRefusedError):
        store.reopen_deposit("dep-1", "matched")


def test_a_decided_deposit_keeps_the_amount_it_was_decided_on(store: Storage) -> None:
    store.match_deposit("dep-1", [_payment(18500)], "staff@fake.invalid")
    store.record_deposits([_deposit("dep-1", 99900)], "2026-10-02")
    assert store.deposit("dep-1")["amount_cents"] == 18500  # type: ignore[index]
