"""Capture exactly what Principle and Firestore send, so the fakes can be checked against it.

    uv run python -m scripts.record_principle_wire [--create-patient] [--older-than [DAYS]]

Every response is kept bit for bit (ADR 0007): the status, the headers and the body bytes as
they came off the wire, captured at the transport under every client before anything parses
them. Nothing is redacted, remapped or reformatted. The recordings therefore hold patient
records and live in production's data folder, never in this checkout. Each operation's
exchanges are `<operationId>/NNN.json` (when, which request, status, headers) and `NNN.body`;
Firestore's are named by collection, `firestore/<collection>/`, and Firebase sign-in's
`firebase/<call>/`. Request headers and bodies are not kept: they carry the API key and the
password. Firebase's sign-in answers are kept like any other, tokens included, so the
recordings folder is as private as `.env`.

Reads go to production and writes to staging (ADR 0006). With --create-patient the recorder
makes a `[TEST] Fake` patient on staging, which the API cannot delete, and updates it, so the
fake's patient writes are checked against what Principle answers. The patient carries the
owner's own mobile and email (ADMIN_OWNER_MOBILE, ADMIN_OWNER_EMAIL), so anything Principle
sends it reaches the owner and no patient. The writes run after every read, and nothing is
written to disk until the whole run has succeeded, so a failed run leaves no patient for its
re-run to duplicate and the previous recordings as they were. Staging's exchanges go in
staging's own data folder.

Refusals are Principle's and Firestore's own wording, and are also written into
`tests/recordings/refusals/`, which tests/fake/server.py reads. There a Firestore refusal's
document path, which names the practice's database, becomes `{document}`.

`--older-than` records nothing: it lists recordings taken more than DAYS ago and fails if any
are, or if there are none.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.firestore import Firestore
from dental_practice_admin.principle import CATALOGUE, PrincipleClient, RecordError

REFUSALS = Path(__file__).resolve().parent.parent / "tests" / "recordings" / "refusals"

# The Firestore collections the fake serves (tests/fake/firestore.py), as recorded.
FIRESTORE_CATALOGUE = ("patients", "appointments", "treatmentSteps", "treatmentCategories",
                       "scheduleSummaries", "rosterSchedules", "calendarEvents")

PRACTICE_TZ = ZoneInfo("Pacific/Auckland")


def transport_for(settings: Settings) -> httpx.AsyncBaseTransport:
    """The network, for one environment's clients."""
    return httpx.AsyncHTTPTransport()


@dataclass(frozen=True)
class Exchange:
    """One response as it came off the wire, named by the operation it answered."""

    name: str
    method: str
    url: str
    status: int
    headers: list[tuple[str, str]]
    body: bytes


class Capture(httpx.AsyncBaseTransport):
    """Keeps every response exactly as received, below every client that parses it."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.exchanges: list[Exchange] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        body = b"".join([chunk async for chunk in response.aiter_raw()])
        await response.aclose()
        self.exchanges.append(Exchange(
            name_of(request), request.method, str(request.url), response.status_code,
            [(k.decode("latin-1"), v.decode("latin-1")) for k, v in response.headers.raw],
            body))
        return httpx.Response(response.status_code, headers=response.headers, content=body,
                              request=request, extensions=response.extensions)

    async def aclose(self) -> None:
        await self.inner.aclose()


def name_of(request: httpx.Request) -> str:
    """Which operation a request is: its operationId, Firestore collection or Firebase call."""
    path = request.url.path
    if "/documents/" in path:
        documents, _, queried = path.partition(":")
        if queried:
            source = json.loads(request.content)["structuredQuery"]["from"][0]
            return f"firestore/{source['collectionId']}"
        return "firestore/" + documents.split("/")[-2]
    if path.startswith("/v1/accounts:") or path == "/v1/token":
        return "firebase/" + path.rsplit("/", 1)[-1].replace(":", "-")
    for call in CATALOGUE:
        if call.method == request.method and re.fullmatch(
                re.sub(r"\{[^}]+\}", "[^/]+", call.path), path):
            return call.name
    raise SystemExit(f"{request.method} {path} is no operation this recorder knows")


def recordings_of(settings: Settings) -> Path:
    """Where an environment's recordings live: its data folder, never this checkout."""
    return settings.data_dir / "recordings"


def recorded(settings: Settings, name: str) -> list[Any]:
    """The successful answers recorded for one operation, decoded as its client decodes them."""
    answers = []
    for meta in sorted((recordings_of(settings) / name).glob("*.json")):
        exchange = json.loads(meta.read_text(encoding="utf-8"))
        if 200 <= exchange["status"] < 300:
            answers.append(httpx.Response(exchange["status"], headers=exchange["headers"],
                                          content=meta.with_suffix(".body").read_bytes()).json())
    return answers


