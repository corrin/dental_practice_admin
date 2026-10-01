"""The same contract, asserted against the real staging API.

This is the tier that makes a green fake run mean something. The fake proves the
application against our model of Principle; only these tests prove the model. They reach
`api.staging.principle.dental`, and they refuse rather than skip when unconfigured.

Run with `uv run pytest -m integration` (or scripts/run_integration_tests.ps1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from dental_practice_admin.config import Settings
from dental_practice_admin.principle import PrincipleClient, PrincipleError

pytestmark = pytest.mark.integration

WINDOW_DAYS = 7


def _window() -> dict[str, str]:
    """A week around today, formatted as the spec's date-time parameters."""
    now = datetime.now(tz=UTC)
    return {
        "from": (now - timedelta(days=WINDOW_DAYS)).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=WINDOW_DAYS)).isoformat().replace("+00:00", "Z"),
    }


async def test_api_key_is_accepted_and_practices_are_listed(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """Proves the auth header, the base URL and the envelope shape against the real API.

    The fake cannot prove any of these: it accepts the header because we told it to.
    """
    envelope = await staging_client.get("listPractices")
    practices = envelope["data"]
    assert practices, "the staging workspace must expose at least one practice"
    assert all({"id", "name"} <= set(practice) for practice in practices)
    assert any(
        practice["id"] == staging_settings.practice_id for practice in practices
    ), f"PRINCIPLE_PRACTICE_ID={staging_settings.practice_id!r} is not in this workspace"


async def test_a_wrong_api_key_is_refused(staging_settings: Settings) -> None:
    """Confirms the real refusal for a bad key, which is what the fake must echo.

    The fake may only refuse in recorded wording; this is where that wording comes from.
    A client that treated 401 as an empty result would report an empty diary as a real one.
    """
    wrong = staging_settings.model_copy(update={"api_key": staging_settings.api_key.__class__("")})
    async with PrincipleClient(wrong) as client:
        with pytest.raises(PrincipleError) as raised:
            await client.get("listPractices")
    assert raised.value.status in {401, 403}, raised.value


async def test_appointment_paging_terminates_against_the_real_api(
    staging_client: PrincipleClient, staging_settings: Settings
) -> None:
    """The cursor contract, proven where it actually matters.

    od_data met non-advancing offsets on this API. A small page size forces several
    boundaries; if the real cursor misbehaves, the client's guard ends the walk and this
    test still finishes -- and the duplicate-id assertion records what happened.
    """
    rows = [
        row
        async for row in staging_client.rows(
            "listAppointmentsByDateRange",
            query={"practiceId": staging_settings.practice_id, **_window()},
            page_size=5,
        )
    ]
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)), "the real API repeated a page boundary"
    assert all("event" in row and "status" in row for row in rows)


async def test_an_unknown_practice_is_refused_not_silently_empty(
    staging_client: PrincipleClient,
) -> None:
    """A wrong practiceId must not read as a practice with no appointments.

    An empty diary and a rejected filter look identical in a report, and this is the one
    that would be filed as "no appointments today".
    """
    try:
        rows = [
            row
            async for row in staging_client.rows(
                "listAppointmentsByDateRange", query={"practiceId": "no-such-practice", **_window()}
            )
        ]
    except PrincipleError as refused:
        assert refused.status in {400, 403, 404}, refused
        return
    pytest.fail(
        f"an unknown practiceId returned {len(rows)} rows instead of refusing; "
        "reports built on this filter cannot distinguish empty from rejected"
    )


async def test_the_client_reaches_staging_over_real_tls(staging_settings: Settings) -> None:
    """Catches a base URL or TLS regression that the fake's in-process transport hides."""
    async with httpx.AsyncClient(timeout=30) as probe:
        response = await probe.get(f"{staging_settings.api_base_url}/v1/practices")
    assert response.status_code in {401, 403}, (
        f"an unauthenticated request returned {response.status_code}; "
        "the API must not serve practice data without a key"
    )
