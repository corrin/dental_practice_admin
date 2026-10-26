"""The client's contract, exercised against the fake Principle at its URL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin.principle import CallError, PrincipleClient, PrincipleError
from tests.fake import FAKE_PRACTICE_ID
from tests.fake.store import FAKE_API_KEY, SEARCH_INDEX_LAG
from tests.servers import FakePrincipleServer
from tests.settings import fake_settings

# Wide enough to cover the whole seeded diary, whose slots are NZ business hours.
WINDOW = {"from": "2026-09-27T00:00:00Z", "to": "2026-10-02T00:00:00Z"}


def _send(fake: FakePrincipleServer, method: str, path: str, query: dict[str, str] | None = None,
          body: dict[str, Any] | None = None) -> httpx.Response:
    """One request straight to the fake, past the client's own checks."""
    return httpx.request(method, fake.url + path, params=query, json=body,
                         headers={"x-api-key": FAKE_API_KEY}, timeout=10)


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
    fake_client: PrincipleClient, fake_principle: FakePrincipleServer
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
        row["id"]
        for row in fake_principle.sql("SELECT id FROM appointments")  # every seeded row
    }
    assert len(ids) == len(set(ids)), "a page boundary was repeated"
    assert set(ids) == expected


async def test_pagination_stops_when_the_cursor_repeats(tmp_path: Path) -> None:
    """A server that returns the same nextOffsetId forever must not hang the client.

    The spec's own PaginationMeta example has nextOffsetId equal to offsetId, and od_data met
    non-advancing cursors on this API, so removing the guard would turn a scheduled report
    into an unbounded loop against production.
    """
    pages = iter(["appointment-1", "appointment-2", "appointment-3"])

    def stuck_cursor(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page(next(pages), "stuck"))

    settings = fake_settings(tmp_path, api_key=SecretStr("k"), practice_id="p")
    async with PrincipleClient(settings, transport=httpx.MockTransport(stuck_cursor)) as client:
        with pytest.raises(PrincipleError):
            _ = [
                row
                async for row in client.rows(
                    "listAppointmentsByDateRange", query={"practiceId": "p", **WINDOW}, page_size=1
                )
            ]


async def test_pagination_refuses_when_the_cursor_restarts(tmp_path: Path) -> None:
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

    settings = fake_settings(tmp_path, api_key=SecretStr("k"), practice_id="p")
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


def test_fake_refuses_a_parameter_it_does_not_apply(fake_principle: FakePrincipleServer) -> None:
    """The fake must never accept a filter it silently ignores.

    A fake that dropped `status` would answer with every appointment and the test asserting
    the filter would pass, proving a belief about Principle rather than its behaviour.
    """
    response = _send(fake_principle, "GET", "/v1/practices/fake-practice-0001/practitioners",
                     {"status": "scheduled"})
    assert response.status_code == 500
    assert "FakeUnhandledParameterError" in fake_principle.take_log()


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


