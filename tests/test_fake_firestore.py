"""The fake's Firestore answers from the same state as its API, and refuses what it can't."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from dental_practice_admin.config import Settings
from dental_practice_admin.scripts import Services
from tests.fake import FakeStore, transport
from tests.fake.firestore import ROOT, FirestoreUnhandledError
from tests.fake.server import REFUSALS_DIR, FakeRefusalNotRecordedError
from tests.fake.store import FAKE_PRACTICE_ID

ADA, BO = "fake-practitioner-01", "fake-practitioner-02"
DAY = "2026-09-28"


@pytest.fixture
async def services(fake_settings: Settings, fake_store: FakeStore) -> AsyncIterator[Services]:
    """The task runner's integrations, with the fake where both sockets would be."""
    services = Services(fake_settings, transport=transport(fake_store))
    yield services
    await services.aclose()


def plain(value: dict[str, Any]) -> Any:
    """A Firestore REST value as plain Python, references reduced to their relative path."""
    (kind, raw), = value.items()
    if kind == "mapValue":
        return {k: plain(v) for k, v in raw.get("fields", {}).items()}
    if kind == "arrayValue":
        return [plain(v) for v in raw.get("values", [])]
    if kind == "referenceValue":
        return raw.removeprefix(f"{ROOT}/")
    if kind == "integerValue":
        return int(raw)
    return raw


def fields(document: dict[str, Any]) -> dict[str, Any]:
    return {k: plain(v) for k, v in document["fields"].items()}


