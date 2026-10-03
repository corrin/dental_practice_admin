"""Saving shares immutable code without repeating a live operation."""
import json
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from dental_practice_admin import saved_scripts, schedules, scripts, task_files
from dental_practice_admin.app import create_app
from dental_practice_admin.auth import FAKE_STAFF
from dental_practice_admin.config import SignIn
from dental_practice_admin.storage import Storage
from tests.test_automation import settings

SOURCE = '''from pathlib import Path
async def run(services, inputs):
    path = Path(inputs['marker'])
    path.write_text(path.read_text() + 'x' if path.exists() else 'x')
    return {'summary': 'Synthetic change complete', 'detail': {}, 'coverage': 'complete'}
'''
TESTS = '''import unittest, asyncio, tempfile
from pathlib import Path
namespace = {}
exec(Path(__file__).with_name('source.txt').read_text(), namespace)
class ScriptTest(unittest.TestCase):
    def test_change(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / 'marker'
            asyncio.run(namespace['run'](None, {'marker': str(marker)}))
            self.assertEqual(marker.read_text(), 'x')
'''
DEFINITION = task_files.Definition(name="synthetic_change", title="Synthetic change",
    description="Append to a synthetic local file", inputs={"type": "object",
        "properties": {"marker": {"type": "string"}}, "required": ["marker"],
        "additionalProperties": False})


async def test_save_never_repeats_a_change_and_review_gates_scheduling(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    script = scripts.Script(source=SOURCE, language="python", inputs={},
                            owner=FAKE_STAFF, thread="private-thread")
    identifier = saved_scripts.prepare(configured, script, DEFINITION, TESTS)
    with pytest.raises(ValueError):
        saved_scripts.save(configured, identifier, FAKE_STAFF, "Synthetic change")
    script = scripts.load_draft(configured, identifier, FAKE_STAFF)
    marker = tmp_path / "private-marker"
    script.inputs = {"marker": str(marker)}
    await scripts.run(configured, script, "draft:" + identifier)
    revision = saved_scripts.save(configured, identifier, FAKE_STAFF, "Synthetic change")
    assert saved_scripts.save(configured, identifier, FAKE_STAFF, "Synthetic change") == revision
    assert marker.read_text() == "x"
    shared = saved_scripts.load(configured, DEFINITION.name, revision, script.inputs, "other@fake")
    assert shared.source == SOURCE and shared.owner == "other@fake"
    await scripts.run(configured, shared, DEFINITION.name)
    assert marker.read_text() == "xx"
    with pytest.raises(FileNotFoundError):
        schedules.save(configured, schedules.Schedule(name=DEFINITION.name,
            revision=revision, inputs=script.inputs), FAKE_STAFF)
    with pytest.raises(FileNotFoundError):
        saved_scripts.candidate(configured, identifier, "other@fake")
    folder = saved_scripts.folder(configured, DEFINITION.name, revision)
    assert str(marker) not in (folder / "task.json").read_text()
    assert str(marker) not in (folder / "source.txt").read_text()
    scripts_path = configured.data_dir / "drafts" / (identifier + ".json")
    scripts_path.unlink()
    assert saved_scripts.load(configured, DEFINITION.name, revision, script.inputs,
                              FAKE_STAFF).source == SOURCE


def test_preparation_rejects_broken_tests_and_foreign_evidence(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    script = scripts.Script(source=SOURCE, language="python", inputs={}, owner="a", thread="t")
    with pytest.raises(ValueError):
        saved_scripts.prepare(configured, script, DEFINITION, "import unittest")
    identifier = saved_scripts.prepare(configured, script, DEFINITION, TESTS)
    with pytest.raises(FileNotFoundError):
        saved_scripts.prepare(configured, script.model_copy(update={"owner": "b"}),
                              DEFINITION, TESTS, identifier)
    with pytest.raises(ValueError):
        saved_scripts.prepare(configured, script.model_copy(update={"source": "different"}),
                              DEFINITION, TESTS, identifier)
    with pytest.raises(ValueError):
        saved_scripts.prepare(configured, script.model_copy(update={"reusable": False}),
                              DEFINITION, TESTS)


def test_staff_flow_and_server_schedule_guard(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(update={"sign_in": SignIn.DEVELOPER,
                                                     "openai_api_key": SecretStr("fake-ai")})
    script = scripts.Script(source=SOURCE, language="python", inputs={},
                            owner=FAKE_STAFF, thread="private-thread")
    identifier = saved_scripts.prepare(configured, script, DEFINITION, TESTS)
    inputs = {"marker": str(tmp_path / "marker")}
    with TestClient(create_app(configured)) as client:
        assert client.get("/tasks/prepare/" + identifier).status_code == 200
        assert client.post("/tasks/save", json={"draft_id": identifier,
            "title": "My script"}).status_code == 400
        assert client.post("/tasks/test", json={"draft_id": identifier,
            "inputs": inputs}).status_code == 200
        assert client.post("/tasks/save", json={"draft_id": identifier,
            "title": "My script"}).status_code == 200
        path = next((configured.data_dir / "saved").glob("*/*/task.json"))
        payload = {"name": DEFINITION.name, "revision": path.parent.name, "inputs": inputs}
        assert client.post("/tasks/schedule", json=payload).status_code == 404
        assert client.post("/tasks/request-review", json=payload).status_code == 200
        assert client.post("/tasks/request-review", json=payload).status_code == 200
        assert "My script" in client.get("/tasks/manage").text
        request = json.loads((path.parent / "review-request.json").read_text())
        assert request["initiator"] == FAKE_STAFF
        with closing(Storage(configured.database_path)) as store:
            assert len(store.recent_runs()) == 1
