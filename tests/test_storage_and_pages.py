"""Run records, and the pages staff read them on."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dental_practice_admin import scripts
from dental_practice_admin.app import create_app
from dental_practice_admin.storage import Coverage, Outcome, Storage
from tests.settings import fake_settings


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


def test_sign_ins_are_recorded_newest_first(store: Storage) -> None:
    """Who signed in, and when.

    Caddy's access log has addresses, not people. If this record were dropped, "which staff member
    opened this, and when" would be unanswerable after the fact.
    """
    store.record_sign_in("nurse@practice.nz", "203.0.113.7")
    store.record_sign_in("reception@practice.nz", None)
    recorded = store.recent_sign_ins()
    assert [email for _at, email, _ip in recorded] == [
        "reception@practice.nz",
        "nurse@practice.nz",
    ]
    assert recorded[1][2] == "203.0.113.7"


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
    configured = fake_settings(tmp_path)
    app = create_app(configured)
    seeding = Storage(configured.database_path)
    with TestClient(app) as client:
        yield Pages(client=client, store=seeding)
    seeding.close()


def test_the_fake_is_announced_on_every_page(pages: Pages) -> None:
    """A page built from fake data must say so.

    Without the banner a development screenshot is indistinguishable from a real report, and
    ARCHITECTURE.md requires a fake run to be unmistakable.
    """
    body = pages.client.get("/").text
    assert 'data-automation-id="fake-banner"' in body


def test_interface_warning_is_visible_on_staff_pages(pages: Pages) -> None:
    pages.store.interface_warning("searchPatients", "fake-release", "response_schema")
    run_id = pages.store.start_run("daily_diary", "fake-user", "fake")
    for path in ("/", "/chat", f"/runs/{run_id}"):
        response = pages.client.get(path)
        assert response.status_code == 200
        assert 'data-automation-id="principle-interface-warning"' in response.text
        assert "searchPatients" in response.text


def test_compatible_pages_have_no_interface_warning(pages: Pages) -> None:
    assert 'data-automation-id="principle-interface-warning"' not in pages.client.get("/").text


def test_results_link_to_application_schedule_controls(pages: Pages) -> None:
    """The application owns schedules and provides staff controls for them."""
    body = pages.client.get("/").text.lower()
    assert 'href="/tasks/manage"' in body


def test_frequent_runs_do_not_hide_other_scripts(pages: Pages) -> None:
    store = pages.store
    weekly = [store.start_run(f"weekly_{n}", "fake-user", "fake") for n in range(6)]
    frequent = [store.start_run("phone_standardiser", "fake-user", "fake") for _ in range(60)]
    store.finish_run(frequent[-1], Outcome.FAILED, Coverage.PARTIAL, "Latest attempt failed")
    draft = store.start_run("draft:private", "fake-user", "fake")
    response = pages.client.get("/tasks/manage")
    assert response.status_code == 200
    for run_id in [*weekly, frequent[-1]]:
        assert f'href="/runs/{run_id}"' in response.text
    for run_id in [*frequent[:-1], draft]:
        assert f'href="/runs/{run_id}"' not in response.text
    assert "Latest attempt failed" in response.text
    assert "Needs attention" in response.text
    assert store.run(frequent[0]) is not None


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

    scripts/verify.ps1 reads this; a health endpoint reporting only "ok" would call a service
    healthy while it was pointed at the wrong Principle.
    """
    payload = pages.client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["principle"] == "fake"


ATTENTION = 'data-automation-id="staff-attention"'
FOR_STAFF = [{"patient": "Fake Dummy", "field": "phone", "value": "ring mum",
              "problem": "not a number", "href": "https://principle.invalid/patients/fake"}]


def _launcher_checked(pages: Pages, ago: timedelta) -> None:
    configured = pages.client.app.state.settings  # type: ignore[attr-defined]
    heartbeat = configured.data_dir / "audits" / "launcher.jsonl"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text("{}\n", encoding="utf-8")
    when = (datetime.now(UTC) - ago).timestamp()
    os.utime(heartbeat, (when, when))


