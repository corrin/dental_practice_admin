"""Bank reconciliation: deposits from Akahu, matched by hand against Principle."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from dental_practice_admin import akahu, reconcile
from dental_practice_admin.app import create_app
from dental_practice_admin.config import ConfigurationError, Settings, load_settings
from dental_practice_admin.storage import MatchRefusedError, MatchRow, Storage
from tests.fake import FakeStore, seed
from tests.fake import transport as principle_transport
from tests.fake.store import canonical
from tests.fake_akahu import (
    FAKE_AKAHU_ENV,
    FAKE_AKAHU_SETTINGS,
    FakeAkahu,
    transaction,
    transport,
)
from tests.fake_akahu import transport as akahu_transport
from tests.settings import fake_environment, fake_settings


@pytest.fixture
def bank_settings(tmp_path: Path) -> Settings:
    return fake_settings(tmp_path)


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


async def test_more_than_one_active_account_is_refused(bank_settings: Settings) -> None:
    bank = FakeAkahu()
    bank.accounts.append({**bank.accounts[0], "_id": "acc_fake_other"})
    with pytest.raises(akahu.AkahuError):
        await akahu.statement(bank_settings, date.today(), transport(bank))


async def test_refused_credentials_raise(bank_settings: Settings) -> None:
    wrong = bank_settings.model_copy(update={"akahu_user_token": SecretStr("revoked")})
    with pytest.raises(httpx.HTTPStatusError):
        await akahu.statement(wrong, date.today(), transport())


def test_the_bank_settings_use_akahus_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    for name, value in (fake_environment(tmp_path) | FAKE_AKAHU_ENV).items():
        monkeypatch.setenv(name, value)
    assert load_settings().model_dump(include=set(FAKE_AKAHU_SETTINGS)) == FAKE_AKAHU_SETTINGS


@pytest.mark.parametrize("missing", sorted(FAKE_AKAHU_SETTINGS))
def test_the_application_refuses_to_start_without_the_bank(
    missing: str, tmp_path: Path,
) -> None:
    configured = fake_settings(tmp_path, **{missing: SecretStr("")})
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


@pytest.fixture
def practice(tmp_path: Path) -> Settings:
    return fake_settings(tmp_path)


@pytest.fixture
def principle() -> Iterator[FakeStore]:
    seeded = seed()
    yield seeded
    seeded.close()


async def _fetch(settings: Settings, principle: FakeStore,
                 bank: FakeAkahu | None = None) -> list[str]:
    store = Storage(settings.database_path)
    try:
        return await reconcile.fetch(settings, store, akahu_transport(bank),
                                     principle_transport(principle))
    finally:
        store.close()


async def test_fetch_reads_deposits_payments_unpaid_invoices_and_names(
    practice: Settings, principle: FakeStore,
) -> None:
    assert await _fetch(practice, principle) == []
    store = Storage(practice.database_path)
    payments, invoices = reconcile.open_items(store)
    assert len(store.deposits("open")) == 3
    assert {(p["patient"], p["method"], p["cents"]) for p in payments} == {
        ("Lily Smith", "Direct Deposit", 18500),
        ("Card One", "Credit Card", 120000), ("Card Two", "Credit Card", 78000)}
    assert {(i["key"], i["cents"]) for i in invoices} == {("tom-1", 9000), ("spaced-1", 6000)}
    assert next(i for i in invoices if i["key"] == "spaced-1")["patient"].startswith(
        "Unreadable")
    assert store.last_fetch()[1] == []  # type: ignore[index]
    store.close()


async def test_a_paid_invoice_leaves_on_the_next_fetch(
    practice: Settings, principle: FakeStore,
) -> None:
    await _fetch(practice, principle)
    principle.db.execute("UPDATE invoices SET status = 'paid', paid = total,"
                         " updated_at = :now WHERE id = 'tom-1'",
                         {"now": canonical(datetime.now(UTC).isoformat())})
    await _fetch(practice, principle)
    store = Storage(practice.database_path)
    assert {i["key"] for i in reconcile.open_items(store)[1]} == {"spaced-1"}
    store.close()


async def test_a_failed_bank_read_is_kept_for_the_page(
    practice: Settings, principle: FakeStore,
) -> None:
    revoked = practice.model_copy(update={"akahu_user_token": SecretStr("revoked")})
    problems = await _fetch(revoked, principle)
    assert [p.split(":")[0] for p in problems] == ["Akahu"]
    store = Storage(practice.database_path)
    assert store.last_fetch()[1] == problems  # type: ignore[index]
    store.close()


@pytest.fixture
async def web(practice: Settings, principle: FakeStore) -> AsyncIterator[TestClient]:
    await _fetch(practice, principle)
    with TestClient(create_app(practice)) as client:
        yield client


def test_find_and_match_offers_the_recorded_payment_first(web: TestClient) -> None:
    page = web.get("/reconcile/trans_fake_transfer").text
    assert page.index("Lily Smith") < page.index("Card One")


def test_a_deposit_is_matched_to_a_payment_already_in_principle(web: TestClient) -> None:
    response = web.post("/reconcile/trans_fake_transfer/match", json={"items": [
        {"payment": "pay-lily:lily-1", "cents": 18500}]})
    assert response.status_code == 200
    assert "trans_fake_transfer" in web.get("/reconcile?tab=matched").text
    assert "Lily Smith" not in web.get("/reconcile/trans_fake_card").text


# Each adds up to its deposit, so only the check named is left to refuse it.
@pytest.mark.parametrize(("deposit", "items"), [
    ("trans_fake_cheque", [{"payment": "pay-lily:lily-1", "cents": 1}]),  # not its amount
    ("trans_fake_transfer", [{"invoice": "tom-1", "cents": 18500}]),  # more than is owed
    ("trans_fake_transfer", [{"payment": "no-such:payment", "cents": 18500}]),  # unknown
    ("trans_fake_transfer",
     [{"payment": "pay-lily:lily-1", "invoice": "tom-1", "cents": 18500}]),  # ambiguous
])
def test_a_match_the_cache_does_not_support_is_refused(
    web: TestClient, deposit: str, items: list[dict[str, object]],
) -> None:
    response = web.post(f"/reconcile/{deposit}/match", json={"items": items})
    assert response.status_code in {409, 422}
    assert deposit in web.get("/reconcile").text


def test_a_split_invoice_matches_part_of_what_is_owed(web: TestClient) -> None:
    response = web.post("/reconcile/trans_fake_cheque/match", json={"items": [
        {"invoice": "tom-1", "cents": 1}]})
    assert response.status_code == 200


def test_exclude_needs_a_reason_and_undo_restores(web: TestClient) -> None:
    assert web.post("/reconcile/trans_fake_cheque/exclude",
                    json={"reason": " "}).status_code == 409
    assert web.post("/reconcile/trans_fake_cheque/exclude",
                    json={"reason": "Bank account check"}).status_code == 200
    assert "Bank account check" in web.get("/reconcile?tab=excluded").text
    assert web.post("/reconcile/trans_fake_cheque/reopen",
                    json={"status": "excluded"}).status_code == 200
    assert "trans_fake_cheque" in web.get("/reconcile").text


def test_undoing_a_match_keeps_who_made_it_in_the_audit(
    web: TestClient, practice: Settings,
) -> None:
    web.post("/reconcile/trans_fake_transfer/match", json={"items": [
        {"payment": "pay-lily:lily-1", "cents": 18500}]})
    web.post("/reconcile/trans_fake_transfer/reopen", json={"status": "matched"})
    events = [json.loads(line) for line in (
        practice.data_dir / "audits" / "bank-reconciliation.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["deposit_matched", "deposit_reopened"]
    assert events[1]["undone"]["decided_by"] == events[0]["staff"]
