"""The reviewed daily diary, callable without a model."""
from datetime import date
from typing import Any

from dental_practice_admin.scripts import Services
from dental_practice_admin.tasks import daily_diary


async def run(services: Services, inputs: dict[str, Any]) -> dict[str, Any]:
    """Report a practice-local day using the shared API and pagination rules."""
    report = await daily_diary(services.api, date.fromisoformat(inputs["date"]))
    return {"summary": report.summary(), "coverage": report.coverage.value,
            "detail": report.as_detail()}
