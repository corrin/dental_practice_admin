"""The Windows entry point executes installed files and reports failure through its exit code."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dental_practice_admin import schedules
from dental_practice_admin.storage import Coverage, Outcome, Storage
from tests.test_automation import settings
from tests.test_task_lifecycle import REVISION, install_fake


@pytest.mark.parametrize("coverage,raises,exit_code", [
    ("complete", False, 0), ("partial", False, 1), ("partial", True, 1),
])
def test_launcher_records_the_installed_task_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    coverage: str, raises: bool, exit_code: int,
) -> None:
    configured = settings(tmp_path)
    install_fake(tmp_path)
    source = ("async def run(services, inputs):\n"
              + ("    raise RuntimeError('synthetic failure')\n" if raises else
                 f"    return {{'summary': 'Synthetic result', 'detail': {{'value': 7}}, "
                 f"'coverage': '{coverage}'}}\n"))
    (configured.data_dir / "installed" / "fake_report" / REVISION / "source.txt").write_text(
        source, encoding="utf-8")
    schedule = schedules.Schedule(name="fake_report", revision=REVISION, inputs={"numbers": [7]})
    schedules.save(configured, schedule, "fake-staff")
    with schedules.open_schedules(configured) as scheduler:
        scheduler.get_job(schedule.id).modify(
            next_run_time=datetime.now(UTC) - timedelta(seconds=1))
    monkeypatch.setattr(schedules, "Settings", lambda: configured)
    assert schedules.main() == exit_code
    store = Storage(configured.database_path)
    try:
        [result] = store.recent_runs()
        assert result.outcome is (Outcome.UNCERTAIN if raises else Outcome.SUCCEEDED)
        assert result.coverage is Coverage(coverage)
        assert result.finished_at is not None
        if not raises:
            assert result.detail is not None and result.detail["value"] == 7
        assert (configured.data_dir / "audits" / f"{result.run_id}.jsonl").is_file()
    finally:
        store.close()
