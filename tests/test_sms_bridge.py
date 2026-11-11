"""The SMS bridge tells a task which patients' texts never went, and whether the phone is up."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin.config import ConfigurationError, Settings
from dental_practice_admin.sms_bridge import SmsBridge
from tests.fake_sms_bridge import FakeSmsBridge, failure, transport
from tests.settings import fake_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return fake_settings(tmp_path)


async def test_every_text_the_bridge_gave_up_on_is_listed_with_its_patient(
        settings: Settings) -> None:
    bridge = FakeSmsBridge([failure("message-a", "patient-a", "+64210000001", 2,
                                    reason="phone Unknown for 4h"),
                            failure("message-b", None, "+64210000002", 1)])
    async with SmsBridge(settings, transport(bridge)) as client:
        failed = await client.failed_texts()
    assert [(f.principle_message_id, f.patient_id, f.patient_phone_number) for f in failed] == [
        ("message-a", "patient-a", "+64210000001"), ("message-b", None, "+64210000002")]
    first = failed[0]
    assert (first.attempts, first.reason) == (3, "phone Unknown for 4h")
    assert first.received_at < first.failed_at < datetime.now(UTC)


async def test_no_failures_is_an_empty_list(settings: Settings) -> None:
    async with SmsBridge(settings, transport(FakeSmsBridge([]))) as client:
        assert await client.failed_texts() == []


async def test_a_phone_that_is_down_says_so_and_why(settings: Settings) -> None:
    bridge = FakeSmsBridge(up=False, detail="Call Centre: Connected, phone: Unknown")
    async with SmsBridge(settings, transport(bridge)) as client:
        status = await client.phone_status()
    assert (status.up, status.detail) == (False, "Call Centre: Connected, phone: Unknown")


@pytest.mark.parametrize("missing", ["sms_bridge_url", "sms_bridge_api_key"])
async def test_without_the_bridges_settings_it_is_not_asked_and_the_refusal_names_them(
        settings: Settings, missing: str) -> None:
    unset = settings.model_copy(update={
        missing: SecretStr("") if missing == "sms_bridge_api_key" else ""})
    async with SmsBridge(unset, transport()) as client:
        with pytest.raises(ConfigurationError, match="ADMIN_SMS_BRIDGE_API_KEY"):
            await client.failed_texts()
        with pytest.raises(ConfigurationError, match="ADMIN_SMS_BRIDGE_URL"):
            await client.phone_status()


async def test_a_refused_key_raises(settings: Settings) -> None:
    wrong = settings.model_copy(update={"sms_bridge_api_key": SecretStr("revoked")})
    async with SmsBridge(wrong, transport()) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.failed_texts()
