"""Business operations, and the command line Task Scheduler invokes.

An operation is an ordinary async function. Chat tools and scheduled commands call the same
function, so what the tests prove about it holds for both.

The practice is in New Zealand, so a "day" is a Pacific/Auckland day. Asking Principle for
a UTC day would return the wrong appointments for most of the year and would silently shift
by an hour at each daylight-saving boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from principle_admin.config import Environment, Settings
from principle_admin.principle import PrincipleClient
from principle_admin.storage import Coverage, Outcome, Storage

PRACTICE_TZ = ZoneInfo("Pacific/Auckland")

# A cancelled slot is not a patient who is coming in; the diary count staff act on excludes
# it, and the status breakdown keeps it visible.
STATUSES_NOT_ATTENDING = frozenset({"cancelled", "unscheduled"})


@dataclass
class PractitionerDay:
    """One practitioner's list for the day."""

    practitioner_id: str
    name: str
    appointments: int
    attending: int
    first_from: str | None
    last_to: str | None


@dataclass
class DiaryReport:
    """What staff read: the day's shape, and whether it is the whole day."""

    on_date: date
    practice_id: str
    principle: str
    appointments: int
    attending: int
    by_status: dict[str, int] = field(default_factory=dict)
    by_practitioner: list[PractitionerDay] = field(default_factory=list)
    coverage: Coverage = Coverage.COMPLETE
    coverage_note: str | None = None

    def summary(self) -> str:
        """One line for the run list. Never claims completeness it does not have."""
        head = (
            f"{self.on_date.isoformat()}: {self.attending} attending "
            f"of {self.appointments} booked across {len(self.by_practitioner)} practitioners"
        )
        if self.coverage is Coverage.PARTIAL:
            return f"{head} (PARTIAL: {self.coverage_note})"
        return head

    def as_detail(self) -> dict[str, object]:
        return {
            "onDate": self.on_date.isoformat(),
            "practiceId": self.practice_id,
            "principle": self.principle,
            "appointments": self.appointments,
            "attending": self.attending,
            "byStatus": self.by_status,
            "byPractitioner": [vars(day) for day in self.by_practitioner],
            "coverage": self.coverage.value,
            "coverageNote": self.coverage_note,
        }


def _event_window(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """An appointment's scheduled window, or None when the record carries none.

    `event` is optional in the schema: an unscheduled appointment has no window, and reading
    one as a string would put "None" in the report's first and last columns.
    """
    event = row.get("event")
    if not isinstance(event, Mapping):
        return None
    starts, ends = event.get("from"), event.get("to")
    if not isinstance(starts, str) or not isinstance(ends, str):
        return None
    return starts, ends


def local_day_window(on_date: date) -> dict[str, str]:
    """The half-open [from, to) window covering one Pacific/Auckland day."""
    start = datetime.combine(on_date, time.min, tzinfo=PRACTICE_TZ)
    end = datetime.combine(on_date + timedelta(days=1), time.min, tzinfo=PRACTICE_TZ)
    return {"from": _wire(start), "to": _wire(end)}


def _wire(moment: datetime) -> str:
    """UTC with `Z`. The window is computed locally and sent unambiguously."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def daily_diary(
    client: PrincipleClient, on_date: date, practice_id: str | None = None
) -> DiaryReport:
    """The day's appointments, grouped by practitioner.

    Practice scope comes from configuration, not from the caller's argument, unless one is
    passed explicitly by trusted code. A model-supplied practice id must never widen what a
    chat tool can read.
    """
    practice = practice_id or client.settings.practice_id
    names: dict[str, str] = {
        str(row["id"]): str(row.get("name") or row["id"])
        async for row in client.rows(
            "list_practitioners", path_params={"practice_id": practice}
        )
    }

    appointments = [
        row
        async for row in client.rows(
            "list_appointments",
            query={"practiceId": practice, **local_day_window(on_date)},
        )
    ]

    report = DiaryReport(
        on_date=on_date,
        practice_id=practice,
        principle=client.settings.environment.value,
        appointments=len(appointments),
        attending=sum(
            1 for row in appointments if row.get("status") not in STATUSES_NOT_ATTENDING
        ),
        by_status=dict(sorted(Counter(str(row.get("status")) for row in appointments).items())),
    )

    unnamed: set[str] = set()
    per_practitioner: dict[str, list[dict[str, object]]] = {}
    for row in appointments:
        practitioner_id = str(row.get("practitionerId"))
        if practitioner_id not in names:
            unnamed.add(practitioner_id)
        per_practitioner.setdefault(practitioner_id, []).append(row)

    for practitioner_id, rows in sorted(per_practitioner.items()):
        windows = sorted(filter(None, (_event_window(row) for row in rows)))
        report.by_practitioner.append(
            PractitionerDay(
                practitioner_id=practitioner_id,
                name=names.get(practitioner_id, f"unknown practitioner {practitioner_id}"),
                appointments=len(rows),
                attending=sum(
                    1 for row in rows if row.get("status") not in STATUSES_NOT_ATTENDING
                ),
                first_from=windows[0][0] if windows else None,
                last_to=max(window[1] for window in windows) if windows else None,
            )
        )

    if unnamed:
        # The diary is complete but the labelling is not, and a report naming a practitioner
        # "unknown" must not be filed as though every column were identified.
        report.coverage = Coverage.PARTIAL
        report.coverage_note = (
            f"{len(unnamed)} practitioner id(s) absent from the practice's practitioner list"
        )
    return report


async def run_daily_diary(settings: Settings, on_date: date, initiator: str) -> str:
    """Execute the diary task and record the run. Returns the run id."""
    storage = Storage(settings.database_path)
    run_id = storage.start_run(
        task="daily_diary", initiator=initiator, principle=settings.environment.value
    )
    try:
        async with PrincipleClient(settings) as client:
            report = await daily_diary(client, on_date)
    except Exception as failure:
        storage.finish_run(
            run_id,
            outcome=Outcome.FAILED,
            coverage=Coverage.PARTIAL,
            summary=f"{type(failure).__name__}: {failure}",
        )
        storage.close()
        raise
    storage.finish_run(
        run_id,
        outcome=Outcome.SUCCEEDED,
        coverage=report.coverage,
        summary=report.summary(),
        detail=report.as_detail(),
    )
    storage.close()
    return run_id


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="principle-admin")
    tasks = parser.add_subparsers(dest="task", required=True)
    diary = tasks.add_parser("diary", help="report one day's appointments")
    diary.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Pacific/Auckland date (default: tomorrow)",
    )
    diary.add_argument(
        "--initiator",
        default=None,
        help="who asked for this run (default: the Windows account running it)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point Task Scheduler calls with an absolute interpreter path."""
    arguments = _parse(argv)
    settings = Settings()
    if settings.environment is not Environment.FAKE:
        settings.require_credentials()
    on_date = arguments.date or (datetime.now(tz=PRACTICE_TZ).date() + timedelta(days=1))
    initiator = arguments.initiator or f"windows:{getpass.getuser()}"
    run_id = asyncio.run(run_daily_diary(settings, on_date, initiator))
    storage = Storage(settings.database_path)
    run = storage.run(run_id)
    storage.close()
    assert run is not None
    print(f"{run.outcome.value} {run.coverage.value} {run_id}\n{run.summary}")
    return 0 if run.is_trustworthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
