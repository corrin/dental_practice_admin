"""A fake Akahu: the practice's bank account, reached through the real transport.

One implementation, two ways in, like `tests/fake/`:

    transport()                          an httpx transport for in-process tests
    uvicorn tests.fake_akahu.server:app  a real HTTP server, for the application as a separate
                                         process pointed at it by AKAHU_BASE_URL

Shapes are the ones the practice's account returned on 2026-10-10: `items` and `cursor.next`
(null on the last page), `meta` holding only the fields the payer filled in, `date` at NZ
midnight in UTC, and `refreshed` on each account in `/accounts`. Pages are two rows, so
every walk pages.
Dates are relative to today, so a fetch of "the last fortnight" always finds the seed.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx2 as httpx

FAKE_AKAHU_APP_TOKEN = "app_token_fake"
FAKE_AKAHU_USER_TOKEN = "user_token_fake"
FAKE_AKAHU_ACCOUNT_ID = "acc_fake0000000000000000001"
PAGE_SIZE = 2
NZ = ZoneInfo("Pacific/Auckland")


class FakeAkahuUnhandledRequestError(NotImplementedError):
    """A call the fake bank has no answer for: add it, rather than guessing."""


def _stamp(day: date) -> str:
    """Akahu's `date`: NZ midnight, written in UTC with milliseconds and `Z`."""
    midnight = datetime.combine(day, datetime.min.time(), NZ).astimezone(UTC)
    return midnight.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def transaction(ident: str, days_ago: int, amount: float, description: str,
                **meta: str) -> dict[str, Any]:
    """One account transaction in Akahu's shape; a negative amount is money out."""
    day = date.today() - timedelta(days=days_ago)
    return {"_id": ident, "_account": FAKE_AKAHU_ACCOUNT_ID, "date": _stamp(day),
            "description": description, "amount": amount, "type": "DIRECT CREDIT",
            "meta": meta}


def seed() -> list[dict[str, Any]]:
    """A few days of the account: a transfer, a card settlement, a debit and a one-cent check."""
    return [
        transaction("trans_fake_transfer", 3, 185.0, "FAKE PAYER SMITH",
                    particulars="SMITH", reference="LILY"),
        transaction("trans_fake_card", 2, 1980.0, "Direct Credit 4621 SmartpaySett 051",
                    particulars="4621", code="SmartpaySett", reference="051"),
        transaction("trans_fake_debit", 1, -42.5, "FAKE SUPPLIER"),
        transaction("trans_fake_cheque", 1, 0.01, "FAKE ACCOUNT CHECK"),
    ]


class FakeAkahu:
    """ASGI application over a mutable list of transactions."""

    def __init__(self, transactions: list[dict[str, Any]] | None = None) -> None:
        self.transactions = seed() if transactions is None else transactions
        self.refreshed = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.accounts = [{
            "_id": FAKE_AKAHU_ACCOUNT_ID, "name": "FAKE DAY TO DAY", "status": "ACTIVE",
            "refreshed": {"balance": self.refreshed, "meta": self.refreshed,
                          "transactions": self.refreshed}}]

    def answer(self, path: str, query: dict[str, str], headers: dict[str, str]) -> tuple[int, Any]:
        if (headers.get("authorization") != f"Bearer {FAKE_AKAHU_USER_TOKEN}"
                or headers.get("x-akahu-id") != FAKE_AKAHU_APP_TOKEN):
            return 401, {"success": False}
        if path == "/accounts":
            return 200, {"success": True, "items": self.accounts}
        if (path != f"/accounts/{FAKE_AKAHU_ACCOUNT_ID}/transactions"
                or set(query) - {"start", "cursor"}):
            raise FakeAkahuUnhandledRequestError(f"no answer for {path} {sorted(query)}")
        start = datetime.fromisoformat(query["start"])
        rows = sorted((t for t in self.transactions
                       if datetime.fromisoformat(t["date"]) >= start),
                      key=lambda t: (t["date"], t["_id"]))
        offset = int(query.get("cursor", "0"))
        page = rows[offset:offset + PAGE_SIZE]
        following = offset + PAGE_SIZE
        return 200, {"success": True, "items": page,
                     "cursor": {"next": str(following) if following < len(rows) else None}}

    async def __call__(self, scope: MutableMapping[str, Any],
                       receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
                       send: Callable[[MutableMapping[str, Any]], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            raise FakeAkahuUnhandledRequestError(f"serves http, not {scope['type']!r}")
        status, document = self.answer(
            scope["path"].removeprefix("/v1"),
            {k: v[0] for k, v in parse_qs(scope.get("query_string", b"").decode()).items()},
            {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])})
        body = json.dumps(document).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


def transport(bank: FakeAkahu | None = None) -> httpx.ASGITransport:
    """The fake bank installed where the socket would be."""
    return httpx.ASGITransport(app=bank if bank is not None else FakeAkahu())


app = FakeAkahu()
