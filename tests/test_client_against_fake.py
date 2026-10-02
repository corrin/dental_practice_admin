"""The client's contract, exercised through the fake over the real transport."""

from __future__ import annotations

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.principle import CallError, PrincipleClient, PrincipleError
from tests.fake import FAKE_PRACTICE_ID, FakeStore, dispatch
from tests.fake.server import FakeUnhandledParameterError, Request
from tests.fake.store import FAKE_API_KEY

# Wide enough to cover the whole seeded diary, whose slots are NZ business hours.
WINDOW = {"from": "2026-09-27T00:00:00Z", "to": "2026-10-02T00:00:00Z"}


def _page(row_id: str, next_offset: str) -> dict[str, object]:
    return {
        "data": [
            {
                "id": row_id,
                "practiceId": "p",
                "practitionerId": "fake-practitioner",
                "patientId": "fake-patient",
                "treatmentOptionId": "fake-option",
                "treatmentPlanId": "fake-plan",
                "treatmentStepId": "fake-step",
                "status": "scheduled",
            }
        ],
        "meta": {"limit": 1, "total": 1, "nextOffsetId": next_offset},
    }


async def test_lists_practices_and_says_it_is_fake(fake_client: PrincipleClient) -> None:
    """A run against the fake must be impossible to mistake for a real one.

    If the marker were dropped from the seed, a report built during development could be
    filed as though it described the real practice.
    """
    envelope = await fake_client.get("listPractices")
    names = [practice["name"] for practice in envelope["data"]]
    assert any("(FAKE PRINCIPLE)" in name for name in names), names


async def test_pagination_returns_every_row_once(
    fake_client: PrincipleClient, fake_store: FakeStore
) -> None:
    """A paging refactor that dropped or repeated a page boundary is caught here.

    The page size is deliberately smaller than the seeded diary, so the walk crosses
    several boundaries; asserting on the set of ids catches both loss and duplication.
    """
    rows = [
        row
        async for row in fake_client.rows(
            "listAppointmentsByDateRange",
            query={"practiceId": FAKE_PRACTICE_ID, **WINDOW},
            page_size=5,
        )
    ]
    ids = [row["id"] for row in rows]
    expected = {
        row[0]
        for row in fake_store.db.execute("SELECT id FROM appointments")  # every seeded row
    }
    assert len(ids) == len(set(ids)), "a page boundary was repeated"
    assert set(ids) == expected


async def test_pagination_stops_when_the_cursor_repeats() -> None:
    """A server that returns the same nextOffsetId forever must not hang the client.

    The spec's own PaginationMeta example has nextOffsetId equal to offsetId, and od_data met
    non-advancing cursors on this API, so removing the guard would turn a scheduled report
    into an unbounded loop against production.
    """
    pages = iter(["appointment-1", "appointment-2", "appointment-3"])

    def stuck_cursor(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page(next(pages), "stuck"))

    settings = Settings(environment=Environment.FAKE, api_key=SecretStr("k"), practice_id="p")
    async with PrincipleClient(settings, transport=httpx.MockTransport(stuck_cursor)) as client:
        with pytest.raises(PrincipleError):
            _ = [
                row
                async for row in client.rows(
                    "listAppointmentsByDateRange", query={"practiceId": "p", **WINDOW}, page_size=1
                )
            ]


async def test_pagination_refuses_when_the_cursor_restarts() -> None:
    """A restarted walk must fail rather than double-count.

    `nextOffsetId` is a createdAt, and Principle ignores a cursor it cannot place instead of
    refusing it, so a stale cursor answers with page one again. Yielding those rows a second
    time would inflate every number in the report, and a plausible wrong count is worse than
    an error.
    """
    answers = iter(
        [
            _page("appointment-1", "c1"),
            _page("appointment-1", "c2"),
        ]
    )

    def restarts(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(answers))

    settings = Settings(environment=Environment.FAKE, api_key=SecretStr("k"), practice_id="p")
    async with PrincipleClient(settings, transport=httpx.MockTransport(restarts)) as client:
        with pytest.raises(PrincipleError):
            _ = [
                row
                async for row in client.rows(
                    "listAppointmentsByDateRange", query={"practiceId": "p", **WINDOW}, page_size=1
                )
            ]


