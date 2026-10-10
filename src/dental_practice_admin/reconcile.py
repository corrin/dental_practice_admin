"""Bank reconciliation, Phase 1: every deposit, matched by hand (docs/plans/bank-reconciliation.md).

Showing the page reads only this app's database. Fetch now reads Akahu and Principle into it.
Principle is never written: a match records which payments or invoices a deposit was for, and
staff still key payments into Principle as they do today.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx2 as httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from dental_practice_admin import akahu
from dental_practice_admin.app import render, storage
from dental_practice_admin.audit import Audit
from dental_practice_admin.auth import CurrentStaff
from dental_practice_admin.config import Settings, current_settings
from dental_practice_admin.principle import CallError, PrincipleClient, PrincipleError
from dental_practice_admin.storage import MatchRefusedError, MatchRow, Storage

# Deposits are re-read from the oldest one still open, and never less than this far back.
FETCH_BACK = timedelta(days=14)
# Find & Match lists payments recorded this close to the deposit. Phase 0 found nearly every
# transfer already recorded in Principle within a week of reaching the bank.
NEAR = timedelta(days=7)
# Data older than this is shown in red: an expired bank connection is noticed the same day.
STALE = timedelta(days=2)
NAME_LOOKUPS = 8

router = APIRouter(prefix="/reconcile")
Configured = Annotated[Settings, Depends(current_settings)]
Store = Annotated[Storage, Depends(storage)]


def cents(amount: float) -> int:
    """Principle's dollar amounts as whole cents, so sums compare exactly."""
    return round(amount * 100)


async def fetch(settings: Settings, store: Storage,
                bank: httpx.AsyncBaseTransport | None = None,
                principle: httpx.AsyncBaseTransport | None = None) -> list[str]:
    """Read new deposits and Principle's changes; return what failed, also kept for the page."""
    oldest = store.oldest_open_deposit()
    since = date.today() - FETCH_BACK
    if oldest is not None:
        since = min(since, date.fromisoformat(oldest))
    problems = []
    try:
        statement = await akahu.statement(settings, since, bank)
        store.record_deposits(statement.deposits, statement.refreshed)
    except (httpx.HTTPError, akahu.AkahuError) as error:
        problems.append(f"Akahu: {error}")
    try:
        await _read_principle(settings, store, since - NEAR, principle)
    except (PrincipleError, CallError, httpx.HTTPError) as error:
        problems.append(f"Principle: {error}")
    store.record_fetch(problems)
    return problems


async def _read_principle(settings: Settings, store: Storage, since: date,
                          transport: httpx.AsyncBaseTransport | None) -> None:
    """Bring the unpaid invoices, complete payments and patient names up to date.

    Each kind is read from where the last read began, by `updatedFrom`, so a fetch takes
    seconds once the first full read is done. An invoice that is no longer unpaid, or a payment
    no longer complete, leaves the cache.
    """
    async with PrincipleClient(settings, transport) as client:
        began = datetime.now(UTC).isoformat()
        mark = store.cache_mark("invoice")
        put, drop = [], []
        async for invoice in client.rows("listInvoicesByDateRange",
                                         query={"updatedFrom": mark} if mark else {}):
            if invoice["status"] == "issued":
                put.append((invoice["id"], invoice["patientId"], invoice))
            else:
                drop.append(invoice["id"])
        store.update_cache("invoice", put, drop, began)

        mark = store.cache_mark("payment")
        start = datetime.combine(since, datetime.min.time(), akahu.NZ).isoformat()
        put, drop = [], []
        async for payment in client.rows(
                "listTransactionsByDateRange",
                query={"updatedFrom": mark} if mark else {"createdFrom": start}):
            key = f"{payment['id']}:{payment['invoiceId']}"
            if payment["type"] == "payment" and payment["status"] == "complete":
                put.append((key, payment["patientId"], payment))
            else:
                drop.append(key)
        store.update_cache("payment", put, drop, began)

        known = store.cached("patient")
        wanted = {row["patientId"] for kind in ("invoice", "payment")
                  for row in store.cached(kind).values()} - set(known)
        lookups = asyncio.Semaphore(NAME_LOOKUPS)

        async def name(patient: str) -> tuple[str, str, dict[str, Any]] | None:
            async with lookups:
                try:
                    found = await client.get("getPatient", path_params={"patientId": patient})
                except (PrincipleError, CallError):
                    # One record Principle sends in a shape its specification forbids must
                    # not stop the rest; it is retried on the next fetch.
                    return None
            return patient, patient, {"name": found["name"]}

        names = await asyncio.gather(*(name(patient) for patient in sorted(wanted)))
        store.update_cache("patient", [n for n in names if n is not None], [], None)


