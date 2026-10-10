"""The practice's bank account, read through Akahu (NZ open banking).

Requests follow a working daily sync (github.com/corrin/akahu_to_budget,
modules/transaction_handler.py): the user token as bearer, the app token as `X-Akahu-ID`,
and transactions paged by `cursor.next`. Field meanings were checked against the practice's
account in Phase 0 of docs/plans/bank-reconciliation.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx

from dental_practice_admin.config import Settings

NZ = ZoneInfo("Pacific/Auckland")


@dataclass(frozen=True)
class Deposit:
    """One credit to the account, as the bank sent it."""

    akahu_id: str
    date: str
    amount_cents: int
    description: str
    particulars: str | None
    code: str | None
    reference: str | None


@dataclass(frozen=True)
class Statement:
    """The deposits since a day, and when Akahu last read the account from the bank."""

    refreshed: str
    deposits: list[Deposit]


def nz_day(stamp: str) -> str:
    """The NZ calendar day of a UTC timestamp.

    Akahu's `date` is NZ midnight in UTC: `2026-09-01T12:00:00.000Z` is 2 September.
    """
    return datetime.fromisoformat(stamp).astimezone(NZ).date().isoformat()


def _deposit(row: dict[str, Any]) -> Deposit:
    # `meta` has only the fields the payer filled in; any of the three may be absent.
    meta = row["meta"]
    return Deposit(akahu_id=row["_id"], date=nz_day(row["date"]),
                   amount_cents=round(row["amount"] * 100), description=row["description"],
                   particulars=meta.get("particulars"), code=meta.get("code"),
                   reference=meta.get("reference"))


async def statement(settings: Settings, since: date,
                    transport: httpx.AsyncBaseTransport | None = None) -> Statement:
    """Every deposit dated `since` or later, oldest first. HTTP failures propagate."""
    headers = {"Authorization": f"Bearer {settings.akahu_user_token.get_secret_value()}",
               "X-Akahu-ID": settings.akahu_app_token.get_secret_value()}
    account = f"/accounts/{settings.akahu_account_id}"
    start = datetime.combine(since, datetime.min.time(), NZ).isoformat()
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=settings.akahu_base_url, headers=headers,
                                 transport=transport, timeout=60) as client:
        refreshed = (await client.get(account)).raise_for_status().json()["item"]["refreshed"]
        params = {"start": start}
        while True:
            page = (await client.get(f"{account}/transactions", params=params)
                    ).raise_for_status().json()
            rows += page["items"]
            cursor = page["cursor"]["next"]
            if not page["items"] or cursor is None:
                break
            params["cursor"] = cursor
    deposits = sorted((_deposit(row) for row in rows if row["amount"] > 0),
                      key=lambda d: (d.date, d.akahu_id))
    return Statement(refreshed=refreshed["transactions"], deposits=deposits)
