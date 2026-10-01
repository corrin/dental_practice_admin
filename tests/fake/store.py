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
    id          TEXT PRIMARY KEY,
    practice_id TEXT NOT NULL REFERENCES practices(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
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
    updated_at          TEXT NOT NULL
);
CREATE INDEX appointments_by_window ON appointments(practice_id, event_from, event_to);
CREATE INDEX appointments_by_cursor ON appointments(created_at DESC, id);
CREATE INDEX appointments_by_practitioner ON appointments(practitioner_id, event_from);
CREATE INDEX appointments_by_status ON appointments(status);
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
            rows=[_render_appointment(row) for row in rows],
            total=len(rows),
            next_offset_id=rows[-1]["created_at"] if full and rows else None,
        )

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

    def add_patient(self, ident: str, practice_id: str, name: str, at: str) -> None:
        self.db.execute(
            "INSERT INTO patients VALUES (:id, :practice_id, :name, :at, :at)",
            {"id": ident, "practice_id": practice_id, "name": name, "at": canonical(at)},
        )

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
    ) -> None:
        self.db.execute(
            "INSERT INTO appointments VALUES (:id, :practice_id, :practitioner_id,"
            " :patient_id, 'opt-exam', 'plan-1', 'step-1', :status, :event_from,"
            " :event_to, :at, :at)",
            {
                "id": ident,
                "practice_id": practice_id,
                "practitioner_id": practitioner_id,
                "patient_id": patient_id,
                "status": status,
                "event_from": canonical(event_from),
                "event_to": canonical(event_to),
                "at": canonical(at),
            },
        )


def _render_appointment(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "practiceId": row["practice_id"],
        "practitionerId": row["practitioner_id"],
        "patientId": row["patient_id"],
        "treatmentOptionId": row["treatment_option_id"],
        "treatmentPlanId": row["treatment_plan_id"],
        "treatmentStepId": row["treatment_step_id"],
        "status": row["status"],
        "event": {"from": row["event_from"], "to": row["event_to"]},
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _iso(moment: datetime) -> str:
    """One spelling for one instant: UTC, with `Z`.

    Every timestamp in this store is written this way, because the columns are compared as
    text and `2026-09-28T09:00:00+13:00` sorts before `2026-09-27T21:00:00Z` while naming a
    later instant.
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


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

    statuses = ["scheduled", "confirmed", "complete", "cancelled"]
    for day in range(days):
        start_of_day = datetime.combine(
            first_day + timedelta(days=day), time(9), tzinfo=PRACTICE_TZ
        )
        for slot in range(appointments_per_day):
            begins = start_of_day + timedelta(minutes=30 * slot)
            index = day * appointments_per_day + slot
            patient_id = f"fake-patient-{index:03d}"
            store.add_patient(patient_id, FAKE_PRACTICE_ID, f"Patient {index:03d}", at)
            store.add_appointment(
                ident=f"fake-appointment-{index:03d}",
                practice_id=FAKE_PRACTICE_ID,
                practitioner_id=practitioners[slot % len(practitioners)][0],
                patient_id=patient_id,
                event_from=_iso(begins),
                event_to=_iso(begins + timedelta(minutes=30)),
                status=statuses[index % len(statuses)],
                at=_iso(created_base - timedelta(minutes=index)),
            )
    store.db.commit()
    return store