def as_text(document: object) -> str:
    """A JSON file's contents."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def write_recordings(exchanges: list[Exchange], into: Path) -> None:
    """Replace each recorded operation's exchanges, numbered in the order made.

    An operation this run did not make keeps its recording.
    """
    counts: dict[str, int] = {}
    for exchange in exchanges:
        if exchange.name not in counts:
            shutil.rmtree(into / f"{exchange.name}.new", ignore_errors=True)
        counts[exchange.name] = counts.get(exchange.name, 0) + 1
        stem = into / f"{exchange.name}.new" / f"{counts[exchange.name]:03d}"
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_suffix(".body").write_bytes(exchange.body)
        stem.with_suffix(".json").write_text(as_text({
            "capturedOn": date.today().isoformat(), "method": exchange.method,
            "url": exchange.url, "status": exchange.status, "headers": exchange.headers}),
            encoding="utf-8")
    # Every operation is written in full before any recording is replaced.
    for name in counts:
        shutil.rmtree(into / name, ignore_errors=True)
        (into / f"{name}.new").rename(into / name)
    print(f"  wrote {len(exchanges)} exchanges to {into}")


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
    if not set(str(fake).strip("<>").split("|")) <= kinds:
        return [f"{where}: the fake sends {fake}, Principle {recorded}"]
    return []


def configured(environment: Environment) -> Settings:
    """One environment's configuration, refusing missing credentials."""
    settings = Settings(environment=environment)
    settings.require_credentials()
    return settings


def window() -> dict[str, str]:
    """A fortnight around today: enough diary to hold a shape, not a bulk export."""
    today = date.today()
    return {
        "from": f"{today - timedelta(days=7)}T00:00:00Z",
        "to": f"{today + timedelta(days=7)}T00:00:00Z",
    }


async def record_firestore(settings: Settings, capture: Capture,
                           appointments: list[dict[str, Any]]) -> None:
    """Every collection the fake serves, read for the sampled appointments.

    Fields vary between documents (a step charted to a tooth or to the whole mouth), so one
    document is too few to stand for a collection. The first appointment's day supplies the
    schedule summaries and calendar events.
    """
    firestore = Firestore(settings, transport=capture)
    try:
        appointment = appointments[0]
        day = datetime.fromisoformat(appointment["event"]["from"]).astimezone(
            PRACTICE_TZ).date()
        start = datetime.combine(day, datetime.min.time(), tzinfo=PRACTICE_TZ)

        def found(name: str, rows: list[dict[str, Any]]) -> None:
            if not any("document" in row for row in rows):
                raise SystemExit(f"{name}: no documents to record; pick another day")

        def stamp(moment: datetime) -> dict[str, str]:
            return {"timestampValue": moment.astimezone(UTC).isoformat().replace("+00:00", "Z")}

        for patient in sorted({a["patientId"] for a in appointments}):
            await firestore.read(f"patients/{patient}")
        for a in appointments:
            await firestore.read(f"patients/{a['patientId']}/appointments/{a['id']}")
            await firestore.read(f"patients/{a['patientId']}/treatmentPlans/"
                                 f"{a['treatmentPlanId']}/treatmentSteps/{a['treatmentStepId']}")
        for category in sorted({a["treatmentCategory"]["id"] for a in appointments
                                if a.get("treatmentCategory")}):
            await firestore.read(f"treatmentCategories/{category}")
        rosters = [row for staff in sorted({a["practitionerId"] for a in appointments})
                   for row in await firestore.read(f"staff/{staff}", query("rosterSchedules"))]
        found("rosterSchedules", rosters)
        found("scheduleSummaries", await firestore.read(
            f"practices/{settings.practice_id}",
            query("scheduleSummaries", ("day", "EQUAL", {"stringValue": day.isoformat()}))))
        found("calendarEvents", await firestore.read("", query(
            "calendarEvents", ("event.from", "GREATER_THAN_OR_EQUAL", stamp(start)),
            ("event.from", "LESS_THAN", stamp(start + timedelta(days=7))))))
    finally:
        await firestore.aclose()


async def record_firestore_refusals(settings: Settings,
                                    capture: Capture) -> list[tuple[Path, str]]:
    """Firestore's answers to a missing document and a bad token, for the fake to speak."""
    firestore = Firestore(settings, transport=capture)
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
            written.append((REFUSALS / f"{name}.json", as_text({
                "capturedOn": date.today().isoformat(),
                "source": f"firestore ({settings.environment.value})",
                "status": response.status_code,
                "body": json.loads(text)})))
        return written
    finally:
        await firestore.aclose()


