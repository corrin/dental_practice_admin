"""The fake Principle's Firestore: documents rendered from the same store as the API.

Principle's website reads Firestore directly, and some tasks read what the REST API lacks
(docs/principle/firestore.md). Every document here is computed from the store's tables, so a
patient created through `POST /v1/patients` is the patient `patients/{id}` returns, and an
appointment's timeline card follows its row.

Served: single-document reads and `:runQuery` over one collection, with `EQUAL`, the range
operators and `AND`. Anything else -- another collection, `orderBy`, `limit`, a unary or `OR`
filter, a collection group, an aggregation -- raises, as the API routes do, because a fake that
quietly ignores part of a query answers a different question.

Firebase sign-in and token refresh answer here too, at the paths Google serves them on. The
fake has no accounts: it signs in any caller and then insists on the token it issued.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Any

from dental_practice_admin.config import FAKE_FIREBASE_PROJECT, FAKE_FIRESTORE_ROOT
from tests.fake.store import FAKE_PRACTICE_ID, PRACTICE_TZ, FakeStore

DOCUMENTS = f"projects/{FAKE_FIREBASE_PROJECT}/databases/(default)/documents"
ROOT = f"{DOCUMENTS}/{FAKE_FIRESTORE_ROOT}"

ID_TOKEN = "fake-firebase-id-token"
REFRESH_TOKEN = "fake-firebase-refresh-token"

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class Ref(str):
    """A document reference, relative to the brand root."""


class Stamp(str):
    """A timestamp, as the store spells it (UTC, `Z`)."""


Fields = dict[str, Any]


class FirestoreUnhandledError(NotImplementedError):
    """A Firestore read the fake does not serve: add it, from a recording."""


# -- typed values ----------------------------------------------------------------------------

def encode(value: Any) -> dict[str, Any]:
    """A Python value in Firestore's REST form."""
    if isinstance(value, Ref):
        return {"referenceValue": f"{ROOT}/{value}"}
    if isinstance(value, Stamp):
        return {"timestampValue": value}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if value is None:
        return {"nullValue": None}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode(item) for item in value]} if value else {}}
    return {"mapValue": {"fields": {k: encode(v) for k, v in value.items()}} if value else {}}


def decode(value: dict[str, Any]) -> Any:
    """A Firestore REST value as a comparable Python value: references and instants included."""
    (kind, raw), = value.items()
    if kind == "referenceValue":
        return Ref(raw.removeprefix(f"{ROOT}/"))
    if kind == "timestampValue":
        return datetime.fromisoformat(raw)
    if kind == "integerValue":
        return int(raw)
    if kind == "mapValue":
        return {k: decode(v) for k, v in raw.get("fields", {}).items()}
    if kind == "arrayValue":
        return [decode(v) for v in raw.get("values", [])]
    if kind in {"stringValue", "booleanValue", "nullValue", "doubleValue"}:
        return raw
    raise FirestoreUnhandledError(f"the fake Firestore does not compare {kind}")


def comparable(value: Any) -> Any:
    return datetime.fromisoformat(value) if isinstance(value, Stamp) else value


# -- documents ------------------------------------------------------------------------------

def named(staff: sqlite3.Row) -> Fields:
    return {"name": staff["name"], "ref": Ref(f"staff/{staff['id']}")}


def practice(store: FakeStore) -> Fields:
    row = store.db.execute("SELECT name FROM practices WHERE id = ?",
                           (FAKE_PRACTICE_ID,)).fetchone()
    return {"name": row["name"], "ref": Ref(f"practices/{FAKE_PRACTICE_ID}")}