async def test_window_is_half_open_and_intersecting(fake_client: PrincipleClient) -> None:
    """An appointment running into the window from before it must still be reported.

    A rewrite to `event_from BETWEEN from AND to` would silently drop the appointment a
    staff member is most likely to ask about -- the one already in progress.
    """
    rows = [
        row
        async for row in fake_client.rows(
            "listAppointmentsByDateRange",
            query={
                "practiceId": FAKE_PRACTICE_ID,
                # Five minutes inside the first slot (09:00-09:30 NZ = 20:00-20:30Z).
                "from": "2026-09-27T20:05:00Z",
                "to": "2026-09-27T20:10:00Z",
            },
        )
    ]
    assert [row["id"] for row in rows] == ["fake-appointment-000"]


async def test_undeclared_query_parameter_is_refused(fake_client: PrincipleClient) -> None:
    """A typo in a filter name must fail, not return an unfiltered answer.

    `practicianId` sent to the real API would be ignored and the call would return every
    practitioner's appointments, which reads as a plausible report.
    """
    with pytest.raises(CallError, match="does not accept"):
        await fake_client.get(
            "listAppointmentsByDateRange",
            query={"practicianId": "x", "practiceId": FAKE_PRACTICE_ID, **WINDOW},
        )


async def test_required_query_parameter_is_refused_when_absent(
    fake_client: PrincipleClient,
) -> None:
    """The spec makes `from`/`to` required; omitting them must fail before the request."""
    with pytest.raises(CallError, match="requires"):
        await fake_client.get("listAppointmentsByDateRange", query={"practiceId": FAKE_PRACTICE_ID})


def test_fake_refuses_a_parameter_it_does_not_apply(fake_store: FakeStore) -> None:
    """The fake must never accept a filter it silently ignores.

    A fake that dropped `status` would answer with every appointment and the test asserting
    the filter would pass, proving a belief about Principle rather than its behaviour.
    """
    request = Request(
        method="GET",
        path="/v1/practices/fake-practice-0001/practitioners",
        query={"status": "scheduled"},
        headers={"x-api-key": FAKE_API_KEY},
    )
    with pytest.raises(FakeUnhandledParameterError, match="does not apply"):
        dispatch(fake_store, request)


async def test_status_filter_is_applied(fake_client: PrincipleClient) -> None:
    """A dropped status predicate is caught by asking for one status and seeing only it."""
    rows = [
        row
        async for row in fake_client.rows(
            "listAppointmentsByDateRange",
            query={"practiceId": FAKE_PRACTICE_ID, "status": "cancelled", **WINDOW},
        )
    ]
    assert rows, "the seed must contain cancelled appointments for this to prove anything"
    assert {row["status"] for row in rows} == {"cancelled"}


async def test_practitioners_are_listed_for_their_practice(
    fake_client: PrincipleClient,
) -> None:
    """A path-parameter regression would list another practice's practitioners."""
    rows = [
        row
        async for row in fake_client.rows(
            "listPractitioners", path_params={"practiceId": FAKE_PRACTICE_ID}
        )
    ]
    assert len(rows) == 2
    assert all("id" in row and "name" in row for row in rows)


async def test_error_status_becomes_a_typed_failure() -> None:
    """A non-2xx must raise with its status attached, not return an empty envelope.

    A caller that saw `{}` on a 403 would file an empty report as a complete one.
    """

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    settings = Settings(environment=Environment.FAKE, api_key=SecretStr("k"), practice_id="p")
    async with PrincipleClient(settings, transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(PrincipleError) as raised:
            await client.get("listPractices")
    assert raised.value.status == 403