def query(collection: str, *filters: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    where = [{"fieldFilter": {"field": {"fieldPath": p}, "op": o, "value": v}}
             for p, o, v in filters]
    body: dict[str, Any] = {"from": [{"collectionId": collection}]}
    if where:
        body["where"] = where[0] if len(where) == 1 else {
            "compositeFilter": {"op": "AND", "filters": where}}
    return {"structuredQuery": body}


async def documents(services: Services, parent: str,
                    body: dict[str, Any]) -> list[dict[str, Any]]:
    rows = await services.firestore.read(parent, body)
    return [fields(row["document"]) for row in rows if "document" in row]


async def test_a_created_patient_is_one_patient_everywhere(services: Services) -> None:
    created = await services.api.call("createPatient", {
        "practiceId": FAKE_PRACTICE_ID, "name": "Created Synthetic", "dateOfBirth": "1980-02-03",
        "gender": "female", "email": "created@fake.invalid"})

    fetched = await services.api.get("getPatient", path_params={"patientId": created["id"]})
    found = await services.api.get("searchPatients", query={"name": "Created Synthetic"})
    document = fields(await services.firestore.read(f"patients/{created['id']}"))

    assert fetched == created
    assert [row["id"] for row in found["data"]] == [created["id"]]
    assert (document["name"], document["dateOfBirth"], document["email"]) == (
        "Created Synthetic", "1980-02-03", "created@fake.invalid")


async def test_the_day_sheets_reads_answer_from_the_seed(services: Services) -> None:
    """The documents the day sheet reads, computed from the same rows as the API."""
    window = {"from": "2026-09-27T11:00:00Z", "to": "2026-09-28T11:00:00Z"}
    appointments = [row async for row in services.api.rows(
        "listAppointmentsByDateRange", query={"practiceId": FAKE_PRACTICE_ID, **window})]
    filled = next(a for a in appointments if a["id"] == "fake-appointment-001")

    summaries = await documents(services, f"practices/{FAKE_PRACTICE_ID}", query(
        "scheduleSummaries", ("day", "EQUAL", {"stringValue": DAY})))
    cards = {event["ref"].rsplit("/", 1)[1]: event["metadata"]
             for summary in summaries for event in summary["events"]}
    step = fields(await services.firestore.read(
        f"patients/{filled['patientId']}/treatmentPlans/{filled['treatmentPlanId']}"
        f"/treatmentSteps/{filled['treatmentStepId']}"))
    uncoloured = fields(await services.firestore.read(
        "treatmentCategories/fake-category-uncoloured"))

    assert {a["status"] for a in appointments if a["id"] not in cards} == {"cancelled"}
    assert cards["fake-appointment-001"]["pinnedNotes"] == [
        "Prefers mornings", "Anxious, explain first"]
    assert cards["fake-appointment-002"]["tags"][0]["name"] == "Shifted to Bo"
    assert [s["chartedRef"]["tooth"] for s in step["treatments"][0]["chartedSurfaces"]] == [
        {"quadrant": 1, "quadrantIndex": 8, "surface": "occlusal"},
        {"quadrant": 1, "quadrantIndex": 8, "surface": "mesial"}]
    assert step["treatments"][0]["uuid"] == filled["treatments"][0]["id"]
    assert "colour" not in uncoloured


async def test_a_block_moved_for_one_day_leaves_the_roster_for_a_calendar_event(
    services: Services,
) -> None:
    roster = await documents(services, f"staff/{BO}", query("rosterSchedules"))
    events = await documents(services, "", query(
        "calendarEvents",
        ("deleted", "EQUAL", {"booleanValue": False}),
        ("event.practice.ref", "EQUAL",
         {"referenceValue": f"{ROOT}/practices/{FAKE_PRACTICE_ID}"}),
        ("event.from", "GREATER_THAN_OR_EQUAL", {"timestampValue": "2026-09-27T11:00:00Z"}),
        ("event.from", "LESS_THAN", {"timestampValue": "2026-09-28T11:00:00Z"})))
    later = await documents(services, "", query(
        "calendarEvents",
        ("event.from", "GREATER_THAN_OR_EQUAL", {"timestampValue": "2026-09-28T11:00:00Z"})))

    [lunch] = roster
    assert lunch["modifiers"] == [{"type": "delete", "dates": ["2026-09-28T00:00:00Z"]}]
    moved = next(e for e in events if e["event"]["type"] == "break")
    assert moved["scheduleRef"] == f"staff/{BO}/rosterSchedules/fake-roster-bo-lunch"
    assert moved["event"]["from"] == "2026-09-28T00:30:00Z"
    request = next(e for e in events if e["event"]["type"] == "appointmentRequest")
    assert request["template"]["step"]["name"] == "Problem with a tooth (single issue)"
    assert later == []


@pytest.mark.parametrize("body", [
    query("waitLists"),
    {"structuredQuery": {**query("calendarEvents")["structuredQuery"],
                         "orderBy": [{"field": {"fieldPath": "event.from"}}]}},
    {"structuredQuery": {"from": [{"collectionId": "calendarEvents", "allDescendants": True}]}},
    {"structuredQuery": {"from": [{"collectionId": "calendarEvents"}], "where": {
        "compositeFilter": {"op": "OR", "filters": []}}}},
    {"structuredQuery": {"from": [{"collectionId": "calendarEvents"}], "where": {
        "unaryFilter": {"op": "IS_NULL", "field": {"fieldPath": "scheduleRef"}}}}},
    {"structuredQuery": {"from": [{"collectionId": "calendarEvents"}], "where": {
        "fieldFilter": {"field": {"fieldPath": "title"}, "op": "ARRAY_CONTAINS",
                        "value": {"stringValue": "Lunch"}}}}},
], ids=["unserved collection", "orderBy", "collection group", "OR", "unary", "array-contains"])
async def test_a_query_the_fake_cannot_apply_is_refused(services: Services,
                                                        body: dict[str, Any]) -> None:
    """Answering part of a query would be answering a different question."""
    with pytest.raises(FirestoreUnhandledError):
        await services.firestore.read("", body)


async def test_a_missing_document_refuses_in_recorded_words_only(services: Services) -> None:
    """A missing document is refused in Firestore's recorded words, or not at all.

    With the 404 recorded the client sees the status; without it the fake refuses to invent
    the wording. Either way nothing is answered.
    """
    recorded = (REFUSALS_DIR / "firestore_not_found.json").exists()
    expected = RuntimeError if recorded else FakeRefusalNotRecordedError
    with pytest.raises(expected) as refused:
        await services.firestore.read("patients/nobody")
    if recorded:
        assert "404" in str(refused.value)
