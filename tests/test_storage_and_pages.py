"""Run records, and the pages staff read them on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from principle_admin.app import app
from principle_admin.config import Environment, Settings, current_settings
from principle_admin.storage import Coverage, Outcome, Storage


@pytest.fixture
def store(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "runs.db")


def test_a_started_run_is_not_yet_trustworthy(store: Storage) -> None:
    """An unfinished run must never read as a result.

    A crashed task leaves its row RUNNING; if that counted as trustworthy the page would
    present an empty summary as a completed day.
    """
    run_id = store.start_run("daily_diary", "windows:test", "fake")
    run = store.run(run_id)
    assert run is not None
    assert run.outcome is Outcome.RUNNING
    assert run.finished_at is None
    assert not run.is_trustworthy


def test_partial_coverage_is_never_trustworthy(store: Storage) -> None:
    """Success with partial coverage is still not a full picture.

    Treating outcome alone as the signal is the mistake: the task succeeded, so a check of
    `outcome == succeeded` would pass while the report covered half the diary.
    """
    run_id = store.start_run("daily_diary", "windows:test", "fake")
    store.finish_run(run_id, Outcome.SUCCEEDED, Coverage.PARTIAL, "8 of ? booked")
    run = store.run(run_id)
    assert run is not None
    assert run.outcome is Outcome.SUCCEEDED
    assert not run.is_trustworthy


def test_detail_round_trips(store: Storage) -> None:
    """The stored report must come back as the same structure the page renders."""
    run_id = store.start_run("daily_diary", "windows:test", "fake")
    store.finish_run(
        run_id,
        Outcome.SUCCEEDED,
        Coverage.COMPLETE,
        "ok",
        detail={"byStatus": {"scheduled": 2}, "byPractitioner": []},
    )
    run = store.run(run_id)
    assert run is not None
    assert run.detail == {"byStatus": {"scheduled": 2}, "byPractitioner": []}


def test_runs_are_listed_newest_first(store: Storage) -> None:
    """Oldest-first would bury today's run below months of history."""
    first = store.start_run("daily_diary", "a", "fake")
    store.finish_run(first, Outcome.SUCCEEDED, Coverage.COMPLETE, "first")
    second = store.start_run("daily_diary", "b", "fake")
    store.finish_run(second, Outcome.SUCCEEDED, Coverage.COMPLETE, "second")
    assert next(run.run_id for run in store.recent_runs()) == second


@dataclass
class Pages:
    """A test client and a separate handle for seeding rows it should then display."""

    client: TestClient
    store: Storage


@pytest.fixture
def pages(tmp_path: Path) -> Iterator[Pages]:
    """The application with its settings pointed at a temporary directory.

    Only the settings are overridden. The real `storage` dependency then opens its own
    connection per request, on the thread that serves it, which is what production does -- a
    shared connection handed in here would pass tests that production cannot run.
    """
    configured = Settings(environment=Environment.FAKE, data_root=tmp_path)
    app.dependency_overrides[current_settings] = lambda: configured
    seeding = Storage(configured.database_path)
    yield Pages(client=TestClient(app), store=seeding)
    app.dependency_overrides.clear()
    seeding.close()


def test_the_fake_is_announced_on_every_page(pages: Pages) -> None:
    """A page built from fake data must say so.

    Without the banner a development screenshot is indistinguishable from a real report, and
    ARCHITECTURE.md requires a fake run to be unmistakable.
    """
    body = pages.client.get("/").text
    assert 'data-automation-id="fake-banner"' in body


def test_no_page_claims_a_next_run_time(pages: Pages) -> None:
    """Windows owns the schedule.

    A next-run time computed here would be a second schedule definition, and it would go on
    displaying a time after someone changed or disabled the real task.
    """
    body = pages.client.get("/").text.lower()
    assert "next run" not in body
    assert "next scheduled" not in body


def test_a_partial_run_is_flagged_on_its_page(pages: Pages) -> None:
    """The warning must be on the page, not only in the summary string.

    A reader who skips the one-line summary and reads the practitioner table would
    otherwise take a partial day for a complete one.
    """
    store = pages.store
    run_id = store.start_run("daily_diary", "windows:test", "fake")
    store.finish_run(
        run_id,
        Outcome.SUCCEEDED,
        Coverage.PARTIAL,
        "2026-09-28: 6 attending of 8 booked (PARTIAL: one practitioner unnamed)",
        detail={"coverageNote": "one practitioner unnamed", "byStatus": {}, "byPractitioner": []},
    )
    body = pages.client.get(f"/runs/{run_id}").text
    assert 'data-automation-id="not-complete-warning"' in body
    assert "one practitioner unnamed" in body


def test_a_complete_run_carries_no_warning(pages: Pages) -> None:
    """The warning must be absent when it does not apply, or it stops meaning anything."""
    store = pages.store
    run_id = store.start_run("daily_diary", "windows:test", "fake")
    store.finish_run(
        run_id,
        Outcome.SUCCEEDED,
        Coverage.COMPLETE,
        "2026-09-28: 6 attending of 8 booked",
        detail={"byStatus": {"scheduled": 8}, "byPractitioner": []},
    )
    body = pages.client.get(f"/runs/{run_id}").text
    assert 'data-automation-id="not-complete-warning"' not in body


def test_an_unknown_run_is_a_404(pages: Pages) -> None:
    """A mistyped run id must not render an empty report as a real one."""
    assert pages.client.get("/runs/deadbeef").status_code == 404


def test_health_reports_which_principle_it_is_talking_to(pages: Pages) -> None:
    """The environment must be in the payload.

    deploy/verify.ps1 reads this; a health endpoint reporting only "ok" would call a service
    healthy while it was pointed at the wrong Principle.
    """
    payload = pages.client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["principle"] == "fake"
