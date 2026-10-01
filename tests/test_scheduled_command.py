"""The scheduled command: what it records, and the exit code Task Scheduler sees.

`daily_diary` itself is covered by tests/test_daily_diary.py through the injected transport.
`run_daily_diary` and `main` cannot be reached that way -- they build their own client from
configuration -- so the fake Principle listens on a loopback port here and everything above the
socket stays real: settings resolution, the client, the report, and the run history in SQLite.
No credential is read and no request leaves this machine.

The end-to-end tier proves the same command across two processes in a browser. What is proved
here, and nowhere else, is the behaviour around a day that went wrong: a fetch that failed, a day
that could not be wholly labelled, a date nobody supplied, and a command line that does not parse.
"""

from __future__ import annotations

import getpass
import socket
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Self

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from dental_practice_admin import tasks
from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.storage import Coverage, Outcome, Storage, TaskRun
from dental_practice_admin.tasks import main, run_daily_diary
from tests.fake import FAKE_API_KEY, FAKE_PRACTICE_ID, FakePrinciple, FakeStore, seed
from tests.servers import DIARY_DATE, EXPECTED_BOOKED

SEEDED_DAY = date.fromisoformat(DIARY_DATE)

STARTUP_TIMEOUT = 30.0


class FrozenClock(datetime):
    """The wall clock standing still, at an instant when Auckland and UTC disagree on the date.

    13:00 UTC on the 28th is 02:00 on the 29th in the practice, so "tomorrow" is the 30th there
    and the 29th in UTC. Without a fixed instant the two coincide for eleven hours a day and a
    default computed in UTC would pass whenever CI happened to run outside the other thirteen.
    """

    NOW = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(cls.NOW.timestamp(), tz=tz)


# The seeded diary's last day: tomorrow in Auckland at the instant above.
TOMORROW_IN_AUCKLAND = "2026-09-30"


def _forget_the_practitioner_list(store: FakeStore) -> None:
    """Withdraw the names, leaving the diary: every appointment becomes unlabelled."""
    store.db.execute("DELETE FROM practitioners")
    store.db.commit()


def _serve(prepare: Callable[[FakeStore], None] | None) -> Iterator[str]:
    """The fake Principle on a loopback port, for the length of one test.

    The store is seeded on the thread that serves it, because a SQLite connection belongs to the
    thread that opened it and uvicorn serves from its own.
    """
    box: dict[str, uvicorn.Server] = {}
    built = threading.Event()

    def serve() -> None:
        store = seed()
        if prepare is not None:
            prepare(store)
        server = uvicorn.Server(
            uvicorn.Config(app=FakePrinciple(store), host="127.0.0.1", port=0, log_level="warning")
        )
        box["server"] = server
        built.set()
        server.run()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert built.wait(STARTUP_TIMEOUT), "the fake Principle never came up"
    server = box["server"]
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    listeners = server.servers[0].sockets if server.started else None
    assert listeners, f"the fake Principle was not listening within {STARTUP_TIMEOUT}s"
    yield f"http://127.0.0.1:{int(listeners[0].getsockname()[1])}"
    server.should_exit = True
    thread.join(timeout=STARTUP_TIMEOUT)


@pytest.fixture
def principle() -> Iterator[str]:
    """The seeded fake practice, reached over a real socket."""
    yield from _serve(None)


@pytest.fixture
def unlisted_practitioners() -> Iterator[str]:
    """The same diary with the practitioner list withdrawn, so the day cannot be labelled."""
    yield from _serve(_forget_the_practitioner_list)


