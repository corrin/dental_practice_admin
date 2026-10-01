"""Readiness must distinguish an empty database from a working scheduled operation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts.check_runs import check

from dental_practice_admin.storage import Coverage, Outcome, Storage


@pytest.mark.parametrize("failure", ["empty", "failed", "partial", "staging", "stale", "running"])
def test_unusable_diary_cannot_pass_verification(tmp_path: Path, failure: str) -> None:
    path = tmp_path / "runs.db"
    store = Storage(path)
    if failure != "empty":
        identifier = store.start_run(
            "daily_diary", "fake-scheduler", "staging" if failure == "staging" else "production"
        )
        if failure != "running":
            store.finish_run(
                identifier,
                Outcome.FAILED if failure == "failed" else Outcome.SUCCEEDED,
                Coverage.PARTIAL if failure == "partial" else Coverage.COMPLETE,
                "fake-report",
            )
        if failure == "stale":
            store.db.execute(
                "UPDATE task_runs SET finished_at = ?",
                ((datetime.now(UTC) - timedelta(days=3)).isoformat(),),
            )
    store.close()
    with pytest.raises(ValueError):
        check(path)


def test_recent_complete_diary_passes(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    store = Storage(path)
    identifier = store.start_run("daily_diary", "fake-scheduler", "production")
    store.finish_run(identifier, Outcome.SUCCEEDED, Coverage.COMPLETE, "fake-report")
    store.close()
    check(path)