def _name(names: dict[str, dict[str, Any]], patient: str) -> str:
    return names[patient]["name"] if patient in names else f"Unreadable patient record {patient}"


def payment_method(payment: dict[str, Any]) -> str:
    """The method staff chose; `provider` is `manual` for every payment keyed at the desk."""
    kind = (payment.get("extendedData") or {}).get("transactionType")
    return str(kind["name"]) if kind else str(payment["provider"])


def open_items(store: Storage) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Payments no deposit covers yet, and unpaid invoices with what is still owed."""
    names = store.cached("patient")
    claimed = {f"{t}:{i}" for t, i in store.claimed_payments()}
    payments = [
        {"key": key, "patient": _name(names, p["patientId"]), "method": payment_method(p),
         "day": akahu.nz_day(p["createdAt"]), "cents": cents(p["amount"])}
        for key, p in store.cached("payment").items() if key not in claimed]
    invoices = []
    for invoice in store.cached("invoice").values():
        paid = sum(a["allocatedAmount"] for t in invoice["transactionAllocations"]
                   for a in t["allocations"])
        owed = cents(invoice["total"] - paid)
        if owed > 0:
            invoices.append({"key": invoice["id"], "patient_id": invoice["patientId"],
                             "patient": _name(names, invoice["patientId"]),
                             "reference": invoice["reference"],
                             "day": akahu.nz_day(invoice["createdAt"]), "cents": owed})
    return payments, sorted(invoices, key=lambda i: (i["patient"], i["day"]))


def local_time(stamp: str | None) -> str:
    """A stored UTC instant as NZ time for staff, or "never"."""
    if stamp is None:
        return "never"
    return datetime.fromisoformat(stamp).astimezone(akahu.NZ).strftime("%a %d %b, %I:%M %p")


def _stale(stamp: str | None) -> bool:
    return stamp is None or datetime.now(UTC) - datetime.fromisoformat(stamp) > STALE


@router.get("", response_class=HTMLResponse)
def page(request: Request, staff: CurrentStaff, configured: Configured, store: Store,
         tab: Literal["open", "matched", "excluded"] = "open") -> HTMLResponse:
    """Every deposit in one state: To reconcile, Reconciled or Excluded."""
    refreshed, read = store.bank_refreshed(), store.cache_mark("payment")
    return render(request, "reconcile.html", staff, configured, store, tab=tab,
                  deposits=store.deposits(tab), names=store.cached("patient"),
                  refreshed=local_time(refreshed), bank_stale=_stale(refreshed),
                  principle_read=local_time(read), principle_stale=_stale(read),
                  last_fetch=store.last_fetch(), local_time=local_time)


@router.post("/fetch")
async def fetch_now(staff: CurrentStaff, configured: Configured) -> dict[str, list[str]]:
    """Fetch now. The store is opened here, on the thread that uses it, as in /chatkit."""
    store = Storage(configured.database_path)
    try:
        return {"problems": await fetch(configured, store)}
    finally:
        store.close()


@router.get("/{akahu_id}", response_class=HTMLResponse)
def find_and_match(request: Request, akahu_id: str, staff: CurrentStaff,
                   configured: Configured, store: Store, q: str = "") -> HTMLResponse:
    """Xero's Find & Match: payments already in Principle first, then unpaid invoices."""
    deposit = store.deposit(akahu_id)
    if deposit is None:
        raise HTTPException(404)
    payments, invoices = open_items(store)
    banked = date.fromisoformat(deposit["date"])
    search = q.strip().lower()
    if search:
        def found(row: dict[str, Any]) -> bool:
            text = " ".join(str(v) for v in row.values()) + f" {row['cents'] / 100:.2f}"
            return search in text.lower()
        payments, invoices = [p for p in payments if found(p)], [i for i in invoices if found(i)]
    else:
        payments = [p for p in payments
                    if abs(date.fromisoformat(p["day"]) - banked) <= NEAR]
    payments.sort(key=lambda p: (p["cents"] != deposit["amount_cents"],
                                 abs(date.fromisoformat(p["day"]) - banked), p["patient"]))
    return render(request, "reconcile_match.html", staff, configured, store, deposit=deposit,
                  payments=payments, invoices=invoices, q=q)


