"""A fake SMS bridge: the texts it gave up on, and the phone, reached through the real transport.

One implementation, two ways in, like `tests/fake_akahu/`:

    transport()                               an httpx transport for in-process tests
    uvicorn tests.fake_sms_bridge.server:app  a real HTTP server, for a process pointed at it
                                              by ADMIN_SMS_BRIDGE_URL

Shapes are SMS_Bridge's (github.com/corrin/SMS_Bridge): `/smsgateway/failed-sms` answers
`{"failed": [...]}` with only the failures no later text to the same number has cleared, and
`/smsgateway/phone-status` answers `{"up": bool, "detail": str}`. Both need `X-API-Key`.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

FAKE_SMS_BRIDGE_API_KEY = "sms_bridge_key_fake"


class FakeSmsBridgeUnhandledRequestError(NotImplementedError):
    """A call the fake bridge has no answer for: add it, rather than guessing."""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def failure(message_id: str, patient_id: str | None, phone: str, hours_ago: float,
            reason: str = "phone Unknown for 4h") -> dict[str, Any]:
    """One text the bridge gave up on, in its shape; received an hour before it failed."""
    failed = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {"principleMessageId": message_id, "patientId": patient_id,
            "patientPhoneNumber": phone, "receivedAt": _stamp(failed - timedelta(hours=1)),
            "failedAt": _stamp(failed), "attempts": 3, "reason": reason}


def seed() -> list[dict[str, Any]]:
    """A failure for a patient of the fake Principle, and one for a number with no patient."""
    return [failure("fake-sms-001", "fake-patient-001", "+64210000001", 2),
            failure("fake-sms-002", None, "+64210000099", 1)]


class FakeSmsBridge:
    """ASGI application over a mutable list of failures and the phone's state."""

    def __init__(self, failed: list[dict[str, Any]] | None = None, up: bool = True,
                 detail: str = "Call Centre: Connected, phone: Connected") -> None:
        self.failed = seed() if failed is None else failed
        self.up = up
        self.detail = detail

    def answer(self, path: str, headers: dict[str, str]) -> tuple[int, Any]:
        if headers.get("x-api-key") != FAKE_SMS_BRIDGE_API_KEY:
            return 401, {"title": "Unauthorized"}
        if path == "/smsgateway/failed-sms":
            return 200, {"failed": self.failed}
        if path == "/smsgateway/phone-status":
            return 200, {"up": self.up, "detail": self.detail}
        raise FakeSmsBridgeUnhandledRequestError(f"no answer for {path}")

    async def __call__(self, scope: MutableMapping[str, Any],
                       receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
                       send: Callable[[MutableMapping[str, Any]], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            raise FakeSmsBridgeUnhandledRequestError(f"serves http, not {scope['type']!r}")
        status, document = self.answer(
            scope["path"], {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])})
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps(document).encode()})


def transport(bridge: FakeSmsBridge | None = None) -> httpx.ASGITransport:
    """The fake bridge installed where the socket would be."""
    return httpx.ASGITransport(app=bridge if bridge is not None else FakeSmsBridge())


app = FakeSmsBridge()
