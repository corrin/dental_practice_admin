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
        scheduler = ScheduleStore(jobstores={"default":
            SQLAlchemyJobStore(url="sqlite:///" + settings.database_path.as_posix())})
        scheduler.start(paused=True)
        try:
            yield scheduler
        finally:
            scheduler.shutdown()


# deploy/task-runner.xml stops a launcher after this long, so a run older is not in progress.
RUN_LIMIT = timedelta(minutes=30)


def launcher_checked(settings: Settings) -> datetime | None:
    """When the five-minute launcher last finished checking for due work, if it ever has."""
    try:
        modified = (settings.data_dir / "audits" / "launcher.jsonl").stat().st_mtime
    except FileNotFoundError:
        return None
    return datetime.fromtimestamp(modified, UTC)


def tasks_synced(settings: Settings) -> datetime | None:
    """When the launcher last brought installed tasks up to date with the practice repository."""
    try:
        modified = (settings.data_dir / "audits" / "task-sync.jsonl").stat().st_mtime
    except FileNotFoundError:
        return None
    return datetime.fromtimestamp(modified, UTC)


def launcher_recent(checked: datetime | None) -> bool:
    """False once the launcher has missed two checks."""
    return checked is not None and (
        datetime.now(UTC) - checked).total_seconds() <= 2 * POLL_SECONDS


def attention(settings: Settings, store: Storage) -> list[dict[str, str]]:
    """What staff must act on for automatic runs, each with the page that explains it.

    Read from what the launcher has recorded, never the job store, so a page never waits on
    the schedules lock. For each task the launcher has run, its latest run by anyone counts,
    so a person's re-run can clear it; it may list records for staff (`detail["for_staff"]`).
    The launcher records its check only after the work it found, so a run still within the
    launcher's time limit, or the next poll after it, means it is busy, not stopped.
    """
    automatic = store.latest_runs_by_task(initiator="scheduler")
    busy = any(run.outcome is Outcome.RUNNING and datetime.now(UTC)
               - datetime.fromisoformat(run.started_at) < RUN_LIMIT + timedelta(
                   seconds=POLL_SECONDS) for run in automatic)
    notes = []
    checked = launcher_checked(settings)
    if automatic and not busy and not launcher_recent(checked):
        notes.append({"text": "Automatic runs have stopped", "href": "/tasks/manage"})
    if launcher_recent(checked) and not launcher_recent(tasks_synced(settings)):
        notes.append({"text": "New and changed reports can't be fetched; ask your administrator",
                      "href": "/tasks/manage"})
    scheduled = {run.task for run in automatic}
    for run in store.latest_runs_by_task():
        if run.task not in scheduled:
            continue
        title = run.task.replace("_", " ").capitalize()
        if run.outcome is not Outcome.RUNNING and not run.is_trustworthy:
            notes.append({"text": f"{title}: the last automatic run did not complete",
                          "href": f"/runs/{run.run_id}"})
        if run.detail and run.detail.get("for_staff"):
            notes.append({"text": f"{title}: {len(run.detail['for_staff'])} for staff to fix",
                          "href": f"/runs/{run.run_id}"})
    return notes


class Schedule(BaseModel):
    """An installed task, explicit inputs, and one library-owned trigger."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    name: str = Field(pattern="^" + task_files.NAME + "$")
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
        """APScheduler owns intervals, weekdays and daylight saving, in the server's zone."""
        if self.minutes:
            return IntervalTrigger(minutes=self.minutes)
        hour, minute = self.at.split(":")
        return CronTrigger(hour=int(hour), minute=int(minute), day_of_week=self.weekdays)


def scheduled_task(name: str, inputs: dict[str, Any]) -> None:
    """The serializable APScheduler callable; execution uses only installed files."""
    settings = Settings()
    asyncio.run(scripts.run(settings, task_files.load(
        settings, name, task_files.current(settings, name), inputs, "scheduler"), name))


def save(settings: Settings, schedule: Schedule, owner: str) -> None:
    """Validate the installed version and audit a staff schedule edit before saving it."""
    task_files.load(settings, schedule.name, task_files.current(settings, schedule.name),
                    schedule.inputs, owner)
    with open_schedules(settings) as scheduler:
        Audit(settings, "schedules").write("schedule_edit", initiator=owner,
                                           schedule=schedule.model_dump())
        job = scheduler.add_job(scheduled_task, schedule.trigger(), id=schedule.id,
            name=schedule.name, args=[schedule.name, schedule.inputs],
            replace_existing=True, misfire_grace_time=POLL_SECONDS, coalesce=False)
        if schedule.paused:
            job.pause()