class Ticked(BaseModel):
    """One ticked row: a payment by its key, or an invoice with the amount it is paid."""

    payment: str | None = None
    invoice: str | None = None
    cents: int


class Match(BaseModel):
    """Everything ticked on Find & Match."""

    items: list[Ticked]


def audit(settings: Settings, event: str, **values: Any) -> None:
    """Every decision on a deposit, so undoing one never loses who made it."""
    Audit(settings, "bank-reconciliation").write(event, **values)


@router.post("/{akahu_id}/match")
def match(akahu_id: str, body: Match, staff: CurrentStaff, configured: Configured,
          store: Store) -> dict[str, str]:
    """Record what a deposit was for, checked against the cache rather than the browser."""
    payments, invoices = open_items(store)
    open_payments = {p["key"]: p for p in payments}
    owed = {i["key"]: i for i in invoices}
    rows = []
    for item in body.items:
        if item.payment is not None and item.invoice is None:
            payment = open_payments.get(item.payment)
            if payment is None or payment["cents"] != item.cents:
                raise HTTPException(409, "That payment is no longer open.")
            cached = store.cached("payment")[item.payment]
            rows.append(MatchRow(patient_id=cached["patientId"], invoice_id=cached["invoiceId"],
                                 amount_cents=item.cents,
                                 principle_transaction_id=cached["id"]))
        elif item.invoice is not None and item.payment is None:
            invoice = owed.get(item.invoice)
            if invoice is None or item.cents > invoice["cents"]:
                raise HTTPException(409, "That invoice no longer owes that much.")
            rows.append(MatchRow(patient_id=invoice["patient_id"], invoice_id=item.invoice,
                                 amount_cents=item.cents, principle_transaction_id=None))
        else:
            raise HTTPException(422, "Tick a payment or an invoice.")
    try:
        store.match_deposit(akahu_id, rows, staff.email)
    except MatchRefusedError as error:
        raise HTTPException(409, str(error)) from None
    audit(configured, "deposit_matched", akahu_id=akahu_id, staff=staff.email,
          matches=[asdict(row) for row in rows])
    return {"url": "/reconcile"}


class Exclusion(BaseModel):
    """Why a deposit is not a patient payment."""

    reason: str


@router.post("/{akahu_id}/exclude")
def exclude(akahu_id: str, body: Exclusion, staff: CurrentStaff, configured: Configured,
            store: Store) -> dict[str, str]:
    """Not a patient payment, like QuickBooks' Exclude; the reason is required."""
    try:
        store.exclude_deposit(akahu_id, body.reason, staff.email)
    except MatchRefusedError as error:
        raise HTTPException(409, str(error)) from None
    audit(configured, "deposit_excluded", akahu_id=akahu_id, staff=staff.email,
          reason=body.reason)
    return {"url": "/reconcile"}


class Reopening(BaseModel):
    """The state a deposit is being taken back out of."""

    status: Literal["matched", "excluded"]


@router.post("/{akahu_id}/reopen")
def reopen(akahu_id: str, body: Reopening, staff: CurrentStaff, configured: Configured,
           store: Store) -> dict[str, str]:
    """Unreconcile a match or undo an exclusion. Principle is never touched."""
    undone = next((d for d in store.deposits(body.status) if d["akahu_id"] == akahu_id), None)
    try:
        store.reopen_deposit(akahu_id, body.status)
    except MatchRefusedError as error:
        raise HTTPException(409, str(error)) from None
    audit(configured, "deposit_reopened", akahu_id=akahu_id, staff=staff.email, undone=undone)
    return {"url": f"/reconcile?tab={body.status}"}
