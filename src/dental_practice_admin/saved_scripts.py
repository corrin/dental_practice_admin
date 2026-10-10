"""Shared manual scripts, backed by immutable files and private execution evidence."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from jsonschema import FormatChecker, validate

from dental_practice_admin import scripts, task_files
from dental_practice_admin.audit import Audit
from dental_practice_admin.config import Settings
from dental_practice_admin.storage import Coverage, Outcome, Storage


def candidate(settings: Settings, identifier: str,
              owner: str) -> tuple[scripts.Script, dict[str, Any]]:
    """Candidate access retains the private draft's ownership boundary."""
    script = scripts.load_draft(settings, identifier, owner)
    path = settings.data_dir / "drafts" / (identifier + ".candidate.json")
    return script, json.loads(path.read_text(encoding="utf-8"))


def prepare(settings: Settings, script: scripts.Script, definition: task_files.Definition,
            tests: str, previous: str = "") -> str:
    """Save a candidate without executing any of its code."""
    if not script.reusable or script.language != definition.language or not tests.strip():
        raise ValueError("A repeatable script and synthetic tests are required")
    if previous:
        old = scripts.load_draft(settings, previous, script.owner)
        if old.thread != script.thread or old.source != script.source or not old.reusable:
            raise ValueError("The previous execution belongs to a different script")
        script = old
    identifier = previous if previous else scripts.save_draft(settings, script)
    path = settings.data_dir / "drafts" / (identifier + ".candidate.json")
    data = {"definition": definition.model_dump(), "tests": tests}
    if Audit(settings, identifier).clean(data) != data:
        raise ValueError("Remove credentials from the script details")
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != data:
        raise ValueError("Prepare a new version to change an existing candidate")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "source.txt").write_text(script.source, encoding="utf-8")
        (root / "test_task.py").write_text(tests, encoding="utf-8")
        result = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover",
            "-s", directory, "-p", "test_task.py"], cwd=directory,
            env={key: value for key, value in os.environ.items()
                 if key.upper() in {"SYSTEMROOT", "TEMP", "TMP", "PATH"}},
            capture_output=True, text=True, timeout=30)
        if result.returncode or "Ran 0 tests" in result.stderr or "skipped=" in result.stderr:
            raise ValueError("Synthetic tests did not pass: " + result.stderr[-2000:])
    path.write_text(json.dumps(data), encoding="utf-8")
    return identifier


def tested(settings: Settings, identifier: str, script: scripts.Script) -> bool:
    """Only successful execution of this immutable source counts as evidence."""
    with closing(Storage(settings.database_path)) as store:
        rows = store.db.execute("SELECT run_id FROM task_runs WHERE task=? AND outcome=? "
                               "AND coverage=?", ("draft:" + identifier,
                                Outcome.SUCCEEDED.value, Coverage.COMPLETE.value))
        for row in rows:
            audit = settings.data_dir / "audits" / (row[0] + ".jsonl")
            if not audit.exists():
                continue
            first = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
            if first["script"]["source"] == script.source and first["script"]["reusable"]:
                return True
    return False


def save(settings: Settings, identifier: str, owner: str, title: str) -> str:
    """Publish local files only; saving cannot execute or repeat a practice change."""
    script, data = candidate(settings, identifier, owner)
    if not title.strip() or not tested(settings, identifier, script):
        raise ValueError("Give the script a name and complete a successful test run first")
    definition = task_files.Definition.model_validate(data["definition"])
    definition.title = title.strip()
    files = {"source.txt": script.source, "task.json": definition.model_dump_json(),
             "test_task.py": data["tests"]}
    if Audit(settings, identifier).clean(files) != files:
        raise ValueError("Remove credentials from the script details")
    revision = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    target = settings.data_dir / "saved" / definition.name / revision
    import portalocker
    with portalocker.Lock(str(task_files.folder_for(settings, script.task_id) / "edit.lock"),
                          timeout=30):
        if target.exists():
            return revision
        staging = target.with_name("save-" + identifier)
        staging.mkdir(parents=True, exist_ok=True)
        for name, value in files.items():
            (staging / name).write_text(value, encoding="utf-8")
        (staging / "provenance.json").write_text(json.dumps({"draft_id": identifier,
            "owner": owner, "thread": script.thread}), encoding="utf-8")
        staging.rename(target)
    Audit(settings, "saved-scripts").write("saved", name=definition.name, revision=revision,
                                          initiator=owner)
    return revision


def folder(settings: Settings, name: str, revision: str) -> Path:
    """Apply the installed-task path constraints to shared scripts too."""
    import re
    if not re.fullmatch(task_files.NAME, name) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise FileNotFoundError("Saved script unavailable")
    path = settings.data_dir / "saved" / name / revision
    if not (path / "task.json").is_file():
        raise FileNotFoundError("Saved script unavailable")
    return path


def load(settings: Settings, name: str, revision: str, inputs: dict[str, Any],
         owner: str) -> scripts.Script:
    """Manual execution resolves shared files; the scheduler uses installed files only."""
    if not (settings.data_dir / "saved" / name / revision / "task.json").is_file():
        return scripts.Script.model_validate(
            task_files.load(settings, name, revision, inputs, owner))
    path = folder(settings, name, revision)
    definition = task_files.Definition.model_validate_json(
        (path / "task.json").read_text(encoding="utf-8"))
    validate(inputs, definition.inputs, format_checker=FormatChecker())
    return scripts.Script(language=definition.language,
        source=(path / "source.txt").read_text(encoding="utf-8"), inputs=inputs,
        owner=owner, thread="", task_id=name, revision=revision)


def publish_review(settings: Settings, name: str, revision: str,
                   checked_for_patient_data: Literal[True]) -> str:
    """Maintainer export uses shared files and the original private source provenance."""
    path = folder(settings, name, revision)
    provenance = json.loads((path / "provenance.json").read_text(encoding="utf-8"))
    review = task_files.Review(draft_id=provenance["draft_id"],
        definition=task_files.Definition.model_validate_json(
            (path / "task.json").read_text(encoding="utf-8")),
        tests=(path / "test_task.py").read_text(encoding="utf-8"),
        checked_for_patient_data=checked_for_patient_data)
    return task_files.publish(settings, review, provenance["owner"])
