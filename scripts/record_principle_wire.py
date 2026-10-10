"""Capture what Principle actually returns, so the fake can be checked against it.

The fake computes its answers; these recordings are the oracle that proves those answers
carry the shapes and the refusal wording the real API produces. Nothing here is replayed at
test time -- a stored answer stops being true the moment the state changes.

Run against staging only:

    uv run python scripts/record_principle_wire.py

Two rules this enforces:

  * **Scrubbing happens before anything is written, and it covers identifiers, not just
    names.** The staging tenant is a migrated copy of a real practice. A patient id is a
    persistent, re-identifiable handle on a real person, and an appointment time attached to
    one is health information however opaque the id looks -- redacting the name is not
    enough. Every id and every timestamp is replaced with a synthetic value.

    Equality and ordering survive the replacement, because that is what the recordings are
    checked for: the same patient in two rows stays the same patient, `nextOffsetId` still
    equals the last row's `createdAt`, and the wire format (`+00:00`, not `Z`) is preserved.
    Values are assigned in order of first appearance, so nothing about the real value -- not
    even a hash of it -- reaches the file.
  * **Refusals are captured, never authored.** Each provoked error is saved under
    `tests/recordings/refusals/`, which is where tests/fake/server.py reads them from.

One catalogue drives both this script and the drift check, so what gets written and what
gets verified cannot diverge.

Firestore documents are recorded as shapes only: each field's name and value type, joined over
several documents, with no value at all. The shape is all tests/test_fake_conformance.py
compares, and a value that is never written cannot leak.

Creating a patient is the one write: a `[TEST]` patient on staging per run, so the fake's
`POST /v1/patients` is checked against what Principle answers.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from dental_practice_admin.config import (
    ConfigurationError,
    Environment,
    Settings,
    is_production_host,
)
from dental_practice_admin.firestore import Firestore
from dental_practice_admin.principle import PrincipleClient, PrincipleError

RECORDINGS = Path(__file__).resolve().parent.parent / "tests" / "recordings"

# One element is a shape; a hundred contacts are a hundred copies of it.
LIST_ELEMENTS_KEPT = 2

# The anonymiser denies by default: a field this module does not recognise is replaced,
# never passed through. An allowlist of things to scrub fails open -- the first Principle
# field nobody anticipated (a note, a referral source, an address nested somewhere new) would
# reach the file untouched and nobody would notice. Adding a key here is a deliberate act.
#
# Structural fields kept verbatim: envelope and paging machinery, enumerations, and counts.
# None of these can carry a patient's details.
STRUCTURAL_KEYS = frozenset(
    {
        "data",
        "meta",
        "event",
        "limit",
        "total",
        "status",
        "statusCode",
        "error",
        "message",
        "treatments",
        "gender",
    }
)

# Keys whose string values are opaque record handles. Each becomes a synthetic id.
ID_KEYS = frozenset(
    {
        "id",
        "patientId",
        "practiceId",
        "practitionerId",
        "treatmentOptionId",
        "treatmentPlanId",
        "treatmentStepId",
        "offsetId",
    }
)

# Keys whose value is a list of bare ids. Without this the ids inside slip through, because
# a list element has no key of its own to match on.
ID_LIST_KEYS = frozenset({"treatmentOptions", "tags"})

# Replaced before anything reaches disk. Keys, not paths, because the same field name
# carries the same kind of value wherever Principle nests it.
SCRUBBED: dict[str, object] = {
    "name": "Redacted Name",
    "firstName": "Redacted",
    "lastName": "Name",
    "preferredName": "Redacted",
    "dateOfBirth": "1970-01-01",
    "email": "redacted@example.invalid",
    "address": "Redacted address",
    "number": "+6490000000",
    "phoneNumber": "+6490000000",
    "mobileNumber": "+6490000000",
    "notes": "Redacted",
}

# Timestamp-valued keys. Their values are remapped, so they are safe to keep by name.
TIMESTAMP_KEYS = frozenset(
    {"createdAt", "updatedAt", "from", "to", "nextOffsetId", "offsetId", "dateOfBirth"}
)

REDACTED = "<unrecognised field; add it to scripts/record_principle_wire.py deliberately>"

FIRESTORE = RECORDINGS / "firestore"

# The Firestore collections the fake serves (tests/fake/firestore.py), keyed by recording name.
FIRESTORE_CATALOGUE = ("patient", "appointment", "treatment_step", "treatment_category",
                       "schedule_summaries", "roster_schedules", "calendar_events")

PRACTICE_TZ = ZoneInfo("Pacific/Auckland")

# Synthetic timestamps start here and step by a minute per distinct real value, which keeps
# them ordered and distinct without resembling any real appointment.
SYNTHETIC_EPOCH = datetime(2026, 1, 5, 20, 0, tzinfo=UTC)


class Pseudonymiser:
    """Replaces ids and timestamps with synthetic values, preserving structure.

    One instance per capture run, so a value appearing in two places maps to the same
    replacement in both -- that equality is exactly what the recordings are read for.

    Timestamps are assigned in **chronological** order, not order of appearance, and so need
    two passes: `observe` every document, then `freeze`, then `scrub`. Assigning them as they
    are encountered destroys ordering, which produced a recording whose second appointment
    ended four minutes before it began and whose createdAt ascended where Principle's
    descends. Order is a property the fake is built on; it has to survive.

    Durations do not survive: synthetic instants are spaced evenly, so a 30-minute appointment
    and a 60-minute one both render as one minute. Nothing reads a duration out of a recording,
    and preserving real gaps would leak the shape of a real day.
    """

    def __init__(self) -> None:
        self.unrecognised: set[str] = set()
        self._ids: dict[str, str] = {}
        self._timestamps: dict[str, str] = {}
        self._seen: dict[str, datetime] = {}
        self._frozen = False

    def observe(self, value: Any) -> None:
        """Collect every timestamp in a document, before any of them is assigned."""
        if isinstance(value, dict):
            for item in value.values():
                self.observe(item)
        elif isinstance(value, list):
            for item in value[:LIST_ELEMENTS_KEPT]:
                self.observe(item)
        elif isinstance(value, str):
            parsed = _as_timestamp(value)
            if parsed is not None:
                self._seen[value] = parsed

    def freeze(self) -> None:
        """Assign synthetic instants in chronological order of the real ones."""
        for index, original in enumerate(sorted(self._seen, key=lambda key: self._seen[key])):
            moment = SYNTHETIC_EPOCH + timedelta(minutes=index)
            # Principle answers "+00:00", not "Z"; the wire format is part of the shape.
            suffix = "Z" if original.endswith("Z") else "+00:00"
            self._timestamps[original] = moment.isoformat().replace("+00:00", "") + suffix
        self._frozen = True

    def identifier(self, value: str) -> str:
        if value not in self._ids:
            self._ids[value] = f"recorded-id-{len(self._ids) + 1:03d}"
        return self._ids[value]

    def scrub(self, value: Any, key: str = "") -> Any:
        """Replace identifying values, keeping keys, types and nesting intact.

        Unrecognised keys are redacted and recorded in `unrecognised`, so the operator is told
        what was dropped rather than trusting that nothing was missed.
        """
        if not self._frozen:
            raise RuntimeError("observe() every document and freeze() before scrubbing")
        if isinstance(value, dict):
            return {name: self.scrub(item, name) for name, item in value.items()}
        if isinstance(value, list):
            kept = value[:LIST_ELEMENTS_KEPT]
            if key in ID_LIST_KEYS:
                return [
                    self.identifier(item) if isinstance(item, str) else self.scrub(item, key)
                    for item in kept
                ]
            return [self.scrub(item, key) for item in kept]

        if key in SCRUBBED:
            return SCRUBBED[key]
        if key in ID_KEYS:
            return self.identifier(value) if isinstance(value, str) else value
        if isinstance(value, str) and value in self._timestamps:
            return self._timestamps[value]
        if key in STRUCTURAL_KEYS or key in TIMESTAMP_KEYS:
            return value
        if isinstance(value, bool | int | float) or value is None:
            return value
        self.unrecognised.add(key)
        return REDACTED


def _as_timestamp(value: str) -> datetime | None:
    """The instant this string names, or None when it is not a timestamp.

    `nextOffsetId` is routed here rather than to the id map: Principle returns a `createdAt`
    in that field, and mapping it as an opaque id would break its equality with the last
    row's timestamp -- the very property the fake is built on.
    """
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A bare date ("1970-01-01") is not an instant; dateOfBirth is replaced outright.
    return moment if moment.tzinfo is not None else None


def shape_of(value: Any) -> Any:
    """A value reduced to its keys and types: `<str>`, `<int>`, a dict or a one-item list.

    Firestore's typed values (`{"stringValue": ...}`) reduce to their type, so a document and
    the fake's rendering of it compare field by field.
    """
    if isinstance(value, dict):
        if len(value) == 1:
            (kind, raw), = value.items()
            if kind == "mapValue":
                return shape_of(raw.get("fields", {}))
            if kind == "arrayValue":
                return joined([shape_of({"mapValue": {"fields": {}}} if v is None else v)
                               for v in raw.get("values", [])])
            if kind.endswith("Value"):
                return f"<{kind.removesuffix('Value')}>"
        return {key: shape_of(item) for key, item in value.items()}
    if isinstance(value, list):
        return joined([shape_of(item) for item in value])
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int | float):
        return "<number>"
    return "<null>" if value is None else "<str>"


def joined(shapes: list[Any]) -> list[Any]:
    """The shapes of a list's items, joined: every key any item has."""
    merged: Any = None
    for shape in shapes:
        merged = union(merged, shape)
    return [] if merged is None else [merged]


