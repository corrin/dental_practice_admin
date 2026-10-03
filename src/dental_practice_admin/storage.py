"""Run history, on the host's local disk outside the checkout.

One SQLite file, shared by the web process and the scheduled tasks. Transactions are short
and never span a Principle call: a task fetches first, then writes what it found, because a
transaction held open across the network blocks the web process behind a stranger's timeout.

`coverage` is the field that stops a partial answer being filed as a complete one. It is
stored beside the summary rather than inferred from it, so a report that could not see the
whole diary cannot render as though it did.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic.dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_runs (
    run_id       TEXT PRIMARY KEY,
    task         TEXT NOT NULL,
    initiator    TEXT NOT NULL,
    principle    TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    outcome      TEXT NOT NULL,
    coverage     TEXT NOT NULL,
    summary      TEXT NOT NULL,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS task_runs_recent ON task_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS sign_ins (
    at          TEXT NOT NULL,
    staff_email TEXT NOT NULL,
    remote_ip   TEXT
);
CREATE INDEX IF NOT EXISTS sign_ins_recent ON sign_ins(at DESC);

CREATE TABLE IF NOT EXISTS interface_warnings (
    operation TEXT PRIMARY KEY,
    interface_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""


class Outcome(StrEnum):
    """How a run ended.

    `UNCERTAIN` exists for writes whose result is unknown -- the response was lost and the
    action may or may not have happened. It is never resolved by repeating the write.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    MISSED = "missed"


class Coverage(StrEnum):
    """Whether the run saw everything it set out to see."""

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class TaskRun:
    """One recorded execution, as the results pages display it."""

    run_id: str
    task: str
    initiator: str
    principle: str
    started_at: str
    finished_at: str | None
    outcome: Outcome
    coverage: Coverage
    summary: str
    detail: dict[str, Any] | None

    @property
    def is_trustworthy(self) -> bool:
        """False when this run must not be read as a full picture."""
        return self.outcome is Outcome.SUCCEEDED and self.coverage is Coverage.COMPLETE


def now() -> str:
    """The current instant, UTC, as every timestamp column stores it."""
    return datetime.now(tz=UTC).isoformat()


class Storage:
    """The local database, opened per process."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, timeout=15.0)
        self.db.row_factory = sqlite3.Row
        # WAL lets the web process read while a scheduled task writes; without it the two
        # contend and one of them waits out the busy timeout for no reason.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def interface_warning(self, operation: str, interface_id: str, reason: str) -> None:
        """Deduplicate production incompatibilities without storing business data."""
        with self._write() as db:
            db.execute(
                "INSERT INTO interface_warnings VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(operation) DO UPDATE SET interface_id=excluded.interface_id, "
                "reason=excluded.reason, last_seen=excluded.last_seen",
                (operation, interface_id, reason, now(), now()),
            )

    def resolve_interface_warning(self, operation: str, interface_id: str) -> None:
        """A compatible response from a different released contract resolves the warning."""
        with self._write() as db:
            db.execute("DELETE FROM interface_warnings WHERE operation=? AND interface_id<>?",
                       (operation, interface_id))

    def interface_warnings(self) -> list[dict[str, str]]:
        """Outstanding observations, retained across application restarts."""
        return [dict(row) for row in self.db.execute(
            "SELECT operation, reason, first_seen, last_seen FROM interface_warnings "
            "ORDER BY first_seen"
        )]

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One short transaction. Nothing inside may touch the network."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def start_run(self, task: str, initiator: str, principle: str,
                  run_id: str | None = None) -> str:
        """Record that a run began, returning its identifier."""
        run_id = run_id or uuid.uuid4().hex
        with self._write() as db:
            db.execute(
                "INSERT INTO task_runs (run_id, task, initiator, principle, started_at,"
                " outcome, coverage, summary) VALUES (?, ?, ?, ?, ?, ?, ?, '')",
                (
                    run_id,
                    task,
                    initiator,
                    principle,
                    now(),
                    Outcome.RUNNING.value,
                    Coverage.PARTIAL.value,
                ),
            )
        return run_id

    def finish_run(
        self,
        run_id: str,
        outcome: Outcome,
        coverage: Coverage,
        summary: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Close a run out with its result.

        A run left `RUNNING` is a crashed run, which is why this is a separate call: the
        absence of a finish is the signal, and overwriting `started_at` would hide it.
        """
        with self._write() as db:
            db.execute(
                "UPDATE task_runs SET finished_at = ?, outcome = ?, coverage = ?,"
                " summary = ?, detail = ? WHERE run_id = ?",
                (
                    now(),
                    outcome.value,
                    coverage.value,
                    summary,
                    json.dumps(detail) if detail is not None else None,
                    run_id,
                ),
            )

    def record_sign_in(self, staff_email: str, remote_ip: str | None) -> None:
        """Note that someone signed in.

        Separate from Caddy's access log because it names the person rather than an address, which
        is what "was anything accessed, and by whom" actually needs.
        """
        with self._write() as db:
            db.execute(
                "INSERT INTO sign_ins (at, staff_email, remote_ip) VALUES (?, ?, ?)",
                (now(), staff_email, remote_ip),
            )

    def recent_sign_ins(self, limit: int = 50) -> list[tuple[str, str, str | None]]:
        return [
            (row["at"], row["staff_email"], row["remote_ip"])
            for row in self.db.execute(
                "SELECT * FROM sign_ins ORDER BY at DESC LIMIT ?", (limit,)
            )
        ]

    def recent_runs(self, limit: int = 50) -> list[TaskRun]:
        return [
            _row_to_run(row)
            for row in self.db.execute(
                "SELECT * FROM task_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            )
        ]

    def run(self, run_id: str) -> TaskRun | None:
        row = self.db.execute(
            "SELECT * FROM task_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row is not None else None

    def latest_runs_by_task(self) -> list[TaskRun]:
        """One latest attempt per task, regardless of how often it runs."""
        return [_row_to_run(row) for row in self.db.execute(
            "SELECT * FROM task_runs WHERE rowid IN ("
            "SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER ("
            "PARTITION BY task ORDER BY started_at DESC, rowid DESC) AS position "
            "FROM task_runs) WHERE position = 1) ORDER BY task"
        )]


def _row_to_run(row: sqlite3.Row) -> TaskRun:
    return TaskRun(**{**dict(row), "detail": json.loads(row["detail"]) if row["detail"] else None})