async def test_error_status_becomes_a_typed_failure(tmp_path: Path) -> None:
    """A non-2xx must raise with its status attached, not return an empty envelope.

    A caller that saw `{}` on a 403 would file an empty report as a complete one.
    """

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    settings = fake_settings(tmp_path, api_key=SecretStr("k"), practice_id="p")
    async with PrincipleClient(settings, transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(PrincipleError) as raised:
            await client.get("listPractices")
    assert raised.value.status == 403


async def test_an_update_replaces_the_fields_sent_and_keeps_the_rest(
        fake_client: PrincipleClient) -> None:
    """docs/principle/patient-writes.md: numbers, address and tags not sent are kept."""
    created = await fake_client.call("createPatient", {
        "name": "Update Fake", "dateOfBirth": "1980-02-03", "gender": "female",
        "email": "update@fake.invalid", "address": "1 Fake Street",
        "contactNumbers": [{"label": "mobile", "number": "+6421000001"}]})
    required = {key: created[key] for key in ("name", "dateOfBirth", "gender", "email")}

    await fake_client.call("updatePatient", {"patientId": created["id"], **required,
                                             "address": "2 Fake Street"})
    saved = await fake_client.call("getPatient", {"patientId": created["id"]})

    assert saved["address"] == "2 Fake Street"
    assert saved["contactNumbers"] == created["contactNumbers"]
    assert saved["updatedAt"] >= created["updatedAt"]

@pytest.fixture
async def slow_client(tmp_path: Path,
                      slow_principle: FakePrincipleServer) -> AsyncIterator[PrincipleClient]:
    """The production client, reaching the fake Principle at Principle's measured speed."""
    client = PrincipleClient(fake_settings(tmp_path, api_base_url=slow_principle.url))
    yield client
    await client.aclose()


async def test_search_does_not_find_a_patient_straight_after_it_is_created(
        slow_client: PrincipleClient) -> None:
    """Principle's name search lags a create by seconds (docs/principle/patient-writes.md)."""
    created = await slow_client.call("createPatient", {
        "name": "Lagging Fake", "dateOfBirth": "1980-02-03", "gender": "female",
        "email": "lag@fake.invalid"})

    found = await slow_client.call("searchPatients", {"name": "Lagging Fake"})

    assert created["id"] not in [row["id"] for row in found["data"]]


async def test_a_patient_the_client_has_just_created_can_be_read_straight_away(
        slow_client: PrincipleClient) -> None:
    created = await slow_client.call("createPatient", {
        "name": "Read Back Fake", "dateOfBirth": "1980-02-03", "gender": "female",
        "email": "readback@fake.invalid"})

    read = await slow_client.call("getPatient", {"patientId": created["id"]})

    assert read["id"] == created["id"]


async def test_a_patient_created_in_one_chat_tool_call_can_be_read_in_the_next(
        tmp_path: Path, slow_principle: FakePrincipleServer) -> None:
    """Each chat tool call opens its own client (principle.api_tools)."""
    settings = fake_settings(tmp_path, api_base_url=slow_principle.url)
    async with PrincipleClient(settings) as creating:
        created = await creating.call("createPatient", {
            "name": "Next Call Fake", "dateOfBirth": "1980-02-03", "gender": "female",
            "email": "nextcall@fake.invalid"})
    async with PrincipleClient(settings) as reading:
        read = await reading.call("getPatient", {"patientId": created["id"]})

    assert read["id"] == created["id"]


def test_an_update_missing_a_required_field_is_not_answered_with_an_invented_reply(
        fake_principle: FakePrincipleServer) -> None:
    """The spec requires practiceId, name, dateOfBirth, gender and email on updatePatient."""
    patient_id = fake_principle.sql("SELECT id FROM patients LIMIT 1")[0]["id"]
    response = _send(fake_principle, "PATCH", f"/v1/patients/{patient_id}",
                     body={"practiceId": FAKE_PRACTICE_ID, "name": "Fake Partial"})
    assert response.status_code == 500
    log = fake_principle.take_log()
    assert "FakeRefusalNotRecordedError" in log and "'update_patient_invalid'" in log


def test_an_update_to_another_practices_patient_is_not_accepted(
        fake_principle: FakePrincipleServer) -> None:
    """The fake must not let a write cross the practice boundary that Principle keeps."""
    fake_principle.sql(
        "INSERT INTO patients (id, practice_id, name, created_at, updated_at, email)"
        " VALUES ('fake-elsewhere', 'fake-practice-0002', 'Fake Elsewhere',"
        " '2020-01-01T00:00:00.000Z', '2020-01-01T00:00:00.000Z', 'elsewhere@fake.invalid')")
    response = _send(fake_principle, "PATCH", "/v1/patients/fake-elsewhere", body={
        "practiceId": FAKE_PRACTICE_ID, "name": "Fake Elsewhere", "dateOfBirth": "1980-02-03",
        "gender": "female", "email": "elsewhere@fake.invalid"})
    assert response.status_code == 500
    log = fake_principle.take_log()
    assert "FakeRefusalNotRecordedError" in log and "'update_patient_other_practice'" in log


async def test_a_reset_drops_index_updates_still_pending(
        slow_client: PrincipleClient, slow_principle: FakePrincipleServer) -> None:
    """An index update scheduled before a reset never reaches the closed database.

    Waits out the real lag, about 20 s: the lag is fixed when the fake starts (ADR 0009).
    """
    await slow_client.call("createPatient", {
        "name": "Reset Fake", "dateOfBirth": "1980-02-03", "gender": "female",
        "email": "reset@fake.invalid"})
    slow_principle.reset()

    await asyncio.sleep(SEARCH_INDEX_LAG.total_seconds() + 1.5)

    assert "Traceback" not in slow_principle.take_log()
