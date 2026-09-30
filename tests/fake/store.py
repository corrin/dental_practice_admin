"""The fake Principle's own data state.

A relational store, not a bag of canned responses: one table per resource the application
touches, with a real column for every field the API filters, orders or keys on, so a
listing answers from a query the way Principle's would. Every answer is computed from this
state -- a stored response stops being true the moment the state changes.

In-memory by default, one store per test. `seed()` builds a small practice whose name
carries "(FAKE PRINCIPLE)" so a fake run can never be read as a real one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
CREATE INDEX appointments_by_window ON appointments(practice_id, event_from, id);
CREATE INDEX appointments_by_practitioner ON appointments(practitioner_id, event_from);
CREATE INDEX appointments_by_status ON appointments(status);
"""

# The key the fake accepts. A request carrying anything else is unauthenticated, which is
# how a test asserts the client actually sends the header.
FAKE_API_KEY = "fake-principle-key"

FAKE_PRACTICE_ID = "fake-practice-0001"


@dataclass(frozen=True)
class Page:
    """One page of a listing, and the cursor that follows it."""

    rows: list[dict[str, object]]
    total: int
    next_offset_id: str | None


class FakeStore:
    """The organisation's state, queried the way the API lets a caller query it."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- reads ---------------------------------------------------------------

    def practices(self) -> list[dict[str, object]]:
        """Every practice; `/v1/practices` returns `data` alone and does not page."""
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in self.db.execute("SELECT * FROM practices ORDER BY id")
        ]

    def practitioners(self, practice_id: str, limit: int, offset_id: str | None) -> Page:
        return self._page(
            table="practitioners",
            order="id",
            where="practice_id = :practice_id",
            params={"practice_id": practice_id},
            limit=limit,
            offset_id=offset_id,
            render=lambda row: {
                "id": row["id"],
                "name": row["name"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "treatmentOptions": [],
            },
        )

    def appointments(
        self,
        practice_id: str,
        window_from: str,
        window_to: str,
        limit: int,
        offset_id: str | None,
        practitioner_id: str | None = None,
        status: str | None = None,
    ) -> Page:
        """Appointments whose event window intersects [from, to).

        The half-open window and the "from OR to intersects" rule are the spec's
        (`listAppointmentsByDateRange`): an appointment starting before the window but
        running into it matches.
        """
        where = [
            "practice_id = :practice_id",
            "event_from < :window_to",
            "event_to > :window_from",
        ]
        params: dict[str, object] = {
            "practice_id": practice_id,
            "window_from": window_from,
            "window_to": window_to,
        }
        if practitioner_id is not None:
            where.append("practitioner_id = :practitioner_id")
            params["practitioner_id"] = practitioner_id
        if status is not None:
            where.append("status = :status")
            params["status"] = status
        return self._page(
            table="appointments",
            order="event_from, id",
            where=" AND ".join(where),
            params=params,
            limit=limit,
            offset_id=offset_id,
            render=_render_appointment,
        )

    # -- paging --------------------------------------------------------------

    def _page(
        self,
        table: str,
        order: str,
        where: str,
        params: dict[str, object],
        limit: int,
        offset_id: str | None,
        render: object,
    ) -> Page:
        """Keyset paging: `offsetId` names the last row of the previous page.

        The cursor is the ordering tuple of that row, so the next page resumes strictly
        after it and the walk always terminates. `nextOffsetId` is None on the last page,
        never a repeat of the current cursor.
        """
        keys = [key.strip() for key in order.split(",")]
        total = int(
            self.db.execute(
                f"SELECT count(*) FROM {table} WHERE {where}",
                params,
            ).fetchone()[0]
        )
        clause = where
        if offset_id is not None:
            cursor = self.db.execute(
                f"SELECT {order} FROM {table} WHERE id = :offset_id",
                {"offset_id": offset_id},
            ).fetchone()
            if cursor is None:
                raise UnknownOffsetError(offset_id)
            tuple_of = ", ".join(keys)
            placeholders = ", ".join(f":cursor_{index}" for index, _ in enumerate(keys))
            clause = f"{where} AND ({tuple_of}) > ({placeholders})"
            params = dict(params) | {
                f"cursor_{index}": cursor[key] for index, key in enumerate(keys)
            }
        rows = self.db.execute(
            f"SELECT * FROM {table} WHERE {clause} ORDER BY {order} LIMIT :limit",
            dict(params) | {"limit": limit + 1},
        ).fetchall()
        has_more = len(rows) > limit
        kept = rows[:limit]
        return Page(
            rows=[render(row) for row in kept],  # type: ignore[operator]
            total=total,
            next_offset_id=kept[-1]["id"] if has_more and kept else None,
        )

    # -- writes (seeding is a write too) -------------------------------------

    def add_practice(self, practice_id: str, name: str, at: str) -> None:
        self.db.execute(
            "INSERT INTO practices VALUES (:id, :name, :at, :at)",
            {"id": practice_id, "name": name, "at": at},
        )

    def add_practitioner(self, ident: str, practice_id: str, name: str, at: str) -> None:
        self.db.execute(
            "INSERT INTO practitioners VALUES (:id, :practice_id, :name, :at, :at)",
            {"id": ident, "practice_id": practice_id, "name": name, "at": at},
        )

    def add_patient(self, ident: str, practice_id: str, name: str, at: str) -> None:
        self.db.execute(
            "INSERT INTO patients VALUES (:id, :practice_id, :name, :at, :at)",
            {"id": ident, "practice_id": practice_id, "name": name, "at": at},
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
                "event_from": event_from,
                "event_to": event_to,
                "at": at,
            },
        )


class UnknownOffsetError(Exception):
    """A cursor naming a row this store does not hold."""

    def __init__(self, offset_id: str) -> None:
        super().__init__(f"offsetId {offset_id!r} names no record")
        self.offset_id = offset_id


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
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def seed(appointments_per_day: int = 8, days: int = 3) -> FakeStore:
    """A small practice with a diary, sized to force more than one page.

    Names are invented. Nothing here comes from a real patient record, and the practice
    name carries the fake marker so any report built from it says what it is.
    """
    store = FakeStore()
    epoch = datetime(2026, 9, 28, tzinfo=UTC)
    at = _iso(epoch)
    store.add_practice(FAKE_PRACTICE_ID, "Kowhai Street Dental (FAKE PRINCIPLE)", at)

    practitioners = [
        ("fake-practitioner-01", "Dr Ada Whitwell"),
        ("fake-practitioner-02", "Dr Bo Ferriter"),
    ]
    for ident, name in practitioners:
        store.add_practitioner(ident, FAKE_PRACTICE_ID, name, at)

    statuses = ["scheduled", "confirmed", "complete", "cancelled"]
    for day in range(days):
        start_of_day = epoch + timedelta(days=day, hours=9)
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
                at=at,
            )
    store.db.commit()
    return store