def staffer(store: FakeStore, staff_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = store.db.execute(
        "SELECT * FROM practitioners WHERE id = ?", (staff_id,)).fetchone()
    if row is None:
        raise FirestoreUnhandledError(f"no staff member {staff_id!r} in the fake")
    return row


def participants(staff: sqlite3.Row) -> Fields:
    return {"participants": [{**named(staff), "type": "staffer"}],
            "participantRefs": [Ref(f"staff/{staff['id']}")]}


def patients(store: FakeStore, _parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    for row in store.db.execute("SELECT * FROM patients ORDER BY id"):
        fields: Fields = {"name": row["name"], "gender": row["gender"],
                          "email": row["email"] or f"{row['id']}@fake.invalid",
                          "address": row["address"] or "1 Fake Street", "status": "active",
                          "deleted": False, "createdAt": Stamp(row["created_at"]),
                          "updatedAt": Stamp(row["updated_at"]),
                          "ref": Ref(f"patients/{row['id']}")}
        if row["date_of_birth"] is not None:
            fields["dateOfBirth"] = row["date_of_birth"]
        if row["phone"] is not None:
            fields["contactNumbers"] = [{"label": "mobile", "number": row["phone"]}]
        yield row["id"], fields


def appointment_documents(store: FakeStore,
                          parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    for row in store.db.execute("SELECT * FROM appointments WHERE patient_id = ? ORDER BY id",
                                (parent.group("patient"),)):
        staff = staffer(store, row["practitioner_id"])
        step: Fields = {"name": row["step_name"],
                        "ref": Ref(step_path(row)),
                        "duration": minutes(row["event_from"], row["event_to"])}
        if row["category_id"] is not None:
            step["display"] = {
                "primaryTreatmentCategory": Ref(f"treatmentCategories/{row['category_id']}")}
        fields: Fields = {
            "event": {"from": Stamp(row["event_from"]), "to": Stamp(row["event_to"]),
                      "type": "appointment", "practice": practice(store),
                      **participants(staff)},
            "practitioner": named(staff), "practice": practice(store),
            "status": row["status"], "deleted": False,
            "tags": [{"name": tag["name"], "ref": Ref(f"appointmentTags/{tag['name']}")}
                     for tag in tags(store, row["id"])],
            "treatmentPlan": {"name": row["plan_name"],
                              "ref": Ref(f"patients/{row['patient_id']}/treatmentPlans/"
                                         f"{row['treatment_plan_id']}"),
                              "treatmentStep": step},
            "createdAt": Stamp(row["created_at"]), "updatedAt": Stamp(row["updated_at"]),
            "ref": Ref(f"patients/{row['patient_id']}/appointments/{row['id']}")}
        if row["online"]:
            fields["appointmentRequestRef"] = Ref(f"calendarEvents/request-{row['id']}")
        yield row["id"], fields


def step_path(row: sqlite3.Row) -> str:
    return (f"patients/{row['patient_id']}/treatmentPlans/{row['treatment_plan_id']}"
            f"/treatmentSteps/{row['treatment_step_id']}")


def treatment_steps(store: FakeStore, parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    for row in store.db.execute(
            "SELECT * FROM appointments WHERE patient_id = ? AND treatment_plan_id = ?",
            (parent.group("patient"), parent.group("plan"))):
        treatments = []
        for t in store.db.execute("SELECT * FROM appointment_treatments"
                                  " WHERE appointment_id = ? ORDER BY position", (row["id"],)):
            treatments.append({"uuid": t["id"], "config": {"name": t["description"]},
                               "type": "treatment", "chartedSurfaces": charted(t)})
        yield row["treatment_step_id"], {
            "name": row["step_name"], "status": "planned", "deleted": False,
            "treatments": treatments,
            "appointment": Ref(f"patients/{row['patient_id']}/appointments/{row['id']}"),
            "createdAt": Stamp(row["created_at"]), "updatedAt": Stamp(row["updated_at"]),
            "ref": Ref(step_path(row))}


def charted(treatment: sqlite3.Row) -> list[Fields]:
    """Charting as the step document holds it: one entry per surface, or the whole mouth."""
    if treatment["quadrant"] is None:
        return [{"uuid": f"{treatment['id']}-chart", "chartedRef": {"wholeMouth": True}}]
    tooth = {"quadrant": treatment["quadrant"], "quadrantIndex": treatment["quadrant_index"]}
    surfaces = (treatment["surfaces"] or "").split(",") if treatment["surfaces"] else [None]
    return [{"uuid": f"{treatment['id']}-chart-{n}",
             "chartedRef": {"tooth": {**tooth, **({"surface": s} if s else {})}}}
            for n, s in enumerate(surfaces)]


def categories(store: FakeStore, _parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    for row in store.db.execute("SELECT * FROM treatment_categories ORDER BY id"):
        fields: Fields = {"name": row["name"], "deleted": bool(row["deleted"]),
                          "ref": Ref(f"treatmentCategories/{row['id']}")}
        if row["colour_value"] is not None:
            fields["colour"] = {"name": row["colour_name"], "value": row["colour_value"]}
        yield row["id"], fields


def schedule_summaries(store: FakeStore,
                       parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    """One summary per practitioner per local day that has appointments.

    Cancelled appointments are left out, as the timeline hides them. Unverified: no production
    day from 2026-09-15 to 2026-11-16 had a cancellation to check against.
    """
    if parent.group("practice") != FAKE_PRACTICE_ID:
        return
    days: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in store.db.execute("SELECT * FROM appointments WHERE status != 'cancelled'"
                                " ORDER BY event_from, id"):
        local = datetime.fromisoformat(row["event_from"]).astimezone(PRACTICE_TZ).date()
        days.setdefault((row["practitioner_id"], local.isoformat()), []).append(row)
    for (staff_id, day), rows in sorted(days.items()):
        staff = staffer(store, staff_id)
        yield f"{staff_id}-{day}", {
            "day": day, "staffer": Ref(f"staff/{staff_id}"),
            "practice": Ref(f"practices/{FAKE_PRACTICE_ID}"),
            "events": [card(store, row, staff) for row in rows],
            "ref": Ref(f"practices/{FAKE_PRACTICE_ID}/scheduleSummaries/{staff_id}-{day}")}


def card(store: FakeStore, row: sqlite3.Row, staff: sqlite3.Row) -> Fields:
    patient = store.db.execute("SELECT name FROM patients WHERE id = ?",
                               (row["patient_id"],)).fetchone()
    metadata: Fields = {
        "label": patient["name"],
        "pinnedNotes": [n["text"] for n in store.db.execute(
            "SELECT text FROM patient_notes WHERE patient_id = ? ORDER BY position",
            (row["patient_id"],))],
        "status": row["status"],
        "tags": [{"name": t["name"], "ref": Ref(f"appointmentTags/{t['name']}")}
                 for t in tags(store, row["id"])],
        "patientRef": Ref(f"patients/{row['patient_id']}"),
        "treatmentPlanName": row["plan_name"], "treatmentStepName": row["step_name"],
        "dependencies": []}
    if row["category_id"] is not None:
        metadata["categoryRef"] = Ref(f"treatmentCategories/{row['category_id']}")
    return {"ref": Ref(f"patients/{row['patient_id']}/appointments/{row['id']}"),
            "event": {"from": Stamp(row["event_from"]), "to": Stamp(row["event_to"]),
                      "type": "appointment", "practice": practice(store),
                      **participants(staff)},
            "isBlocking": True, "metadata": metadata}


def rosters(store: FakeStore, parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    staff = staffer(store, parent.group("staff"))
    for row in store.db.execute("SELECT * FROM roster_items WHERE staff_id = ? ORDER BY id",
                                (staff["id"],)):
        removed = [Stamp(d["at"]) for d in store.db.execute(
            "SELECT at FROM roster_deletions WHERE roster_id = ? ORDER BY at", (row["id"],))]
        pattern: Fields = {"frequencyType": "Custom", "customFrequencyType": "Weekly",
                           "seperationCount": 1, "daysOfWeek": row["days"].split(","),
                           "startDate": row["start_date"],
                           "endingType": "on" if row["ending_date"] else "never"}
        if row["ending_date"]:
            pattern["endingDate"] = row["ending_date"]
        yield row["id"], {
            "deleted": bool(row["deleted"]), "pattern": pattern,
            "scheduleTime": {"from": row["from_time"], "to": row["to_time"]},
            "modifiers": [{"type": "delete", "dates": removed}] if removed else [],
            "item": {"deleted": False, "isBlocking": True,
                     "title": [{"type": "text", "text": row["title"]}],
                     "event": {"type": row["type"], "practice": practice(store),
                               **participants(staff)}},
            "ref": Ref(f"staff/{staff['id']}/rosterSchedules/{row['id']}")}


def calendar_events(store: FakeStore, _parent: re.Match[str]) -> Iterator[tuple[str, Fields]]:
    for row in store.db.execute("SELECT * FROM calendar_events ORDER BY id"):
        staff = staffer(store, row["staff_id"])
        fields: Fields = {
            "deleted": bool(row["deleted"]), "isBlocking": True,
            "title": [{"type": "text", "text": row["title"]}],
            "event": {"type": row["type"], "from": Stamp(row["event_from"]),
                      "to": Stamp(row["event_to"]), "practice": practice(store),
                      "organiser": named(staff), "creator": named(staff),
                      **participants(staff)},
            "createdAt": Stamp(row["created_at"]),
            "ref": Ref(f"calendarEvents/{row['id']}")}
        if row["schedule_ref"] is not None:
            fields["scheduleRef"] = Ref(f"staff/{staff['id']}/rosterSchedules/"
                                        f"{row['schedule_ref']}")
        if row["type"] == "appointmentRequest":
            fields["practitioner"] = named(staff)
            fields["template"] = {"step": {"name": row["step_name"]}}
        yield row["id"], fields


def minutes(starts: str, ends: str) -> int:
    return int((datetime.fromisoformat(ends) - datetime.fromisoformat(starts)).seconds // 60)


def tags(store: FakeStore, appointment_id: str) -> list[sqlite3.Row]:
    return store.db.execute("SELECT name FROM appointment_tags WHERE appointment_id = ?"
                            " ORDER BY position", (appointment_id,)).fetchall()


Collection = Callable[[FakeStore, "re.Match[str]"], Iterator[tuple[str, Fields]]]

# (parent path, collection id) -> documents. A parent of "" is the brand root.
COLLECTIONS: tuple[tuple[re.Pattern[str], Collection], ...] = tuple(
    (re.compile(f"^{pattern}$"), documents) for pattern, documents in (
        (r"/patients", patients),
        (r"patients/(?P<patient>[^/]+)/appointments", appointment_documents),
        (r"patients/(?P<patient>[^/]+)/treatmentPlans/(?P<plan>[^/]+)/treatmentSteps",
         treatment_steps),
        (r"/treatmentCategories", categories),
        (r"practices/(?P<practice>[^/]+)/scheduleSummaries", schedule_summaries),
        (r"staff/(?P<staff>[^/]+)/rosterSchedules", rosters),
        (r"/calendarEvents", calendar_events),
    ))


def collection(store: FakeStore, parent: str, collection_id: str) -> list[tuple[str, Fields]]:
    """Every document in `parent/collection_id`, as (relative path, fields)."""
    key = f"{parent}/{collection_id}"
    for pattern, documents in COLLECTIONS:
        match = pattern.match(key)
        if match is not None:
            prefix = f"{parent}/" if parent else ""
            return [(f"{prefix}{collection_id}/{ident}", fields)
                    for ident, fields in documents(store, match)]
    raise FirestoreUnhandledError(
        f"the fake Firestore has no collection {key!r}; add it to tests/fake/firestore.py, "
        "from a recording")


def rendered(path: str, fields: Fields) -> dict[str, Any]:
    stamp = fields.get("updatedAt") or fields.get("createdAt") or Stamp("2026-01-01T00:00:00Z")
    return {"name": f"{ROOT}/{path}", "fields": encode(fields)["mapValue"].get("fields", {}),
            "createTime": str(fields.get("createdAt") or stamp), "updateTime": str(stamp)}


# -- queries --------------------------------------------------------------------------------

OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "EQUAL": lambda a, b: a == b,
    "LESS_THAN": lambda a, b: a is not None and a < b,
    "LESS_THAN_OR_EQUAL": lambda a, b: a is not None and a <= b,
    "GREATER_THAN": lambda a, b: a is not None and a > b,
    "GREATER_THAN_OR_EQUAL": lambda a, b: a is not None and a >= b,
}


def matcher(where: dict[str, Any] | None) -> Callable[[Fields], bool]:
    """A filter as a predicate over a document's fields, refusing what it does not apply."""
    if where is None:
        return lambda _fields: True
    (kind, body), = where.items()
    if kind == "compositeFilter":
        if body["op"] != "AND":
            raise FirestoreUnhandledError(f"the fake Firestore applies AND, not {body['op']}")
        parts = [matcher(f) for f in body["filters"]]
        return lambda fields: all(part(fields) for part in parts)
    if kind != "fieldFilter":
        raise FirestoreUnhandledError(f"the fake Firestore does not apply {kind}")
    if body["op"] not in OPERATORS:
        raise FirestoreUnhandledError(f"the fake Firestore does not apply {body['op']}")
    test, path, wanted = OPERATORS[body["op"]], body["field"]["fieldPath"], decode(body["value"])

    def predicate(fields: Fields) -> bool:
        value: Any = fields
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return test(comparable(value), wanted)
    return predicate


def run_query(store: FakeStore, parent: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    query = body.get("structuredQuery")
    if query is None or set(body) != {"structuredQuery"}:
        raise FirestoreUnhandledError("the fake Firestore runs structuredQuery only")
    unapplied = set(query) - {"from", "where"}
    if unapplied:
        raise FirestoreUnhandledError(f"the fake Firestore does not apply {sorted(unapplied)}")
    (source,) = query["from"]
    if source.get("allDescendants"):
        raise FirestoreUnhandledError("the fake Firestore does not run collection groups")
    keep = matcher(query.get("where"))
    read_time = f"{date.today().isoformat()}T00:00:00Z"
    found = [{"document": rendered(path, fields), "readTime": read_time}
             for path, fields in collection(store, parent, source["collectionId"])
             if keep(fields)]
    return found or [{"readTime": read_time}]


def get_document(store: FakeStore, path: str) -> dict[str, Any] | None:
    parent = path.rpartition("/")[0]
    grandparent, _, collection_id = parent.rpartition("/")
    for found, fields in collection(store, grandparent, collection_id):
        if found == path:
            return rendered(path, fields)
    return None

