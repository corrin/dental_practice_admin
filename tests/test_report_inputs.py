"""A report's date can mean "today" when it runs, and its practitioner is chosen from Principle."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import ValidationError as SchemaError
from pydantic import SecretStr

from dental_practice_admin import schedules, task_files
from dental_practice_admin.app import create_app
from dental_practice_admin.config import Settings
from tests.test_automation import SOURCE, settings

REVISION = "b" * 40
DAY_SHEET = {"type": "object", "additionalProperties": False, "properties": {
    "date": {"type": "string", "format": "date", "default": "today"},
    "practitioner": {"type": "string", "minLength": 1, "format": "practitioner"}}}


def install(root: Path) -> Settings:
    configured = settings(root)
    folder = configured.data_dir / "installed" / "day_report" / REVISION
    folder.mkdir(parents=True)
    (folder / "task.json").write_text(task_files.Definition(
        name="day_report", title="Day report", description="Synthetic",
        inputs=DAY_SHEET).model_dump_json(), encoding="utf-8")
    (folder / "source.txt").write_text(SOURCE, encoding="utf-8")
    (folder.parent / "current").write_text(REVISION, encoding="utf-8")
    return configured


def test_a_date_word_the_application_does_not_know_is_refused(tmp_path: Path) -> None:
    configured = install(tmp_path)
    with pytest.raises(SchemaError):
        task_files.load(configured, "day_report", REVISION, {"date": "yesterday"}, "fake-staff")


@pytest.mark.parametrize(("word", "days"), [("today", 0), ("tomorrow", 1)])
def test_a_relative_date_reaches_the_task_as_the_date_it_means(
        tmp_path: Path, word: str, days: int) -> None:
    configured = install(tmp_path)
    script = task_files.load(configured, "day_report", REVISION, {"date": word}, "fake-staff")
    assert script.inputs["date"] == (date.today() + timedelta(days=days)).isoformat()


def test_a_chosen_date_reaches_the_task_unchanged(tmp_path: Path) -> None:
    configured = install(tmp_path)
    script = task_files.load(configured, "day_report", REVISION, {"date": "2026-10-30"},
                             "fake-staff")
    assert script.inputs["date"] == "2026-10-30"


def test_a_schedule_keeps_today_so_each_run_is_for_its_own_day(tmp_path: Path) -> None:
    """A weekday schedule saved on Monday for "today" must not print Monday's sheet on Friday."""
    configured = install(tmp_path)
    schedule = schedules.Schedule(name="day_report", inputs={"date": "today"})
    schedules.save(configured, schedule, "fake-staff")
    with schedules.open_schedules(configured) as scheduler:
        assert scheduler.get_job(schedule.id).args[1] == {"date": "today"}


def test_the_practitioner_list_says_when_principle_refuses(
        tmp_path: Path, principle_settings: Settings) -> None:
    refused = principle_settings.model_copy(update={"api_key": SecretStr("fake-wrong-key")})
    with TestClient(create_app(refused)) as client:
        response = client.get("/tasks/practitioners")
    assert response.status_code == 502
    assert "practitioners" in response.json()["detail"]


def test_the_practitioner_list_is_principles_names_in_order(
        principle_settings: Settings) -> None:
    with TestClient(create_app(principle_settings)) as client:
        response = client.get("/tasks/practitioners")
    assert response.status_code == 200
    assert response.json() == ["Dr Ada Whitwell", "Dr Bo Ferriter"]