def union(a: Any, b: Any) -> Any:
    """Two shapes joined. Differing leaf types are kept as `<a|b>`; None is no shape."""
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {key: union(a.get(key), b.get(key)) if key in a else b[key]
                for key in a.keys() | b.keys()}
    if isinstance(a, list) and isinstance(b, list):
        return joined(a + b)
    if a == b:
        return a
    kinds = sorted(set(str(a).strip("<>").split("|")) | set(str(b).strip("<>").split("|")))
    return "<" + "|".join(kinds) + ">"


def covers(recorded: Any, fake: Any, where: str = "") -> list[str]:
    """Where the fake's shape has a field or type the recording never showed."""
    if isinstance(fake, dict):
        if not isinstance(recorded, dict):
            return [f"{where or '.'}: the fake sends an object, Principle {recorded}"]
        return [problem for key, item in fake.items()
                for problem in ([f"{where}.{key}: Principle never sent this field"]
                                if key not in recorded else
                                covers(recorded[key], item, f"{where}.{key}"))]
    if isinstance(fake, list):
        if not isinstance(recorded, list):
            return [f"{where}: the fake sends a list, Principle {recorded}"]
        if not fake or not recorded:
            return []
        return covers(recorded[0], fake[0], f"{where}[]")
    kinds = set(str(recorded).strip("<>").split("|"))
    if not set(str(fake).strip("<>").split("|")) <= kinds | {"null"}:
        return [f"{where}: the fake sends {fake}, Principle {recorded}"]
    return []


