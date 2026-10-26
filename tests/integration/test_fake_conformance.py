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
from typing import Any

import pytest
from scripts.record_principle_wire import FIRESTORE_CATALOGUE, covers, joined, shape_of
from scripts.record_principle_wire import recorded as answers

from dental_practice_admin.config import Environment, Settings
from tests.fake import FakeStore, dispatch
from tests.fake import firestore as fake_firestore
from tests.fake.server import Request
from tests.fake.store import FAKE_API_KEY, FAKE_PRACTICE_ID

pytestmark = pytest.mark.integration


def recorded(name: str) -> list[Any]:
    """Every successful answer production gave, from the data folder the recorder keeps."""
    found = answers(Settings(environment=Environment.PRODUCTION), name)
    if not found:
        pytest.fail(f"no recording of {name}; record it with"
                    " `uv run python -m scripts.record_principle_wire`")
    return found


def answer(store: FakeStore, method: str, path: str, query: dict[str, str] | None = None,
           body: dict[str, Any] | None = None) -> Any:
    response = dispatch(store, Request(method, path, query or {}, {"x-api-key": FAKE_API_KEY},
                                       json.dumps(body).encode() if body else b""))
    return response.body


API_CALLS = {
    "listPractices": ("GET", "/v1/practices", {}, None),
    "listPractitioners": ("GET", f"/v1/practices/{FAKE_PRACTICE_ID}/practitioners", {}, None),
    "listAppointmentsByDateRange": ("GET", "/v1/appointments", {
        "practiceId": FAKE_PRACTICE_ID, "from": "2026-09-27T00:00:00Z",
        "to": "2026-09-29T00:00:00Z", "limit": "2"}, None),
    "getPatient": ("GET", "/v1/patients/fake-patient-001", {}, None),
    "searchPatients": ("GET", "/v1/patients",
                        {"practiceId": FAKE_PRACTICE_ID, "name": "Patient 001"}, None),
    "createPatient": ("POST", "/v1/patients", {}, {
        "practiceId": FAKE_PRACTICE_ID, "name": "Conformance", "dateOfBirth": "1970-01-01",
        "gender": "notSpecified", "email": "conformance@fake.invalid"}),
}


@pytest.mark.parametrize("name", sorted(API_CALLS))
def test_the_fake_api_sends_only_recorded_shapes(fake_store: FakeStore, name: str) -> None:
    method, path, query, body = API_CALLS[name]
    real = joined([shape_of(body) for body in recorded(name)])[0]
    fake = shape_of(answer(fake_store, method, path, query, body))
    assert covers(real, fake) == []


FIRESTORE_READS: dict[str, Callable[[FakeStore], list[dict[str, Any] | None]]] = {
    "patients": lambda store: [fake_firestore.get_document(store, "patients/fake-patient-001")],
    "appointments": lambda store: [fake_firestore.get_document(
        store, "patients/fake-patient-001/appointments/fake-appointment-001")],
    "treatmentSteps": lambda store: [fake_firestore.get_document(
        store, "patients/fake-patient-001/treatmentPlans/plan-fake-appointment-001"
        "/treatmentSteps/step-fake-appointment-001")],
    "treatmentCategories": lambda store: [fake_firestore.get_document(
        store, "treatmentCategories/fake-category-hygiene")],
    "scheduleSummaries": lambda store: [
        row["document"] for row in fake_firestore.run_query(
            store, f"practices/{FAKE_PRACTICE_ID}",
            {"structuredQuery": {"from": [{"collectionId": "scheduleSummaries"}]}})],
    "rosterSchedules": lambda store: [
        row["document"] for row in fake_firestore.run_query(
            store, "staff/fake-practitioner-02",
            {"structuredQuery": {"from": [{"collectionId": "rosterSchedules"}]}})],
    "calendarEvents": lambda store: [
        row["document"] for row in fake_firestore.run_query(
            store, "", {"structuredQuery": {"from": [{"collectionId": "calendarEvents"}]}})],
}


def test_every_recorded_collection_is_checked() -> None:
    assert set(FIRESTORE_READS) == set(FIRESTORE_CATALOGUE)


@pytest.mark.parametrize("name", FIRESTORE_CATALOGUE)
def test_the_fake_firestore_sends_only_recorded_shapes(fake_store: FakeStore,
                                                       name: str) -> None:
    # A document read answers the document; a query answers rows, each holding one.
    real = joined([shape_of(document.get("fields", {}))
                   for body in recorded(f"firestore/{name}")
                   for document in ([row["document"] for row in body if "document" in row]
                                    if isinstance(body, list) else [body])])[0]
    documents = FIRESTORE_READS[name](fake_store)
    assert documents and all(documents)
    for document in documents:
        assert document is not None
        assert covers(real, shape_of(document["fields"])) == []
