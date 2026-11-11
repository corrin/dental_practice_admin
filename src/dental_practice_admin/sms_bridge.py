"""The practice's SMS bridge (github.com/corrin/SMS_Bridge), which sends Principle's texts.

Principle hands each outbound text to the bridge, which sends it from the practice phone. It
retries a text that fails, and keeps one it has given up on until a later text to the same
number goes through. Both calls need the bridge's `BRIDGE_API_KEY` as `X-API-Key`.
"""
from __future__ import annotations

from types import TracebackType

import httpx2 as httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from dental_practice_admin.audit import observed
from dental_practice_admin.config import Settings


class FailedText(BaseModel):
    """A text to a patient the bridge gave up sending."""

    model_config = ConfigDict(alias_generator=to_camel, frozen=True)

    principle_message_id: str
    # None when no Principle patient has the number.
    patient_id: str | None
    patient_phone_number: str
    received_at: AwareDatetime
    failed_at: AwareDatetime
    attempts: int
    reason: str


class _FailedTexts(BaseModel):
    failed: list[FailedText]


class PhoneStatus(BaseModel):
    """Whether texts can go out now: the phone, Call Centre, and the bridge not stuck."""

    model_config = ConfigDict(frozen=True)

    up: bool
    detail: str


class SmsBridge:
    """The bridge's two reads. HTTP failures propagate."""

    def __init__(self, settings: Settings,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(
            base_url=settings.sms_bridge_url, transport=transport, timeout=60,
            headers={"X-API-Key": settings.sms_bridge_api_key.get_secret_value()})

    @observed("sms_bridge.failed_texts")
    async def failed_texts(self) -> list[FailedText]:
        """Every text given up on that no later text to the same number has made up for."""
        response = (await self.client.get("/smsgateway/failed-sms")).raise_for_status()
        return _FailedTexts.model_validate_json(response.content).failed

    @observed("sms_bridge.phone_status")
    async def phone_status(self) -> PhoneStatus:
        """The phone's state, read live by the bridge."""
        response = (await self.client.get("/smsgateway/phone-status")).raise_for_status()
        return PhoneStatus.model_validate_json(response.content)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> SmsBridge:
        return self

    async def __aexit__(self, kind: type[BaseException] | None, error: BaseException | None,
                        traceback: TracebackType | None) -> None:
        await self.aclose()