def write_shape(name: str, documents: list[dict[str, Any]]) -> Path:
    """Save the joined shape of some Firestore documents' fields: names and types only."""
    if not documents:
        raise SystemExit(f"{name}: no documents to take a shape from; pick another day")
    target = FIRESTORE / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shape = joined([shape_of(d.get("fields", {})) for d in documents])[0]
    target.write_text(json.dumps({
        "capturedOn": datetime.now(tz=UTC).date().isoformat(),
        "source": "firestore (staging)", "documents": len(documents), "shape": shape,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def write(
    name: str, status: int, body: object, names: Pseudonymiser, *, refusal: bool = False
) -> Path:
    """Save one scrubbed recording, citing the day it was captured.

    The capture date is a day, not an instant: a precise timestamp on a file describing one
    practice's diary narrows down when that diary was read.
    """
    target = (RECORDINGS / "refusals" if refusal else RECORDINGS) / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "capturedOn": datetime.now(tz=UTC).date().isoformat(),
        "source": "api.staging.principle.dental",
        "status": status,
        "body": names.scrub(body),
    }
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def staging_settings() -> Settings:
    """Staging configuration, refusing production and missing credentials alike."""
    settings = Settings(environment=Environment.STAGING)
    if is_production_host(settings.api_base_url):
        raise ConfigurationError("the recorder addresses staging, never production")
    settings.require_credentials()
    return settings


def window() -> dict[str, str]:
    """A fortnight around today: enough diary to hold a shape, not a bulk export."""
    today = date.today()
    return {
        "from": f"{today - timedelta(days=7)}T00:00:00Z",
        "to": f"{today + timedelta(days=7)}T00:00:00Z",
    }


async def record_firestore(settings: Settings, appointment: dict[str, Any]) -> list[Path]:
    """The shape of every collection the fake serves, from one appointment's day on staging."""
    firestore = Firestore(settings)
    try:
        patient, ident = appointment["patientId"], appointment["id"]
        step = (f"patients/{patient}/treatmentPlans/{appointment['treatmentPlanId']}"
                f"/treatmentSteps/{appointment['treatmentStepId']}")
        day = datetime.fromisoformat(appointment["event"]["from"]).astimezone(
            PRACTICE_TZ).date()
        start = datetime.combine(day, datetime.min.time(), tzinfo=PRACTICE_TZ)

        def query(collection: str, *filters: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
            where = [{"fieldFilter": {"field": {"fieldPath": p}, "op": o, "value": v}}
                     for p, o, v in filters]
            body: dict[str, Any] = {"from": [{"collectionId": collection}]}
            if where:
                body["where"] = (where[0] if len(where) == 1
                                 else {"compositeFilter": {"op": "AND", "filters": where}})
            return {"structuredQuery": body}

        def found(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [row["document"] for row in rows if "document" in row]

        def stamp(moment: datetime) -> dict[str, str]:
            return {"timestampValue": moment.astimezone(UTC).isoformat().replace("+00:00", "Z")}

        category = appointment.get("treatmentCategory", {}).get("id")
        return [
            write_shape("patient", [await firestore.read(f"patients/{patient}")]),
            write_shape("appointment", [await firestore.read(
                f"patients/{patient}/appointments/{ident}")]),
            write_shape("treatment_step", [await firestore.read(step)]),
            write_shape("treatment_category", found(await firestore.read(
                "", query("treatmentCategories"))) if category is None else
                [await firestore.read(f"treatmentCategories/{category}")]),
            write_shape("schedule_summaries", found(await firestore.read(
                f"practices/{settings.practice_id}",
                query("scheduleSummaries", ("day", "EQUAL", {"stringValue": day.isoformat()}))))),
            write_shape("roster_schedules", found(await firestore.read(
                f"staff/{appointment['practitionerId']}", query("rosterSchedules")))),
            write_shape("calendar_events", found(await firestore.read("", query(
                "calendarEvents", ("event.from", "GREATER_THAN_OR_EQUAL", stamp(start)),
                ("event.from", "LESS_THAN", stamp(start + timedelta(days=7))))))),
        ]
    finally:
        await firestore.aclose()


async def record_firestore_refusals(settings: Settings) -> list[Path]:
    """Firestore's answers to a missing document and a bad token, with the path as a slot."""
    firestore = Firestore(settings)
    try:
        token = await firestore.authenticate()
        base = settings.firebase_url("firestore") + "/v1/" + firestore.root
        written = []
        for name, path, bearer in (("firestore_not_found", "patients/recorded-missing", token),
                                   ("firestore_unauthenticated", "patients/recorded-any",
                                    "not-a-token")):
            response = await firestore.client.get(
                f"{base}/{path}", headers={"Authorization": "Bearer " + bearer})
            if response.is_success:
                raise SystemExit(f"{name}: expected a refusal, got {response.status_code}")
            text = response.text.replace(f"{firestore.root}/{path}", "{document}")
            if firestore.root in text or settings.firebase_project in text:
                raise SystemExit(f"{name}: the refusal still names the real database")
            target = RECORDINGS / "refusals" / f"{name}.json"
            target.write_text(json.dumps({
                "capturedOn": datetime.now(tz=UTC).date().isoformat(),
                "source": "firestore (staging)", "status": response.status_code,
                "body": json.loads(text)}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            written.append(target)
        return written
    finally:
        await firestore.aclose()


async def record_patient(client: PrincipleClient, practice_id: str, names: Pseudonymiser,
                         appointment: dict[str, Any]) -> list[Path]:
    """getPatient, searchPatients and createPatient: the fake's patient round trip."""
    patient = await client.get("getPatient", path_params={"patientId": appointment["patientId"]})
    search = await client.get("searchPatients", query={"name": patient["name"]})
    created = await client.call("createPatient", {
        "practiceId": practice_id, "name": f"[TEST] Recording {date.today()}",
        "dateOfBirth": "1970-01-01", "gender": "notSpecified",
        "email": "recording@example.invalid"})
    for document in (patient, search, created):
        names.observe(document)
    names.freeze()
    return [write("patient", 200, patient, names), write("search_patients", 200, search, names),
            write("create_patient", 201, created, names)]


async def record_successes(
    client: PrincipleClient, practice_id: str, names: Pseudonymiser
) -> list[Path]:
    """Capture one page of each verified diary listing.

    All three share one Pseudonymiser, so the practice id in the appointment rows maps to the
    same synthetic value as the practice's own id -- the cross-references between recordings
    are part of the shape.
    """
    practices = await client.get("listPractices")
    practitioners = await client.get(
        "listPractitioners", path_params={"practiceId": practice_id}
    )
    appointments = await client.get(
        "listAppointmentsByDateRange", query={"practiceId": practice_id, "limit": 2, **window()}
    )
    for document in (practices, practitioners, appointments):
        names.observe(document)
    names.freeze()
    return [
        write("practices", 200, practices, names),
        write("practitioners", 200, practitioners, names),
        write("appointments", 200, appointments, names),
    ]


async def first_appointment(client: PrincipleClient, practice_id: str) -> dict[str, Any]:
    """A booked appointment in the recording window, to read its documents from."""
    async for row in client.rows("listAppointmentsByDateRange",
                                 query={"practiceId": practice_id, **window()}):
        if row["status"] != "cancelled" and row.get("treatments"):
            return row
    raise SystemExit("no booked appointment within a week of today on staging")


async def record_refusals(settings: Settings, names: Pseudonymiser) -> list[Path]:
    """Provoke each error the fake needs to be able to speak, and save what was said."""
    written: list[Path] = []

    no_key = settings.model_copy(update={"api_key": SecretStr("")})
    async with PrincipleClient(no_key) as client:
        written.append(await _provoke(client, "unauthorised", "listPractices", names))

    # Deliberately not provoked: an unplaceable offsetId, a missing required parameter, and
    # an out-of-range limit. The first is not a refusal at all -- Principle ignores it and
    # answers page one (tests/integration/test_pagination_contract.py) -- and the client
    # refuses the others before a request leaves, so there is no real wording to capture.
    return written


async def _provoke(
    client: PrincipleClient,
    name: str,
    call: str,
    names: Pseudonymiser,
    query: dict[str, Any] | None = None,
) -> Path:
    try:
        envelope = await client.get(call, query=query)
    except PrincipleError as refused:
        return write(name, refused.status, refused.body, names, refusal=True)
    raise SystemExit(
        f"{name}: expected {call} to be refused but it answered {json.dumps(envelope)[:200]}; "
        "the fake must not be given a refusal the API does not actually produce"
    )


async def main() -> None:
    """Capture every recording the fake needs, successes and refusals alike."""
    settings = staging_settings()
    print(f"recording from {settings.api_base_url}")
    names = Pseudonymiser()
    async with PrincipleClient(settings) as client:
        written = await record_successes(client, settings.practice_id, names)
        appointment = await first_appointment(client, settings.practice_id)
        written += await record_patient(client, settings.practice_id, Pseudonymiser(),
                                        appointment)
    written += await record_refusals(settings, names)
    written += await record_firestore(settings, appointment)
    written += await record_firestore_refusals(settings)
    for path in written:
        print(f"  wrote {path.relative_to(Path.cwd())}")
    if names.unrecognised:
        print(
            "\nRedacted unrecognised field(s): "
            + ", ".join(sorted(names.unrecognised))
            + "\nEach was replaced, not passed through. Decide for each whether it is"
            " structural (add it to STRUCTURAL_KEYS), an id, a timestamp, or genuinely"
            " identifying -- then re-record."
        )


if __name__ == "__main__":
    asyncio.run(main())
