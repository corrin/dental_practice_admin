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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic.dataclasses import dataclass

from dental_practice_admin.akahu import Deposit

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

-- Bank reconciliation (docs/plans/bank-reconciliation.md). A deposit is kept by its Akahu id
-- with a status, so an unfinished visit to the page never loses one.
CREATE TABLE IF NOT EXISTS bank_deposits (
    akahu_id     TEXT PRIMARY KEY,
    date         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    description  TEXT NOT NULL,
    particulars  TEXT,
    code         TEXT,
    reference    TEXT,
    status       TEXT NOT NULL,
    decided_by   TEXT,
    decided_at   TEXT,
    note         TEXT
);
-- One row per Principle payment or invoice a deposit was matched to. A Phase 1 match to an
-- invoice has no transaction: the payment is still keyed in Principle by hand.
CREATE TABLE IF NOT EXISTS bank_matches (
    akahu_id                 TEXT NOT NULL REFERENCES bank_deposits(akahu_id),
    principle_transaction_id TEXT,
    patient_id               TEXT NOT NULL,
    invoice_id               TEXT NOT NULL,
    amount_cents             INTEGER NOT NULL,
    created_here             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bank_matches_by_deposit ON bank_matches(akahu_id);
-- A Principle payment row covers at most one deposit.
CREATE UNIQUE INDEX IF NOT EXISTS bank_matches_one_deposit_per_payment
    ON bank_matches(principle_transaction_id, invoice_id)
    WHERE principle_transaction_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS bank_account (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    refreshed TEXT NOT NULL
);
-- The last Fetch now, and what went wrong in it, so every visitor sees a failed fetch.
CREATE TABLE IF NOT EXISTS bank_fetch (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    fetched_at TEXT NOT NULL,
    problems   TEXT NOT NULL
);

-- What the reconcile page needs from Principle, so showing it makes no calls. `kind` is
-- invoice (unpaid only), payment (complete only, one row per invoice it pays) or patient.
CREATE TABLE IF NOT EXISTS principle_cache (
    kind       TEXT NOT NULL,
    key        TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    body       TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS principle_cache_marks (
    kind    TEXT PRIMARY KEY,
    read_to TEXT NOT NULL
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

    # -- bank reconciliation -------------------------------------------------

    def record_deposits(self, deposits: Iterable[Deposit], refreshed: str) -> None:
        """Add new deposits as open, and refresh what the bank sent for ones still open.

        A deposit already matched or excluded keeps the amount it was decided on, so its
        matches always add up to it.
        """
        with self._write() as db:
            db.executemany(
                "INSERT INTO bank_deposits (akahu_id, date, amount_cents, description,"
                " particulars, code, reference, status) VALUES (:akahu_id, :date,"
                " :amount_cents, :description, :particulars, :code, :reference, 'open')"
                " ON CONFLICT(akahu_id) DO UPDATE SET date=excluded.date,"
                " amount_cents=excluded.amount_cents, description=excluded.description,"
                " particulars=excluded.particulars, code=excluded.code,"
                " reference=excluded.reference WHERE bank_deposits.status = 'open'",
                [asdict(deposit) for deposit in deposits])
            db.execute("INSERT INTO bank_account VALUES (1, ?) ON CONFLICT(id) DO UPDATE"
                       " SET refreshed=excluded.refreshed", (refreshed,))

    def record_fetch(self, problems: list[str]) -> None:
        with self._write() as db:
            db.execute("INSERT OR REPLACE INTO bank_fetch VALUES (1, ?, ?)",
                       (now(), json.dumps(problems)))

    def last_fetch(self) -> tuple[str, list[str]] | None:
        """When Fetch now last ran and what went wrong; None if it never has."""
        row = self.db.execute("SELECT fetched_at, problems FROM bank_fetch").fetchone()
        return None if row is None else (row["fetched_at"], json.loads(row["problems"]))

    def bank_refreshed(self) -> str | None:
        """When Akahu last read the account from the bank; None before the first fetch."""
        row = self.db.execute("SELECT refreshed FROM bank_account").fetchone()
        return None if row is None else str(row["refreshed"])

    def oldest_open_deposit(self) -> str | None:
        """The date of the oldest deposit nobody has dealt with yet."""
        row = self.db.execute(
            "SELECT MIN(date) AS day FROM bank_deposits WHERE status = 'open'").fetchone()
        return None if row["day"] is None else str(row["day"])

    def deposits(self, status: str) -> list[dict[str, Any]]:
        """Deposits in one status, newest first, each with the rows it was matched to."""
        rows = [dict(row) for row in self.db.execute(
            "SELECT * FROM bank_deposits WHERE status = ? ORDER BY date DESC, akahu_id",
            (status,))]
        for row in rows:
            row["matches"] = [dict(m) for m in self.db.execute(
                "SELECT * FROM bank_matches WHERE akahu_id = ?", (row["akahu_id"],))]
        return rows

    def deposit(self, akahu_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM bank_deposits WHERE akahu_id = ?", (akahu_id,)).fetchone()
        return None if row is None else dict(row)

    def claimed_payments(self) -> set[tuple[str, str]]:
        """(transaction id, invoice id) of every Principle payment a deposit already covers."""
        return {(row[0], row[1]) for row in self.db.execute(
            "SELECT principle_transaction_id, invoice_id FROM bank_matches"
            " WHERE principle_transaction_id IS NOT NULL")}

    def match_deposit(self, akahu_id: str, matches: list[MatchRow], staff: str) -> None:
        """Mark an open deposit matched to rows that add up to it exactly.

        Checked inside the write, so two people matching at once cannot both succeed. The
        unique index refuses a payment that already covers a deposit.
        """
        with self._write() as db:
            deposit = db.execute("SELECT amount_cents, status FROM bank_deposits"
                                 " WHERE akahu_id = ?", (akahu_id,)).fetchone()
            if deposit is None or deposit["status"] != "open":
                raise MatchRefusedError("the deposit is no longer open")
            if not matches or any(m.amount_cents <= 0 for m in matches):
                raise MatchRefusedError("every matched amount must be positive")
            if sum(m.amount_cents for m in matches) != deposit["amount_cents"]:
                raise MatchRefusedError("the matched amounts do not add up to the deposit")
            try:
                db.executemany(
                    "INSERT INTO bank_matches VALUES (:akahu_id, :principle_transaction_id,"
                    " :patient_id, :invoice_id, :amount_cents, 0)",
                    [{"akahu_id": akahu_id, **asdict(m)} for m in matches])
            except sqlite3.IntegrityError:
                raise MatchRefusedError("a payment is already matched to a deposit") from None
            db.execute("UPDATE bank_deposits SET status = 'matched', decided_by = ?,"
                       " decided_at = ? WHERE akahu_id = ?", (staff, now(), akahu_id))

    def exclude_deposit(self, akahu_id: str, reason: str, staff: str) -> None:
        """Set an open deposit aside as not a patient payment, saying why."""
        if not reason.strip():
            raise MatchRefusedError("an exclusion needs a reason")
        with self._write() as db:
            changed = db.execute(
                "UPDATE bank_deposits SET status = 'excluded', note = ?, decided_by = ?,"
                " decided_at = ? WHERE akahu_id = ? AND status = 'open'",
                (reason.strip(), staff, now(), akahu_id)).rowcount
            if changed != 1:
                raise MatchRefusedError("the deposit is no longer open")

    def reopen_deposit(self, akahu_id: str, status: str) -> None:
        """Unreconcile a matched deposit or restore an excluded one; Principle is untouched."""
        with self._write() as db:
            changed = db.execute(
                "UPDATE bank_deposits SET status = 'open', note = NULL, decided_by = NULL,"
                " decided_at = NULL WHERE akahu_id = ? AND status = ?",
                (akahu_id, status)).rowcount
            if changed != 1:
                raise MatchRefusedError(f"the deposit is no longer {status}")
            db.execute("DELETE FROM bank_matches WHERE akahu_id = ?", (akahu_id,))

    def cache_mark(self, kind: str) -> str | None:
        """The instant a kind was last read up to; None before its first full read."""
        row = self.db.execute("SELECT read_to FROM principle_cache_marks WHERE kind = ?",
                              (kind,)).fetchone()
        return None if row is None else str(row["read_to"])

    def update_cache(self, kind: str, put: Iterable[tuple[str, str, dict[str, Any]]],
                     drop: Iterable[str], read_to: str | None) -> None:
        """Store (key, patient id, body) rows, remove keys, and move the read mark."""
        with self._write() as db:
            db.executemany("INSERT OR REPLACE INTO principle_cache VALUES (?, ?, ?, ?)",
                           [(kind, key, patient, json.dumps(body)) for key, patient, body in put])
            db.executemany("DELETE FROM principle_cache WHERE kind = ? AND key = ?",
                           [(kind, key) for key in drop])
            if read_to is not None:
                db.execute("INSERT OR REPLACE INTO principle_cache_marks VALUES (?, ?)",
                           (kind, read_to))

    def cached(self, kind: str) -> dict[str, dict[str, Any]]:
        """Every cached body of one kind, by key."""
        return {row["key"]: json.loads(row["body"]) for row in self.db.execute(
            "SELECT key, body FROM principle_cache WHERE kind = ?", (kind,))}


class MatchRefusedError(ValueError):
    """A match, exclusion or reopening the deposit's current state does not allow."""


@dataclass(frozen=True)
class MatchRow:
    """One Principle payment or invoice a deposit covers, in cents."""

    patient_id: str
    invoice_id: str
    amount_cents: int
    principle_transaction_id: str | None


def _row_to_run(row: sqlite3.Row) -> TaskRun:
    return TaskRun(**{**dict(row), "detail": json.loads(row["detail"]) if row["detail"] else None})