def task_lock(settings: Settings, name: str) -> portalocker.Lock:
    """Held while a scheduled task runs, so it never runs twice at once (ADR 0009)."""
    folder = settings.data_dir / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    return portalocker.Lock(str(folder / f"task-{name}.lock"), timeout=0)


async def run_due(settings: Settings, at: datetime | None = None,
                  claim_wait: float = 30) -> int:
    """Claim each occurrence before execution; interrupted claims are never replayed.

    The launcher lock covers only the claiming. Claimed tasks run side by side, each under its
    own task lock, so a long run holds up neither another task nor the next launch (ADR 0009).
    Occurrences of one task claimed together run one after the other under its lock.
    """
    observed_at = at or datetime.now(UTC)
    store = Storage(settings.database_path)
    failed = 0
    pending: dict[str, tuple[portalocker.Lock, list[tuple[str, Any, datetime]]]] = {}
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    claiming = portalocker.Lock(str(settings.data_dir / "launcher.lock"), timeout=claim_wait)
    try:
        try:
            claiming.acquire()
        except portalocker.AlreadyLocked:
            Audit(settings, "launcher-failed").write("failed", reason="claiming_locked",
                                                     waited_seconds=claim_wait)
            return 1
        with claiming, open_schedules(settings) as scheduler:
            running = store.db.execute(
                "SELECT run_id, task FROM task_runs"
                " WHERE initiator='scheduler' AND outcome='running'")
            for run_id, name in running.fetchall():
                try:
                    with task_lock(settings, name):
                        pass
                except portalocker.AlreadyLocked:
                    continue  # Still running, under another launch.
                Audit(settings, run_id).write("interrupted", reason="launcher_disappeared")
                store.finish_run(run_id, Outcome.UNCERTAIN, Coverage.PARTIAL,
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
                    elif job.name in pending:
                        pending[job.name][1].append((identifier, job, due))
                    else:
                        # Taken now, before the launcher lock is let go, so no launch can see
                        # this claimed run with its task lock free and call it interrupted.
                        lock = task_lock(settings, job.name)
                        try:
                            lock.acquire()
                        except portalocker.AlreadyLocked:
                            Audit(settings, identifier).write("missed", schedule=job.id,
                                                              reason="still_running")
                            store.finish_run(identifier, Outcome.MISSED, Coverage.PARTIAL,
                                             "Not started: the previous run of this task is"
                                             " still going")
                            failed += 1
                        else:
                            pending[job.name] = (lock, [(identifier, job, due)])
                    due = next_due
        runs = [run_claimed(settings, store, claimed, lock)
                for lock, claimed in pending.values()]
        pending = {}  # Each task's runs release its lock from here on.
        outcomes = await asyncio.gather(*runs)
        failed += sum(outcomes)
        Audit(settings, "launcher").write("checked", failures=failed)
    finally:
        for lock, _claimed in pending.values():
            lock.release()
        store.close()
    return int(failed > 0)


async def run_claimed(settings: Settings, store: Storage,
                      claimed: list[tuple[str, Any, datetime]], lock: portalocker.Lock) -> int:
    """Run one task's claimed occurrences in turn, then release its lock; how many failed."""
    try:
        return sum([await _run_one(settings, store, identifier, job, due)
                    for identifier, job, due in claimed])
    finally:
        lock.release()


async def _run_one(settings: Settings, store: Storage, identifier: str, job: Any,
                   due: datetime) -> int:
    """Run one claimed occurrence, for the day it was due; 1 if it did not succeed."""
    try:
        name, inputs = job.args
        script = task_files.load(settings, name, task_files.current(settings, name),
                                 inputs, "scheduler", due.astimezone().date())
        await scripts.recorded(settings, script, job.name,
            partial(scripts.worker_run, settings, script), identifier)
        result = store.run(identifier)
    except Exception:
        Audit(settings, identifier).write("failed", schedule=job.id)
        store.finish_run(identifier, Outcome.UNCERTAIN, Coverage.PARTIAL,
                         "Scheduled execution incomplete; inspect its audit")
        return 1
    return int(result is None or not result.is_trustworthy)


def main() -> int:
    """Bring installed tasks up to date, then check due tasks once, for the Windows launcher.

    Tasks already installed keep running when GitHub can't be reached; staff pages say so.
    """
    settings = Settings()
    settings.require_data_root()
    settings.require_credentials()
    try:
        task_files.sync(settings)
        Audit(settings, "task-sync").write("synced")
    except Exception as error:
        Audit(settings, "task-sync-failed").write("failed", error=repr(error))
    return asyncio.run(run_due(settings))


if __name__ == "__main__":
    raise SystemExit(main())
