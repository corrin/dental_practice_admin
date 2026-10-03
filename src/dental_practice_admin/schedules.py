"""APScheduler persists timing; the five-minute launcher executes due local tasks."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import portalocker
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field, model_validator

from dental_practice_admin import scripts, task_files
from dental_practice_admin.audit import Audit
from dental_practice_admin.config import Settings
from dental_practice_admin.storage import Coverage, Outcome, Storage

POLL_SECONDS = 300
TIMEZONE = "Pacific/Auckland"


class ScheduleStore(BaseScheduler):  # type: ignore[misc]
    """APScheduler's job API without a background execution loop.

    BackgroundScheduler can process due jobs while waking to shut down. Only the explicit
    launcher may claim work; viewing or editing schedules must never execute a task.
    """

    def wakeup(self) -> None:
        """The Windows launcher supplies wakeups through run_due."""

    def shutdown(self, wait: bool = True) -> None:
        """Close the library's job stores and executors."""
        super().shutdown(wait)


@contextmanager
def open_schedules(settings: Settings) -> Iterator[Any]:
    """Serialize APScheduler job-store access across web and launcher processes."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(settings.data_dir / "schedules.lock"), timeout=30):
        scheduler = ScheduleStore(timezone=TIMEZONE, jobstores={"default":
            SQLAlchemyJobStore(url="sqlite:///" + settings.database_path.as_posix())})
        scheduler.start(paused=True)
        try:
            yield scheduler
        finally:
            scheduler.shutdown()


class Schedule(BaseModel):
    """A pinned local task, explicit inputs, and one library-owned trigger."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    name: str = Field(pattern="^" + task_files.NAME + "$")
    revision: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    inputs: dict[str, Any] = Field(default_factory=dict)
    minutes: int = Field(default=0, ge=0)
    at: str = Field(default="16:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekdays: str = "mon-fri"
    paused: bool = False

    @model_validator(mode="after")
    def valid_trigger(self) -> Schedule:
        """Validate timing before changing a live schedule."""
        if self.minutes % 5:
            raise ValueError("Intervals must be multiples of five minutes")
        self.trigger()
        return self

    def trigger(self) -> Any:
        """Let APScheduler own intervals, weekdays and daylight-saving calculations."""
        if self.minutes:
            return IntervalTrigger(minutes=self.minutes, timezone=TIMEZONE)
        hour, minute = self.at.split(":")
        return CronTrigger(hour=int(hour), minute=int(minute), day_of_week=self.weekdays,
                           timezone=TIMEZONE)


def scheduled_task(name: str, revision: str, inputs: dict[str, Any]) -> None:
    """The serializable APScheduler callable; execution uses only installed files."""
    settings = Settings()
    asyncio.run(scripts.run(settings, task_files.load(settings, name, revision, inputs,
                                                     "scheduler"), name))


def save(settings: Settings, schedule: Schedule, owner: str) -> None:
    """Validate the installed version and audit a staff schedule edit before saving it."""
    task_files.load(settings, schedule.name, schedule.revision, schedule.inputs, owner)
    with open_schedules(settings) as scheduler:
        Audit(settings, "schedules").write("schedule_edit", initiator=owner,
                                           schedule=schedule.model_dump())
        job = scheduler.add_job(scheduled_task, schedule.trigger(), id=schedule.id,
            name=schedule.name, args=[schedule.name, schedule.revision, schedule.inputs],
            replace_existing=True, misfire_grace_time=POLL_SECONDS, coalesce=False)
        if schedule.paused:
            job.pause()


async def run_due(settings: Settings, at: datetime | None = None) -> int:
    """Claim each occurrence before execution; interrupted claims are never replayed."""
    observed_at = at or datetime.now(UTC)
    store = Storage(settings.database_path)
    failed = 0
    pending = []
    launcher = portalocker.Lock(str(settings.data_dir / "launcher.lock"), timeout=0)
    try:
        launcher.acquire()
        with open_schedules(settings) as scheduler:
            interrupted = store.db.execute(
                "SELECT run_id FROM task_runs WHERE initiator='scheduler' AND outcome='running'")
            for row in interrupted.fetchall():
                Audit(settings, row[0]).write("interrupted", reason="launcher_disappeared")
                store.finish_run(row[0], Outcome.UNCERTAIN, Coverage.PARTIAL,
                                 "Launcher interrupted; inspect saved state before retrying")
            for job in scheduler.get_jobs():
                due = job.next_run_time
                while due is not None and due <= observed_at:
                    identifier = uuid.uuid5(uuid.NAMESPACE_URL, job.id + due.isoformat()).hex
                    previous = store.run(identifier)
                    if previous is None:
                        store.start_run(job.name, "scheduler", settings.environment.value,
                                        identifier)
                    late = (observed_at - due).total_seconds() > POLL_SECONDS
                    next_due = (job.trigger.get_next_fire_time(
                        None, observed_at - timedelta(seconds=POLL_SECONDS)) if late else
                        job.trigger.get_next_fire_time(due, due + timedelta(microseconds=1)))
                    job.modify(next_run_time=next_due)
                    if previous is not None:
                        failed += int(previous.outcome is not Outcome.SUCCEEDED)
                    elif late:
                        Audit(settings, identifier).write("missed", schedule=job.id,
                                                          due=due, until=next_due)
                        store.finish_run(identifier, Outcome.MISSED, Coverage.PARTIAL,
                                         f"Missed scheduled work from {due.isoformat()}")
                        failed += 1
                    else:
                        pending.append((identifier, due, job))
                    due = next_due
        for identifier, due, job in pending:
            try:
                if ((at or datetime.now(UTC)) - due).total_seconds() > POLL_SECONDS:
                    Audit(settings, identifier).write("missed", schedule=job.id, due=due)
                    store.finish_run(identifier, Outcome.MISSED, Coverage.PARTIAL,
                                     "Execution window passed while another task was running")
                    failed += 1
                    continue
                script = task_files.load(settings, job.args[0], job.args[1],
                                         job.args[2], "scheduler")
                await scripts.recorded(settings, script, job.name,
                    partial(scripts.worker_run, settings, script), identifier)
                result = store.run(identifier)
                failed += int(result is None or not result.is_trustworthy)
            except Exception:
                Audit(settings, identifier).write("failed", schedule=job.id)
                store.finish_run(identifier, Outcome.UNCERTAIN, Coverage.PARTIAL,
                                 "Scheduled execution incomplete; inspect its audit")
                failed += 1
        Audit(settings, "launcher").write("checked", failures=failed)
    finally:
        launcher.release()
        store.close()
    return int(failed > 0)


def main() -> int:
    """Check due tasks once for the Windows launcher."""
    settings = Settings()
    settings.require_credentials()
    return asyncio.run(run_due(settings))


if __name__ == "__main__":
    raise SystemExit(main())
