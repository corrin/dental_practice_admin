"""The fake Principle sends only shapes the real one was recorded sending.

The recordings are the oracle (scripts/record_principle_wire.py). They stay on the machine
that made them, because they derive from patient records, so this runs in the integration
tier beside the other checks that need a real Principle. A missing recording fails rather
than skips: re-record with scripts/record_principle_wire.py.

Each check is one-way. The fake may leave out a field Principle sends, which a task reading
that field would find at once. It may not send a field or a type Principle never did, because
a task written against that would pass here and fail in the practice.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scripts.record_principle_wire import FIRESTORE_CATALOGUE, RECORDINGS, covers, shape_of

from tests.fake import FakeStore, dispatch
from tests.fake import firestore as fake_firestore
from tests.fake.server import Request
from tests.fake.store import FAKE_API_KEY, FAKE_PRACTICE_ID

pytestmark = pytest.mark.integration


def recorded(path: Path) -> dict[str, Any]:
    if not path.exists():
        pytest.fail(f"{path} is missing; re-record with scripts/record_principle_wire.py")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def answer(store: FakeStore, method: str, path: str, query: dict[str, str] | None = None,
           body: dict[str, Any] | None = None) -> Any:
    response = dispatch(store, Request(method, path, query or {}, {"x-api-key": FAKE_API_KEY},
                                       json.dumps(body).encode() if body else b""))
    return response.body


API_CALLS = {
    "practices": ("GET", "/v1/practices", {}, None),
    "practitioners": ("GET", f"/v1/practices/{FAKE_PRACTICE_ID}/practitioners", {}, None),
    "appointments": ("GET", "/v1/appointments", {
        "practiceId": FAKE_PRACTICE_ID, "from": "2026-09-27T00:00:00Z",
        "to": "2026-09-29T00:00:00Z", "limit": "2"}, None),
    "patient": ("GET", "/v1/patients/fake-patient-001", {}, None),
    "search_patients": ("GET", "/v1/patients",
                        {"practiceId": FAKE_PRACTICE_ID, "name": "Patient 001"}, None),
    "create_patient": ("POST", "/v1/patients", {}, {
        "practiceId": FAKE_PRACTICE_ID, "name": "Conformance", "dateOfBirth": "1970-01-01",
        "gender": "notSpecified", "email": "conformance@fake.invalid"}),
}


@pytest.mark.parametrize("name", sorted(API_CALLS))
def test_the_fake_api_sends_only_recorded_shapes(fake_store: FakeStore, name: str) -> None:
    method, path, query, body = API_CALLS[name]
    real = shape_of(recorded(RECORDINGS / f"{name}.json")["body"])
    fake = shape_of(answer(fake_store, method, path, query, body))
    assert covers(real, fake) == []


FIRESTORE_READS: dict[str, Callable[[FakeStore], list[dict[str, Any] | None]]] = {
    "patient": lambda store: [fake_firestore.get_document(store, "patients/fake-patient-001")],
    "appointment": lambda store: [fake_firestore.get_document(
        store, "patients/fake-patient-001/appointments/fake-appointment-001")],
    "treatment_step": lambda store: [fake_firestore.get_document(
        store, "patients/fake-patient-001/treatmentPlans/plan-fake-appointment-001"
        "/treatmentSteps/step-fake-appointment-001")],
    "treatment_category": lambda store: [fake_firestore.get_document(
        store, "treatmentCategories/fake-category-hygiene")],
    "schedule_summaries": lambda store: [
        row["document"] for row in fake_firestore.run_query(
            store, f"practices/{FAKE_PRACTICE_ID}",
            {"structuredQuery": {"from": [{"collectionId": "scheduleSummaries"}]}})],
    "roster_schedules": lambda store: [
        row["document"] for row in fake_firestore.run_query(
            store, "staff/fake-practitioner-02",
            {"structuredQuery": {"from": [{"collectionId": "rosterSchedules"}]}})],
    "calendar_events": lambda store: [
        row["document"] for row in fake_firestore.run_query(
            store, "", {"structuredQuery": {"from": [{"collectionId": "calendarEvents"}]}})],
}


def test_every_recorded_collection_is_checked() -> None:
    assert set(FIRESTORE_READS) == set(FIRESTORE_CATALOGUE)


@pytest.mark.parametrize("name", FIRESTORE_CATALOGUE)
def test_the_fake_firestore_sends_only_recorded_shapes(fake_store: FakeStore,
                                                       name: str) -> None:
    real = recorded(RECORDINGS / "firestore" / f"{name}.json")["shape"]
    documents = FIRESTORE_READS[name](fake_store)
    assert documents and all(documents)
    for document in documents:
        assert document is not None
        assert covers(real, shape_of(document["fields"])) == []