async def record_patient(client: PrincipleClient, appointment: dict[str, Any]) -> None:
    """The appointment's patient, by id and by name search."""
    async def as_sent(name: str, **arguments: Any) -> dict[str, Any]:
        # A phone number or email that breaks the specification is real data, not a reason
        # to leave the record out.
        try:
            return await client.get(name, **arguments)
        except RecordError as error:
            return error.body

    patient = await as_sent("getPatient", path_params={"patientId": appointment["patientId"]})
    await as_sent("searchPatients", query={"name": patient["name"]})


async def record_writes(staging: Settings, capture: Capture) -> dict[str, float]:
    """Create a `[TEST]` patient on staging carrying the owner's details, then update it.

    It runs only with --create-patient, and last, so a run that fails leaves no patient behind
    for its re-run to duplicate. The patient is read back before it is written to again, and
    the update is refused unless it carries the owner's mobile and email (ADR 0007).
    """
    async with PrincipleClient(staging, transport=capture) as writer:
        created = await writer.call("createPatient", {
            "practiceId": staging.practice_id,
            "name": f"[TEST] Fake Recording {date.today()}",
            "dateOfBirth": "1970-01-01", "gender": "notSpecified",
            "contactNumbers": [{"label": "mobile", "number": staging.owner_mobile}],
            "email": staging.owner_email})
        searched = await search_lag(writer, created)
        patient = await writer.call("getPatient", {"patientId": created["id"]})
        numbers = [number["number"] for number in patient["contactNumbers"]]
        if staging.owner_mobile not in numbers or patient["email"] != staging.owner_email:
            raise SystemExit(f"{created['id']} does not carry the owner's mobile and email;"
                             " nothing more is written to it")
        await writer.call("updatePatient", {
            "patientId": created["id"], "practiceId": staging.practice_id,
            **{key: patient[key] for key in ("name", "dateOfBirth", "gender", "email")},
            "address": "[TEST] 1 Fake Street"})
        await writer.call("getPatient", {"patientId": created["id"]})
    return {"searchFindsCreatedPatientAfterSeconds": searched}


def keep_measurement(path: Path, measured: dict[str, float]) -> None:
    """Add this run's measurements to every earlier run's (ADR 0009)."""
    runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if isinstance(runs, dict):
        runs = [runs]
    path.write_text(as_text([*runs, {"capturedOn": date.today().isoformat(), **measured}]),
                    encoding="utf-8")


# Longer than Principle has ever taken to index a new patient, so a run that waits this long
# has found a fault rather than a delay.
SEARCH_GIVE_UP = 60.0


async def search_lag(writer: PrincipleClient, created: dict[str, Any]) -> float:
    """Seconds until Principle's name search finds a patient just created."""
    started = time.monotonic()
    while time.monotonic() - started < SEARCH_GIVE_UP:
        rows = (await writer.call("searchPatients", {"name": created["name"]}))["data"]
        if any(row["id"] == created["id"] for row in rows):
            return round(time.monotonic() - started, 1)
        await asyncio.sleep(0.25)
    raise SystemExit(f"search did not find {created['id']} within {SEARCH_GIVE_UP:.0f} s")


async def record_successes(client: PrincipleClient, practice_id: str) -> None:
    """One page of each verified diary listing."""
    await client.get("listPractices")
    await client.get("listPractitioners", path_params={"practiceId": practice_id})
    await client.get("listAppointmentsByDateRange",
                     query={"practiceId": practice_id, "limit": 2, **window()})


async def record_reconciliation(client: PrincipleClient) -> None:
    """Invoices and payments changed in the last week, read as bank reconciliation reads them."""
    since = f"{date.today() - timedelta(days=7)}T00:00:00Z"
    for name in ("listInvoicesByDateRange", "listTransactionsByDateRange"):
        if not [row async for row in client.rows(name, query={"updatedFrom": since})]:
            raise SystemExit(f"{name}: nothing changed since {since[:10]}; nothing to record")