def _ran(pages: Pages, outcome: str, coverage: str, detail: dict[str, object],
         initiator: str = "scheduler") -> str:
    run_id = pages.store.start_run("fake_report", initiator, "fake")
    if outcome != "running":
        pages.store.finish_run(run_id, Outcome(outcome), Coverage(coverage), "Synthetic",
                               detail)
    return run_id


def test_a_list_for_staff_is_announced_and_shown(pages: Pages) -> None:
    run_id = _ran(pages, "succeeded", "complete", {"for_staff": FOR_STAFF})
    body = pages.client.get("/chat").text
    assert ATTENTION in body
    assert f'href="/runs/{run_id}"' in body
    run = pages.client.get(f"/runs/{run_id}").text
    assert 'data-automation-id="for-staff"' in run
    assert "ring mum" in run
    assert 'href="https://principle.invalid/patients/fake"' in run


def test_stopped_automatic_runs_are_announced(pages: Pages) -> None:
    assert ATTENTION not in pages.client.get("/").text
    _ran(pages, "succeeded", "complete", {})
    assert ATTENTION in pages.client.get("/").text
    _launcher_checked(pages, timedelta(minutes=1))
    assert ATTENTION not in pages.client.get("/").text
    _launcher_checked(pages, timedelta(hours=1))
    assert ATTENTION in pages.client.get("/").text


def test_a_launcher_busy_with_a_long_run_is_not_called_stopped(pages: Pages) -> None:
    _launcher_checked(pages, timedelta(minutes=40))
    run_id = _ran(pages, "running", "partial", {})
    started = (datetime.now(UTC) - timedelta(minutes=32)).isoformat()
    pages.store.db.execute("UPDATE task_runs SET started_at=? WHERE run_id=?", (started, run_id))
    assert ATTENTION not in pages.client.get("/").text


def test_an_incomplete_last_run_is_announced(pages: Pages) -> None:
    run_id = _ran(pages, "uncertain", "partial", {})
    body = pages.client.get("/").text
    assert ATTENTION in body
    assert f'href="/runs/{run_id}"' in body


def test_a_healthy_or_running_task_is_not_announced(pages: Pages) -> None:
    _launcher_checked(pages, timedelta(minutes=1))
    _ran(pages, "succeeded", "complete", {"for_staff": []})
    assert ATTENTION not in pages.client.get("/").text
    _ran(pages, "running", "partial", {})
    assert ATTENTION not in pages.client.get("/").text


def test_a_run_started_by_a_person_is_not_announced(pages: Pages) -> None:
    _ran(pages, "uncertain", "partial", {"for_staff": FOR_STAFF}, initiator="fake-staff")
    assert ATTENTION not in pages.client.get("/").text


def test_a_person_re_running_a_scheduled_task_replaces_its_result(pages: Pages) -> None:
    _launcher_checked(pages, timedelta(minutes=1))
    _ran(pages, "uncertain", "partial", {})
    _ran(pages, "succeeded", "complete", {"for_staff": FOR_STAFF}, initiator="fake-staff")
    assert ATTENTION in pages.client.get("/").text
    _ran(pages, "succeeded", "complete", {}, initiator="fake-staff")
    assert ATTENTION not in pages.client.get("/").text


def test_the_tasks_page_shows_the_banner(pages: Pages) -> None:
    _launcher_checked(pages, timedelta(minutes=1))
    _ran(pages, "succeeded", "complete", {"for_staff": FOR_STAFF})
    assert ATTENTION in pages.client.get("/tasks/manage").text


@pytest.mark.parametrize("for_staff", [3, [{"patient": 3}], [{"href": "javascript:alert(1)"}],
                                       [{"patient": "Fake A"}, {"problem": "Fake"}]])
def test_a_malformed_list_for_staff_is_refused_when_a_task_returns(for_staff: object) -> None:
    with pytest.raises(ValidationError):
        scripts.Result.model_validate({"summary": "Synthetic", "coverage": "complete",
                                       "detail": {"for_staff": for_staff}})
