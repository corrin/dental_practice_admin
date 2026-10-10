"""The fake Principle's own data state.

A relational store, not a bag of canned responses: one table per resource the application
touches, with a real column for every field the API filters, orders or keys on. Every answer
is computed from this state -- a stored response stops being true the moment state changes.

The paging behaviour here is **not** what the specification describes. It is what
api.staging.principle.dental was observed doing on 2026-10-01, captured in
tests/recordings/ and pinned by tests/integration/test_pagination_contract.py:

  * `meta.total` is the number of rows on **this page**, not the size of the result set.
    `limit=1` answers `total: 1`; `limit=100` answers `total: 100`. It is useless as a count.
  * `meta.nextOffsetId` is the last row's **createdAt**, not its id, and the ordering is
    `createdAt` descending. The spec calls it "id of the last record".
  * An `offsetId` the server cannot place is **ignored**, answering the first page, rather
    than refused.
  * `nextOffsetId` is absent once a page is not full, and the default `limit` is 20.
  * `/v1/practices` and `/v1/practices/{id}/practitioners` carry no `meta` at all and ignore
    `limit` -- they are not paginated.

Implementing the spec instead would make the fake kinder than Principle, and a report that
trusted `total` would pass here and be wrong in the practice.

In-memory, one store per test. `seed()` builds a small practice whose name carries
"(FAKE PRINCIPLE)" so a fake run can never be read as a real one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dental_practice_admin.config import FAKE_API_KEY as FAKE_API_KEY
from dental_practice_admin.config import FAKE_PRACTICE_ID as FAKE_PRACTICE_ID

SCHEMA = """
CREATE TABLE practices (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE practitioners (
    id          TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX practitioners_by_practice ON practitioners(practice_id, id);

CREATE TABLE patients (
    id            TEXT PRIMARY KEY,
    practice_id   TEXT NOT NULL REFERENCES practices(id),
    name          TEXT NOT NULL,
    phone         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    gender        TEXT NOT NULL DEFAULT 'notSpecified',
    email         TEXT NOT NULL,
    address       TEXT,
    date_of_birth TEXT
);
-- The patient's pinned notes, which Principle copies onto each timeline card.
CREATE TABLE patient_notes (
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    position    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (patient_id, position)
);
CREATE TABLE invoices (
    id          TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    reference   TEXT NOT NULL,
    status      TEXT NOT NULL,
    total       REAL NOT NULL,
    paid        REAL NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
-- One row per invoice a payment pays, sharing the payment's id, as Principle lists them.
CREATE TABLE transactions (
    id          TEXT NOT NULL,
    invoice_id  TEXT NOT NULL REFERENCES invoices(id),
    practice_id TEXT NOT NULL REFERENCES practices(id),
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    amount      REAL NOT NULL,
    method      TEXT,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, invoice_id)
);

CREATE TABLE appointments (
    id                  TEXT PRIMARY KEY,
    practice_id         TEXT NOT NULL REFERENCES practices(id),
    practitioner_id     TEXT NOT NULL REFERENCES practitioners(id),
    patient_id          TEXT NOT NULL REFERENCES patients(id),
    treatment_option_id TEXT NOT NULL,
    treatment_plan_id   TEXT NOT NULL,
    treatment_step_id   TEXT NOT NULL,
    status              TEXT NOT NULL,
    event_from          TEXT NOT NULL,
    event_to            TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    category_id         TEXT REFERENCES treatment_categories(id),
    plan_name           TEXT NOT NULL DEFAULT 'Treatment plan',
    step_name           TEXT NOT NULL DEFAULT 'Step 1',
    -- Booked through online booking: the appointment document's appointmentRequestRef.
    online              INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX appointments_by_window ON appointments(practice_id, event_from, event_to);
CREATE INDEX appointments_by_cursor ON appointments(created_at DESC, id);
CREATE INDEX appointments_by_practitioner ON appointments(practitioner_id, event_from);
CREATE INDEX appointments_by_status ON appointments(status);

CREATE TABLE treatment_categories (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    colour_name  TEXT,
    colour_value TEXT,
    deleted      INTEGER NOT NULL DEFAULT 0
);
-- One row per treatment on an appointment's step. `id` is the API's treatment id and the
-- step document's treatment `uuid`.
CREATE TABLE appointment_treatments (
    id             TEXT PRIMARY KEY,
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    position       INTEGER NOT NULL,
    description    TEXT NOT NULL,
    quadrant       INTEGER,
    quadrant_index INTEGER,
    -- Comma-separated, in charted order: 'occlusal,mesial'. NULL with no tooth is whole-mouth.
    surfaces       TEXT,
    price_cents    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE appointment_tags (
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    position       INTEGER NOT NULL,
    name           TEXT NOT NULL,
    PRIMARY KEY (appointment_id, position)
);
-- A staff member's recurring block. Weekly is the only repetition seen in production.
CREATE TABLE roster_items (
    id          TEXT PRIMARY KEY,
    staff_id    TEXT NOT NULL REFERENCES practitioners(id),
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    from_time   TEXT NOT NULL,
    to_time     TEXT NOT NULL,
    days        TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    ending_date TEXT,
    deleted     INTEGER NOT NULL DEFAULT 0
);
-- One occurrence removed from a roster item: the block's start instant on that day, UTC.
CREATE TABLE roster_deletions (
    roster_id   TEXT NOT NULL REFERENCES roster_items(id),
    at          TEXT NOT NULL
);
-- Non-roster events: a block edited for one day (`schedule_ref` names its roster item) and
-- pending online booking requests.
CREATE TABLE calendar_events (
    id           TEXT PRIMARY KEY,
    practice_id  TEXT NOT NULL REFERENCES practices(id),
    staff_id     TEXT NOT NULL REFERENCES practitioners(id),
    type         TEXT NOT NULL,
    title        TEXT NOT NULL,
    event_from   TEXT NOT NULL,
    event_to     TEXT NOT NULL,
    schedule_ref TEXT REFERENCES roster_items(id),
    step_name    TEXT,
    deleted      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""

# The practice is in New Zealand; the diary task asks for local days, so the seed sits in
# local business hours.
PRACTICE_TZ = ZoneInfo("Pacific/Auckland")

# Principle's own default when a request omits `limit` (observed 2026-10-01).
SERVER_DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class Page:
    """One page of a listing, and the cursor that follows it.

    `total` is this page's row count, matching Principle. Nothing may read it as a count of
    the whole result set.
    """

    rows: list[dict[str, object]]
    total: int
    next_offset_id: str | None


class FakeStore:
    """The practice's state, queried the way the API lets a caller query it."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- reads ---------------------------------------------------------------

    def practices(self) -> list[dict[str, object]]:
        """Every practice. Not paginated, and no `meta` on the wire."""
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in self.db.execute("SELECT * FROM practices ORDER BY id")
        ]

    def practitioners(self, practice_id: str) -> list[dict[str, object]]:
        """Every practitioner at a practice.

        Takes no limit: the real endpoint ignores one and returns the lot.
        """
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "treatmentOptions": [],
            }
            for row in self.db.execute(
                "SELECT * FROM practitioners WHERE practice_id = ? ORDER BY id",
                (practice_id,),
            )
        ]

    def appointments(
        self,
        practice_id: str,
        window_from: str,
        window_to: str,
        limit: int = SERVER_DEFAULT_LIMIT,
        offset_id: str | None = None,
        practitioner_id: str | None = None,
        status: str | None = None,
    ) -> Page:
        """Appointments whose event window intersects [from, to), newest-created first.

        The half-open intersecting window is the spec's (`listAppointmentsByDateRange`): an
        appointment starting before the window but running into it matches, which is the one
        a staff member asks about most -- the appointment already in progress.

        Paging is a `createdAt` descending keyset. An `offset_id` that does not parse as a
        timestamp is ignored rather than refused, because that is what Principle does.
        """
        where = [
            "practice_id = :practice_id",
            "event_from < :window_to",
            "event_to > :window_from",
        ]
        params: dict[str, object] = {
            "practice_id": practice_id,
            "window_from": canonical(window_from),
            "window_to": canonical(window_to),
        }
        if practitioner_id is not None:
            where.append("practitioner_id = :practitioner_id")
            params["practitioner_id"] = practitioner_id
        if status is not None:
            where.append("status = :status")
            params["status"] = status

        cursor = _parse_cursor(offset_id)
        if cursor is not None:
            where.append("created_at < :cursor")
            params["cursor"] = cursor

        rows = self.db.execute(
            "SELECT * FROM appointments WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id LIMIT :limit",
            params | {"limit": limit},
        ).fetchall()

        # `nextOffsetId` appears only while the page is full, and is the last row's createdAt.
        full = len(rows) == limit
        return Page(
            rows=[self._render_appointment(row) for row in rows],
            total=len(rows),
            next_offset_id=wire(rows[-1]["created_at"]) if full and rows else None,
        )

    def _render_appointment(self, row: sqlite3.Row) -> dict[str, object]:
        category = self.db.execute("SELECT id, name FROM treatment_categories WHERE id = ?",
                                   (row["category_id"],)).fetchone()
        appointment: dict[str, object] = {
            "id": row["id"],
            "practiceId": row["practice_id"],
            "practitionerId": row["practitioner_id"],
            "patientId": row["patient_id"],
            "treatmentOptionId": row["treatment_option_id"],
            "treatmentPlanId": row["treatment_plan_id"],
            "treatmentStepId": row["treatment_step_id"],
            "status": row["status"],
            "event": {"from": wire(row["event_from"]), "to": wire(row["event_to"])},
            "createdAt": wire(row["created_at"]),
            "updatedAt": wire(row["updated_at"]),
            "treatments": [
                {"id": t["id"], "description": t["description"],
                 "treatmentStepId": row["treatment_step_id"],
                 "practitionerId": row["practitioner_id"],
                 "basePriceInCents": t["price_cents"], "totalInCents": t["price_cents"],
                 "taxInCents": 0, "serviceCodes": [],
                 **({"treatmentCategoryName": category["name"]} if category else {})}
                for t in self.db.execute("SELECT * FROM appointment_treatments"
                                         " WHERE appointment_id = ? ORDER BY position",
                                         (row["id"],))],
        }
        if category is not None:
            appointment["treatmentCategory"] = {"id": category["id"], "name": category["name"]}
        return appointment

    # -- writes (seeding is a write too) -------------------------------------

    def add_practice(self, practice_id: str, name: str, at: str) -> None:
        self.db.execute(
            "INSERT INTO practices VALUES (:id, :name, :at, :at)",
            {"id": practice_id, "name": name, "at": canonical(at)},
        )

    def add_practitioner(self, ident: str, practice_id: str, name: str, at: str) -> None:
        self.db.execute(
            "INSERT INTO practitioners VALUES (:id, :practice_id, :name, :at, :at)",
            {"id": ident, "practice_id": practice_id, "name": name, "at": canonical(at)},
        )

    def add_patient(self, ident: str, practice_id: str, name: str, at: str,
                    phone: str | None = None, notes: tuple[str, ...] = ()) -> None:
        self.db.execute(
            "INSERT INTO patients (id, practice_id, name, phone, created_at, updated_at, email,"
            " address) VALUES (:id, :practice_id, :name, :phone, :at, :at, :email, :address)",
            {"id": ident, "practice_id": practice_id, "name": name, "phone": phone,
             "at": canonical(at), "email": f"{ident}@fake.invalid", "address": "1 Fake Street"},
        )
        self.db.executemany("INSERT INTO patient_notes VALUES (?, ?, ?)",
                            [(ident, n, text) for n, text in enumerate(notes)])

    def create_patient(self, body: dict[str, Any], at: str) -> dict[str, object]:
        """A patient created through `POST /v1/patients`, with an id minted by the store."""
        count = self.db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        ident = f"fake-created-{count:04d}"
        numbers = body.get("contactNumbers") or []
        self.db.execute(
            "INSERT INTO patients (id, practice_id, name, phone, created_at, updated_at, gender,"
            " email, address, date_of_birth) VALUES (:id, :practice, :name, :phone, :at, :at,"
            " :gender, :email, :address, :dob)",
            {"id": ident, "practice": body["practiceId"], "name": body["name"],
             "phone": numbers[0]["number"] if numbers else None, "at": canonical(at),
             "gender": body["gender"], "email": body["email"],
             # create_patient.json: Principle answers a string when no address was sent. The
             # anonymiser hides which, so the fake sends an empty one; to confirm, create a
             # patient on staging without an address and read it back with getPatient.
             "address": body.get("address", ""), "dob": body["dateOfBirth"]})
        self.db.commit()
        created = self.patient(ident)
        assert created is not None
        return created

    def add_invoice(self, ident: str, patient_id: str, total: float, paid: float,
                    at: str) -> None:
        self.db.execute(
            "INSERT INTO invoices VALUES (:id, :practice, :patient, :reference, :status,"
            " :total, :paid, :at, :at)",
            {"id": ident, "practice": FAKE_PRACTICE_ID, "patient": patient_id,
             "reference": f"INV-{ident}", "status": "paid" if paid >= total else "issued",
             "total": total, "paid": paid, "at": canonical(at)})

    def add_payment(self, ident: str, invoice_id: str, amount: float, method: str | None,
                    at: str) -> None:
        """A complete payment on one invoice; `method` is the practice's transaction type."""
        patient = self.db.execute("SELECT patient_id FROM invoices WHERE id = ?",
                                  (invoice_id,)).fetchone()["patient_id"]
        self.db.execute(
            "INSERT INTO transactions VALUES (:id, :invoice, :practice, :patient, :amount,"
            " :method, 'complete', :at, :at)",
            {"id": ident, "invoice": invoice_id, "practice": FAKE_PRACTICE_ID,
             "patient": patient, "amount": amount, "method": method, "at": canonical(at)})

    # -- patients, invoices and payments -------------------------------------

    def patient(self, patient_id: str) -> dict[str, object] | None:
        row = self.db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return None if row is None else _render_patient(row)

    def search_patients(self, practice_id: str, name: str) -> list[dict[str, object]]:
        """Patients whose name contains `name`, ignoring case. Not paginated."""
        return [_render_patient(row) for row in self.db.execute(
            "SELECT * FROM patients WHERE practice_id = ? AND name LIKE ? ORDER BY id",
            (practice_id, f"%{name}%"))]

    def changed(self, table: str, practice_id: str, query: dict[str, str], limit: int,
                offset_id: str | None) -> Page:
        """Invoices or transactions in Principle's keyset order, by created or updated time."""
        where, params = ["practice_id = :practice_id"], {"practice_id": practice_id}
        for name, column, operator in (("createdFrom", "created_at", ">="),
                                       ("createdTo", "created_at", "<"),
                                       ("updatedFrom", "updated_at", ">="),
                                       ("updatedTo", "updated_at", "<")):
            if name in query:
                where.append(f"{column} {operator} :{name}")
                params[name] = canonical(query[name])
        cursor = _parse_cursor(offset_id)
        if cursor is not None:
            where.append("created_at < :cursor")
            params["cursor"] = cursor
        rows = self.db.execute(
            f"SELECT * FROM {table} WHERE " + " AND ".join(where)
            + " ORDER BY created_at DESC, id LIMIT :limit", params | {"limit": limit},
        ).fetchall()
        render = _render_invoice if table == "invoices" else _render_transaction
        full = len(rows) == limit
        return Page(rows=[render(row) for row in rows], total=len(rows),
                    next_offset_id=rows[-1]["created_at"] if full and rows else None)

    def add_appointment(
        self,
        ident: str,
        practice_id: str,
        practitioner_id: str,
        patient_id: str,
        event_from: str,
        event_to: str,
        status: str,
        at: str,
        category_id: str | None = None,
        step_name: str = "Step 1",
        online: bool = False,
        treatments: tuple[tuple[str, str | None], ...] = (("Periodic Exam", None),),
        tags: tuple[str, ...] = (),
    ) -> None:
        """An appointment on its own plan and step, as each booking in Principle has.

        Each treatment is (description, tooth): a tooth is "18" or "18 occlusal,mesial", and
        None is whole-mouth.
        """
        self.db.execute(
            "INSERT INTO appointments (id, practice_id, practitioner_id, patient_id,"
            " treatment_option_id, treatment_plan_id, treatment_step_id, status, event_from,"
            " event_to, created_at, updated_at, category_id, step_name, online)"
            " VALUES (:id, :practice_id, :practitioner_id, :patient_id, 'opt-exam',"
            " :plan, :step, :status, :event_from, :event_to, :at, :at, :category, :step_name,"
            " :online)",
            {
                "id": ident,
                "practice_id": practice_id,
                "practitioner_id": practitioner_id,
                "patient_id": patient_id,
                "plan": f"plan-{ident}",
                "step": f"step-{ident}",
                "status": status,
                "event_from": canonical(event_from),
                "event_to": canonical(event_to),
                "at": canonical(at),
                "category": category_id,
                "step_name": step_name,
                "online": int(online),
            },
        )
        for position, (description, tooth) in enumerate(treatments):
            number, _, surfaces = (tooth or "").partition(" ")
            self.db.execute(
                "INSERT INTO appointment_treatments (id, appointment_id, position,"
                " description, quadrant, quadrant_index, surfaces) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{ident}-treatment-{position}", ident, position, description,
                 int(number[0]) if number else None, int(number[1]) if number else None,
                 surfaces or None))
        self.db.executemany("INSERT INTO appointment_tags VALUES (?, ?, ?)",
                            [(ident, n, tag) for n, tag in enumerate(tags)])

    def add_category(self, ident: str, name: str, colour: tuple[str, str] | None) -> None:
        """A treatment category; `colour` is (palette name, hex), or None when none is set."""
        self.db.execute("INSERT INTO treatment_categories VALUES (?, ?, ?, ?, 0)",
                        (ident, name, *(colour or (None, None))))

    def add_roster_item(self, ident: str, staff_id: str, kind: str, title: str,
                        times: tuple[str, str], days: tuple[str, ...], start_date: str,
                        ending_date: str | None = None,
                        removed_on: tuple[str, ...] = ()) -> None:
        """A weekly roster block; `removed_on` are the UTC start instants deleted from it."""
        self.db.execute(
            "INSERT INTO roster_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (ident, staff_id, kind, title, *times, ",".join(days), start_date, ending_date))
        self.db.executemany("INSERT INTO roster_deletions VALUES (?, ?)",
                            [(ident, canonical(at)) for at in removed_on])

    def add_calendar_event(self, ident: str, staff_id: str, kind: str, title: str,
                           event_from: str, event_to: str, at: str,
                           schedule_ref: str | None = None,
                           step_name: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO calendar_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (ident, FAKE_PRACTICE_ID, staff_id, kind, title, canonical(event_from),
             canonical(event_to), schedule_ref, step_name, canonical(at)))


def _render_patient(row: sqlite3.Row) -> dict[str, object]:
    """A patient as Principle sends one.

    tests/recordings/create_patient.json (2026-10-10): `contactNumbers` and `tags` are always
    present, empty when unset, and there is no `practiceId`.
    """
    patient: dict[str, object] = {
        "id": row["id"], "name": row["name"], "gender": row["gender"],
        "email": row["email"], "address": row["address"],
        "contactNumbers": ([{"label": "mobile", "number": row["phone"]}]
                           if row["phone"] is not None else []),
        "tags": [],
        "createdAt": wire(row["created_at"]),
        "updatedAt": wire(row["updated_at"])}
    if row["date_of_birth"] is not None:
        patient["dateOfBirth"] = row["date_of_birth"]
    return patient


def _render_invoice(row: sqlite3.Row) -> dict[str, object]:
    """An invoice as production sends one: allocated to a practitioner, paid by allocations."""
    practitioner = {"id": "fake-practitioner-01", "name": "Dr Ada Whitwell"}
    paid = [{"transactionId": f"paid-{row['id']}", "allocations": [{
        "allocatedAmount": row["paid"], "allocatedProportion": 1,
        "target": {"type": "practitioner", "practitioner": practitioner}}]}]
    return {
        "id": row["id"], "reference": row["reference"], "status": row["status"],
        "patientId": row["patient_id"],
        "practice": {"id": row["practice_id"], "name": "Kowhai Street Dental (FAKE PRINCIPLE)"},
        "items": [], "subtotal": row["total"], "tax": 0, "total": row["total"],
        "allocations": [{"allocatedAmount": row["total"],
                         "target": {"type": "practitioner", "practitioner": practitioner}}],
        "transactionAllocations": paid if row["paid"] else [],
        "createdAt": row["created_at"], "issuedAt": row["created_at"],
        "updatedAt": row["updated_at"]}


def _render_transaction(row: sqlite3.Row) -> dict[str, object]:
    """A desk payment: `provider` is always manual, and the method is a transaction type."""
    payment: dict[str, object] = {
        "id": row["id"], "invoiceId": row["invoice_id"], "patientId": row["patient_id"],
        "practiceId": row["practice_id"], "provider": "manual", "reference": "1",
        "type": "payment", "status": row["status"], "amount": row["amount"],
        "description": "Payment", "createdAt": row["created_at"],
        "updatedAt": row["updated_at"]}
    if row["method"] is not None:
        payment["extendedData"] = {"transactionType": {
            "name": row["method"], "ref": {"id": f"type-{row['method']}"}}}
    return payment


def _iso(moment: datetime) -> str:
    """One spelling for one instant: UTC, with `Z`.

    Every timestamp in this store is written this way, because the columns are compared as
    text and `2026-09-28T09:00:00+13:00` sorts before `2026-09-27T21:00:00Z` while naming a
    later instant.
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def wire(stored: str) -> str:
    """A stored timestamp as the API sends it: Principle answers `+00:00`, never `Z`."""
    return stored.replace("Z", "+00:00")


def canonical(timestamp: str) -> str:
    """A caller's ISO timestamp, in this store's one spelling.

    The API documents `format: date-time`, so a request may carry any offset; comparing it
    against stored values without normalising answers with the wrong day.
    """
    return _iso(datetime.fromisoformat(timestamp))


def _parse_cursor(offset_id: str | None) -> str | None:
    """A paging cursor, or None where Principle would disregard one."""
    if not offset_id:
        return None
    try:
        return canonical(offset_id)
    except ValueError:
        return None


def seed(appointments_per_day: int = 8, days: int = 3) -> FakeStore:
    """A small practice with a diary, sized to force more than one page.

    Names are invented. Nothing here comes from a real patient record, and the practice name
    carries the fake marker so any report built from it says what it is.

    Each appointment gets a distinct `createdAt`, descending with its slot, because the paging
    cursor is a createdAt and rows sharing one would page unpredictably.
    """
    store = FakeStore()
    first_day = date(2026, 9, 28)
    created_base = datetime(2026, 3, 31, 20, 55, tzinfo=UTC)
    at = _iso(datetime.combine(first_day, time(0), tzinfo=PRACTICE_TZ))
    store.add_practice(FAKE_PRACTICE_ID, "Kowhai Street Dental (FAKE PRINCIPLE)", at)

    practitioners = [
        ("fake-practitioner-01", "Dr Ada Whitwell"),
        ("fake-practitioner-02", "Dr Bo Ferriter"),
    ]
    for ident, name in practitioners:
        store.add_practitioner(ident, FAKE_PRACTICE_ID, name, at)

    # Principle's own palette names and values (docs/principle/firestore.md). A category with
    # no colour is the case the day sheet must report rather than crash on.
    store.add_category("fake-category-hygiene", "Hygiene", ("Pink a100", "#ff80ab"))
    store.add_category("fake-category-recall", "Recall", ("Brown a100", "#d7ccc8"))
    store.add_category("fake-category-np", "New Patient Exam", ("Deep Purple a100", "#b388ff"))
    store.add_category("fake-category-uncoloured", "Consultation", None)
    categories = ["fake-category-hygiene", "fake-category-recall", "fake-category-np"]

    statuses = ["scheduled", "confirmed", "complete", "cancelled"]
    for day in range(days):
        start_of_day = datetime.combine(
            first_day + timedelta(days=day), time(9), tzinfo=PRACTICE_TZ
        )
        for slot in range(appointments_per_day):
            begins = start_of_day + timedelta(minutes=30 * slot)
            index = day * appointments_per_day + slot
            patient_id = f"fake-patient-{index:03d}"
            store.add_patient(patient_id, FAKE_PRACTICE_ID, f"Patient {index:03d}", at,
                              notes=("Prefers mornings", "Anxious, explain first")
                              if index == 1 else ())
            store.add_appointment(
                ident=f"fake-appointment-{index:03d}",
                practice_id=FAKE_PRACTICE_ID,
                practitioner_id=practitioners[slot % len(practitioners)][0],
                patient_id=patient_id,
                event_from=_iso(begins),
                event_to=_iso(begins + timedelta(minutes=30)),
                status=statuses[index % len(statuses)],
                at=_iso(created_base - timedelta(minutes=index)),
                # Statuses repeat every four, so the uncoloured category goes on one booked
                # appointment by name: on a cancelled one no task would read its colour.
                category_id=("fake-category-uncoloured" if index == 5
                             else categories[index % len(categories)]),
                step_name="Fillings upper right" if index == 1 else f"Planned - {first_day}",
                online=index == 0,
                treatments=(("Composite Filling - Direct Adhesive Restoration (1 surface)",
                             "18 occlusal,mesial"), ("Bitewing Radiograph", None))
                if index == 1 else (("Periodic Exam", None),),
                tags=("Shifted to Bo",) if index == 2 else (),
            )
    seed_roster(store, first_day)
    seed_payments(store)
    store.db.commit()
    return store


def seed_roster(store: FakeStore, day: date) -> None:
    """Lunch for each practitioner, one moved for `day`, and a pending online request.

    Moving Dr Bo's lunch is what editing one occurrence does in Principle: the day is deleted
    from the weekly block and a calendar event takes its place, pointing back through
    `scheduleRef` (docs/principle/firestore.md, "Calendar events").
    """
    def local(hour: int, minute: int = 0) -> str:
        return _iso(datetime.combine(day, time(hour, minute), tzinfo=PRACTICE_TZ))

    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday")
    store.add_roster_item("fake-roster-ada-lunch", "fake-practitioner-01", "break", "Lunch",
                          ("13:00", "14:00"), weekdays, "2026-01-05")
    store.add_roster_item("fake-roster-bo-lunch", "fake-practitioner-02", "break", "Lunch",
                          ("13:00", "14:00"), weekdays, "2026-01-05",
                          removed_on=(local(13),))
    store.add_calendar_event("fake-event-bo-lunch", "fake-practitioner-02", "break", "Lunch",
                             local(13, 30), local(14, 30), local(8),
                             schedule_ref="fake-roster-bo-lunch")
    store.add_calendar_event("fake-event-request", "fake-practitioner-01",
                             "appointmentRequest", "Appointment Request", local(14),
                             local(14, 40), local(8),
                             step_name="Problem with a tooth (single issue)")


def seed_payments(store: FakeStore) -> None:
    """Payments and unpaid invoices for the fake bank's deposits, dated relative to today.

    Matches tests/fake_akahu: the $185.00 transfer three days ago is already recorded as a
    Direct Deposit, and the $1,980.00 card settlement two days ago is the previous day's two
    Credit Card payments. One patient's phone number has spaces, which breaks Principle's
    own specification.
    """
    today = datetime.combine(date.today(), time(10), tzinfo=PRACTICE_TZ)

    def days_ago(days: int) -> str:
        return _iso(today - timedelta(days=days))

    for ident, name, phone in (("fake-lily", "Lily Fake", None),
                               ("fake-tom", "Tom Fake", None),
                               ("fake-spaced", "Spaced Fake", "021 123 4567"),
                               ("fake-card-1", "Card Fake-One", None),
                               ("fake-card-2", "Card Fake-Two", None)):
        store.add_patient(ident, FAKE_PRACTICE_ID, name, days_ago(30), phone)
    store.add_invoice("lily-1", "fake-lily", 185, 185, days_ago(10))
    store.add_payment("pay-lily", "lily-1", 185, "Direct Deposit", days_ago(3))
    store.add_invoice("tom-1", "fake-tom", 90, 0, days_ago(9))
    store.add_invoice("spaced-1", "fake-spaced", 60, 0, days_ago(8))
    for ident, patient, amount in (("card-1", "fake-card-1", 1200),
                                   ("card-2", "fake-card-2", 780)):
        store.add_invoice(ident, patient, amount, amount, days_ago(4))
        store.add_payment(f"pay-{ident}", ident, amount, "Credit Card", days_ago(3))