def _unreachable() -> str:
    """An origin nothing is listening on: the shape of Principle being down."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{int(probe.getsockname()[1])}"


def _settings(origin: str, data_root: Path) -> Settings:
    return Settings(
        environment=Environment.FAKE,
        api_base_url=origin,
        api_key=SecretStr(FAKE_API_KEY),
        practice_id=FAKE_PRACTICE_ID,
        data_root=data_root,
    )


def _environment(monkeypatch: pytest.MonkeyPatch, origin: str, data_root: Path) -> Settings:
    """The same configuration, arriving the way Task Scheduler delivers it: through the environment.

    `main` builds its own `Settings`, so this is the only way to reach it. A misspelled name here
    resolves to the fake's unroutable address and the test fails on a connection error.
    """
    monkeypatch.setenv("PRINCIPLE_ENVIRONMENT", "fake")
    monkeypatch.setenv("PRINCIPLE_API_BASE_URL_FAKE", origin)
    monkeypatch.setenv("PRINCIPLE_API_KEY_FAKE", FAKE_API_KEY)
    monkeypatch.setenv("PRINCIPLE_PRACTICE_ID_FAKE", FAKE_PRACTICE_ID)
    monkeypatch.setenv("ADMIN_DATA_ROOT", str(data_root))
    return Settings()


def _runs(database: Path) -> list[TaskRun]:
    store = Storage(database)
    try:
        return store.recent_runs()
    finally:
        store.close()


async def test_a_successful_run_is_recorded_with_the_report_it_produced(
    principle: str, tmp_path: Path
) -> None:
    """The stored row is what the results page renders and what the readiness check reads.

    A task that fetched the diary and then recorded nothing, or recorded a summary unrelated to
    the report, leaves staff reading an empty page while `scripts/check_runs.py` refuses a
    deployment that works.
    """
    settings = _settings(principle, tmp_path)
    run_id = await run_daily_diary(settings, SEEDED_DAY, "windows:test")

    [run] = _runs(settings.database_path)
    assert run.run_id == run_id
    assert run.outcome is Outcome.SUCCEEDED
    assert run.coverage is Coverage.COMPLETE
    assert run.is_trustworthy
    assert run.finished_at is not None, "a finished run that still looks crashed is a false alarm"
    assert run.initiator == "windows:test"
    assert run.principle == "fake"
    assert f"of {EXPECTED_BOOKED} booked" in run.summary
    assert run.detail is not None
    assert run.detail["onDate"] == DIARY_DATE
    assert len(run.detail["byPractitioner"]) == 2
    assert sum(run.detail["byStatus"].values()) == EXPECTED_BOOKED


async def test_a_run_that_cannot_reach_principle_is_recorded_failed_and_still_raises(
    tmp_path: Path,
) -> None:
    """A failed fetch leaves a finished, untrustworthy row, and the exception still escapes.

    Swallowing it would file an empty day as a result; leaving the row unfinished would hide a
    known failure among genuinely crashed runs, where a missing finish is the only signal.
    """
    settings = _settings(_unreachable(), tmp_path)
    with pytest.raises(httpx.HTTPError):
        await run_daily_diary(settings, SEEDED_DAY, "windows:test")

    [run] = _runs(settings.database_path)
    assert run.outcome is Outcome.FAILED
    assert run.coverage is Coverage.PARTIAL
    assert not run.is_trustworthy
    assert run.finished_at is not None
    assert run.summary, "an empty summary tells whoever reads the page nothing"
    assert run.detail is None


async def test_a_day_it_cannot_wholly_label_is_recorded_partial(
    unlisted_practitioners: str, tmp_path: Path
) -> None:
    """Success and completeness are different questions, and the row must answer both.

    The fetch worked, so recording the outcome alone would let the page render a day whose
    practitioners are all unnamed as though it were the full picture.
    """
    settings = _settings(unlisted_practitioners, tmp_path)
    await run_daily_diary(settings, SEEDED_DAY, "windows:test")

    [run] = _runs(settings.database_path)
    assert run.outcome is Outcome.SUCCEEDED
    assert run.coverage is Coverage.PARTIAL
    assert not run.is_trustworthy
    assert run.detail is not None
    assert run.detail["coverageNote"]
    assert "PARTIAL" in run.summary


def test_the_command_reports_the_run_it_recorded(
    principle: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Standard output and the exit code are all Task Scheduler and an operator ever see.

    Reaching this line at all also proves the web-build check is skipped for the fake, which has
    no website to read: calling it would raise rather than warn.
    """
    settings = _environment(monkeypatch, principle, tmp_path)
    assert main(["diary", "--date", DIARY_DATE, "--initiator", "windows:test"]) == 0

    printed = capsys.readouterr().out
    [run] = _runs(settings.database_path)
    assert "succeeded complete" in printed
    assert run.run_id in printed, "the identifier printed is not the run that was stored"
    assert DIARY_DATE in printed
    assert f"of {EXPECTED_BOOKED} booked" in printed


def test_the_command_exits_non_zero_for_a_day_it_cannot_wholly_see(
    unlisted_practitioners: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 0 for a partial day is the failure nobody notices for a week.

    The scheduler shows only success or failure, so a report that could not name its
    practitioners has to be visible there and not only on a page someone may never open.
    """
    settings = _environment(monkeypatch, unlisted_practitioners, tmp_path)
    assert main(["diary", "--date", DIARY_DATE]) == 1
    assert "succeeded partial" in capsys.readouterr().out
    assert not _runs(settings.database_path)[0].is_trustworthy


def test_the_command_defaults_to_tomorrow_in_the_practice_s_time_zone(
    principle: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduled task passes no date, so the default is the whole behaviour.

    "Tomorrow" in UTC is still today in New Zealand for thirteen hours a day, and a run in the
    early morning would report a day that is already over. The initiator defaults to the account
    running the task, because a run nobody is named on cannot be followed up.
    """
    monkeypatch.setattr(tasks, "datetime", FrozenClock)
    settings = _environment(monkeypatch, principle, tmp_path)
    assert main(["diary"]) == 0

    [run] = _runs(settings.database_path)
    assert run.detail is not None
    assert run.detail["onDate"] == TOMORROW_IN_AUCKLAND
    assert run.detail["appointments"] == EXPECTED_BOOKED, "the default day must be a seeded one"
    assert run.initiator == f"windows:{getpass.getuser()}"


@pytest.mark.parametrize(
    "argv", [[], ["diary", "--date", "28/09/2026"], ["diary", "--date", DIARY_DATE, "--initiator"]]
)
def test_an_unusable_command_line_records_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """A typo in the scheduled command must fail before anything is fetched or written.

    Recording a run for a command that never ran would put a row on the page that reads like a
    result, and exiting 0 would tell the scheduler nothing was wrong.
    """
    settings = _environment(monkeypatch, _unreachable(), tmp_path)
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2
    assert _runs(settings.database_path) == []
