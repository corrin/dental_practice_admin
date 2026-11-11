"""Staging only: how Principle takes an outbound text's status being set to failed, then sent.

    uv run python -m scripts.sms_status_probe

SMS_Bridge sets the status of each text it sends with `updateSMSMessageStatus`: `failed` when
it gives up, `sent` when the phone sends it. This creates one outbound `pending` text on the
recorder's `[TEST] Fake` patient, which carries the owner's mobile and email (ADR 0007), sets
it `failed` and then `sent`, and prints each answer's HTTP status and message status. It
prints the patient's and message's ids, to find them in the website. If staging's own SMS is
on, the text goes to the owner's mobile. docs/principle/sms-messages.md records what it showed.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx2 as httpx

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.principle import PrincipleClient

BODY = "[TEST] Fake: a message-status check from the admin app. Please ignore."


def owners(patient: dict[str, Any], settings: Settings) -> bool:
    """Whether a patient is the recorder's, carrying the owner's own mobile and email."""
    numbers = [number["number"] for number in patient.get("contactNumbers", [])]
    return ("[TEST] Fake" in patient["name"] and settings.owner_mobile in numbers
            and patient.get("email") == settings.owner_email)


async def probe() -> None:
    """Create a pending text on the owner's Fake patient, then set it failed, then sent."""
    settings = Settings(environment=Environment.STAGING)
    settings.require_credentials()
    settings.require_owner_contact()
    async with PrincipleClient(settings) as client:
        found = [patient async for patient in client.rows(
            "searchPatients", query={"name": "[TEST] Fake"})]
        mine = sorted((p for p in found if owners(p, settings)), key=lambda p: p["createdAt"])
        if not mine:
            raise SystemExit("No [TEST] Fake patient carries the owner's mobile and email; run"
                             " `uv run python -m scripts.record_principle_wire --create-patient`")
        patient = mine[-1]["id"]
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=60, headers={
            "X-API-Key": settings.api_key.get_secret_value()}) as api:
        texts = f"/v1/patients/{patient}/sms-messages"
        for direction, initial in (("outbound", "pending"), ("inbound", "sent")):
            created = await api.post(texts, json={
                "practiceId": settings.practice_id, "body": BODY, "direction": direction,
                "status": initial})
            print(f"patient {patient}: create {direction} {initial} answered"
                  f" {created.status_code} {created.text[:300]}")
            if created.is_success:
                break
        else:
            return
        message = created.json()["id"]
        for status in ("failed", "sent"):
            async with PrincipleClient(settings) as client:
                again = await client.call("getPatient", {"patientId": patient})
            if not owners(again, settings):
                raise SystemExit(f"{patient} no longer carries the owner's details; stopped")
            answer = await api.patch(f"{texts}/{message}/status", json={"status": status})
            read = await api.get(f"{texts}/{message}")
            print(f"  set {status}: answered {answer.status_code} "
                  f"{answer.json().get('status') if answer.is_success else answer.text[:300]},"
                  f" reads back {read.json().get('status')}")

if __name__ == "__main__":
    asyncio.run(probe())