def query(collection: str, *filters: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    """A Firestore structured query over one collection, its filters joined with AND."""
    where = [{"fieldFilter": {"field": {"fieldPath": p}, "op": o, "value": v}}
             for p, o, v in filters]
    body: dict[str, Any] = {"from": [{"collectionId": collection}]}
    if where:
        body["where"] = (where[0] if len(where) == 1
                         else {"compositeFilter": {"op": "AND", "filters": where}})
    return {"structuredQuery": body}


async def first_appointment(client: PrincipleClient, settings: Settings,
                            capture: Capture) -> dict[str, Any]:
    """A booked appointment on a day with a schedule summary, to read its documents from."""
    search = window()
    firestore = Firestore(settings, transport=capture)
    checked: dict[date, bool] = {}
    try:
        async for row in client.rows("listAppointmentsByDateRange",
                                     query={"practiceId": settings.practice_id, **search}):
            if row["status"] == "cancelled" or not row.get("treatments"):
                continue
            day = datetime.fromisoformat(row["event"]["from"]).astimezone(PRACTICE_TZ).date()
            if day not in checked:
                found = await firestore.read(f"practices/{settings.practice_id}", query(
                    "scheduleSummaries", ("day", "EQUAL", {"stringValue": day.isoformat()})))
                checked[day] = any("document" in r for r in found)
            if checked[day]:
                return row
    finally:
        await firestore.aclose()
    raise SystemExit(f"no booked appointment on a day with a schedule summary in "
                     f"{search['from'][:10]}..{search['to'][:10]} ({settings.environment.value})")


# Documents read per collection: enough for fields that only some documents carry (a step
# charted to a tooth) to appear, few enough to keep a run short.
DOCUMENTS_SAMPLED = 40


async def sample(client: PrincipleClient, settings: Settings,
                 appointment: dict[str, Any]) -> list[dict[str, Any]]:
    """`appointment` first, then other booked appointments from the fortnight around today."""
    rows = [appointment]
    async for row in client.rows("listAppointmentsByDateRange",
                                 query={"practiceId": settings.practice_id, **window()}):
        if len(rows) == DOCUMENTS_SAMPLED:
            break
        if row["id"] != appointment["id"] and row["status"] != "cancelled" \
                and row.get("treatments"):
            rows.append(row)
    return rows


async def record_refusals(settings: Settings, capture: Capture) -> list[tuple[Path, str]]:
    """Provoke each error the fake needs to be able to speak, and keep what was said."""
    # The client's PrincipleError carries no response body, by design, so refusals are read
    # from the raw response: the recording is Principle's own words or nothing.
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=60,
                                 transport=capture) as raw:
        refused = await raw.get("/v1/practices", headers={"X-API-Key": ""})
    if refused.is_success:
        raise SystemExit("unauthorised: an empty key was answered; nothing to record")
    if "json" not in refused.headers.get("content-type", ""):
        raise SystemExit(f"unauthorised: the refusal is not JSON ({refused.status_code})")
    # Deliberately not provoked: an unplaceable offsetId, a missing required parameter, and
    # an out-of-range limit. The first is not a refusal at all -- Principle ignores it and
    # answers page one (tests/integration/test_pagination_contract.py) -- and the client
    # refuses the others before a request leaves, so there is no real wording to capture.
    return [(REFUSALS / "unauthorised.json", as_text({
        "capturedOn": date.today().isoformat(), "source": refused.url.host,
        "status": refused.status_code, "body": refused.json()}))]


def report_old(days: int) -> None:
    """Name every recording taken more than `days` ago; fail if there is one, or none at all."""
    recordings = recordings_of(Settings(environment=Environment.PRODUCTION))
    if not any(recordings.rglob("*.json")):
        print(f"no recordings in {recordings}; record them, or check ADMIN_DATA_ROOT")
        raise SystemExit(1)
    old = [path for folder in (recordings, REFUSALS) for path in sorted(folder.rglob("*.json"))
           if date.today() - date.fromisoformat(
               json.loads(path.read_text(encoding="utf-8"))["capturedOn"])
           > timedelta(days=days)]
    for path in old:
        print(f"  older than {days} days: {path}")
    if old:
        raise SystemExit(1)


async def main() -> None:
    """Capture every recording the fake needs, successes and refusals alike."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--create-patient", action="store_true",
                        help="also create an undeletable [TEST] patient on staging")
    parser.add_argument("--older-than", type=int, nargs="?", const=60, metavar="DAYS",
                        help="list recordings older than DAYS (%(const)s) and fail if any;"
                             " records nothing")
    arguments = parser.parse_args()
    if arguments.older_than is not None:
        report_old(arguments.older_than)
        return
    settings = configured(Environment.PRODUCTION)
    staging = configured(Environment.STAGING) if arguments.create_patient else None
    if staging is not None:
        staging.require_owner_contact()
    print(f"recording from {settings.api_base_url}")
    capture = Capture(transport_for(settings))
    async with PrincipleClient(settings, transport=capture) as client:
        await record_successes(client, settings.practice_id)
        await record_reconciliation(client)
        appointment = await first_appointment(client, settings, capture)
        await record_patient(client, appointment)
        appointments = await sample(client, settings, appointment)
    refusals = await record_refusals(settings, capture)
    await record_firestore(settings, capture, appointments)
    refusals += await record_firestore_refusals(settings, capture)
    if staging is not None:
        writing = Capture(transport_for(staging))
        measured = await record_writes(staging, writing)
        write_recordings(writing.exchanges, recordings_of(staging))
        keep_measurement(recordings_of(staging) / "measurements.json", measured)
    write_recordings(capture.exchanges, recordings_of(settings))
    for target, text in refusals:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"  wrote {target}")


if __name__ == "__main__":
    asyncio.run(main())
