"""A report's date can mean "today" when it runs, and its practitioner is chosen from Principle."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import ValidationError as SchemaError
from pydantic import SecretStr

from dental_practice_admin import saved_scripts, schedules, scripts, task_files
from dental_practice_admin.app import create_app
from dental_practice_admin.auth import FAKE_STAFF
from dental_practice_admin.config import Settings, SignIn
from tests.fake_akahu import FAKE_AKAHU_SETTINGS
from tests.test_automation import SOURCE, settings

REVISION = "b" * 40
DAY_SHEET = {"type": "object", "additionalProperties": False, "properties": {
    "date": {"type": "string", "format": "date", "default": "today"},
    "practitioner": {"type": "string", "minLength": 1, "format": "practitioner"}}}


def install(root: Path, name: str = "day_report", inputs: dict[str, Any] = DAY_SHEET) -> Settings:
    configured = settings(root)
    folder = configured.data_dir / "installed" / name / REVISION
    folder.mkdir(parents=True)
    (folder / "task.json").write_text(task_files.Definition(
        name=name, title=name.replace("_", " ").capitalize(), description="Synthetic",
        inputs=inputs).model_dump_json(), encoding="utf-8")
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


DATED_SOURCE = '''from pathlib import Path
async def run(services, inputs):
    Path(inputs['marker']).write_text(inputs['date'])
    return {'summary': 'Synthetic dated report', 'detail': {}, 'coverage': 'complete'}
'''
DATED_TESTS = '''import unittest, asyncio, tempfile
from pathlib import Path
namespace = {}
exec(Path(__file__).with_name('source.txt').read_text(), namespace)
class ScriptTest(unittest.TestCase):
    def test_date(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / 'marker'
            asyncio.run(namespace['run'](None, {'marker': str(marker), 'date': '2026-10-30'}))
            self.assertEqual(marker.read_text(), '2026-10-30')
'''
DATED = task_files.Definition(name="dated_report", title="Dated report",
    description="Write the date it was given", inputs={"type": "object",
        "properties": {"marker": {"type": "string"},
                       "date": {"type": "string", "format": "date", "default": "today"}},
        "required": ["marker"], "additionalProperties": False})


def test_a_draft_being_tried_gets_today_as_a_date(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(update={
        "sign_in": SignIn.DEVELOPER, "openai_api_key": SecretStr("fake-ai"),
        **FAKE_AKAHU_SETTINGS})
    script = scripts.Script(source=DATED_SOURCE, language="python", inputs={},
                            owner=FAKE_STAFF, thread="private-thread")
    identifier = saved_scripts.prepare(configured, script, DATED, DATED_TESTS)
    marker = tmp_path / "marker"
    with TestClient(create_app(configured)) as client:
        response = client.post("/tasks/test", json={"draft_id": identifier,
                               "inputs": {"marker": str(marker), "date": "today"}})
    assert response.status_code == 200
    assert marker.read_text() == date.today().isoformat()


async def test_a_run_due_before_midnight_is_for_the_day_it_was_due(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher may start a 23:58 run at 00:01; "today" still means the day it was due."""
    configured = install(tmp_path)
    schedule = schedules.Schedule(name="day_report", inputs={"date": "today"})
    schedules.save(configured, schedule, "fake-staff")
    started = datetime(2026, 10, 5, 0, 1).astimezone()  # this host's zone, on that day
    with schedules.open_schedules(configured) as scheduler:
        scheduler.get_job(schedule.id).modify(next_run_time=started - timedelta(minutes=3))
    given: list[str] = []
    load = task_files.load

    def noting(*args: Any, **kwargs: Any) -> Any:
        script = load(*args, **kwargs)
        given.append(script.inputs["date"])
        return script

    monkeypatch.setattr(task_files, "load", noting)
    await schedules.run_due(configured, started)
    assert given == ["2026-10-04"]
