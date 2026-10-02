"""File-backed development and approved local execution, independent of GitHub."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from git import Repo
from jsonschema import ValidationError as SchemaError
from pydantic import SecretStr, ValidationError

from dental_practice_admin import schedules, scripts, task_files
from dental_practice_admin.audit import Audit, observed, recording
from dental_practice_admin.storage import Outcome, Storage
from tests.test_automation import SOURCE, draft, settings

REVISION = "a" * 40
CONTRACT = {"type": "object", "properties": {"numbers": {"type": "array",
            "items": {"type": "number"}}}, "required": ["numbers"], "additionalProperties": False}


def install_fake(root: Path) -> task_files.Definition:
    definition = task_files.Definition(name="fake_report", title="Fake report",
        description="Synthetic arithmetic", inputs=CONTRACT)
    folder = settings(root).data_dir / "installed" / definition.name / REVISION
    folder.mkdir(parents=True)
    (folder / "task.json").write_text(definition.model_dump_json(), encoding="utf-8")
    (folder / "source.txt").write_text(SOURCE, encoding="utf-8")
    return definition


def test_local_refinements_share_history_without_any_remote(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    first = draft()
    first_id = scripts.save_draft(configured, first)
    second = first.model_copy(update={"source": SOURCE + "\n# Synthetic refinement\n"})
    second_id = scripts.save_draft(configured, second)
    assert first.task_id == second.task_id
    assert scripts.load_draft(configured, first_id, first.owner).source == SOURCE
    assert scripts.load_draft(configured, second_id, first.owner).source == second.source
    with Repo(task_files.folder_for(configured, first.task_id)) as repo:
        assert len(list(repo.iter_commits())) == 2
        assert not repo.remotes
    with pytest.raises(FileNotFoundError):
        scripts.save_draft(configured, first.model_copy(update={"owner": "other@fake.invalid"}))


async def test_audit_precedes_calls_and_preserves_failure_without_credentials(
    tmp_path: Path,
) -> None:
    audit = Audit(settings(tmp_path), "fake-audit")

    @observed("synthetic")
    async def operation(arguments: dict[str, Any]) -> None:
        events = [json.loads(line) for line in audit.path.read_text().splitlines()]
        assert events[-1]["event"] == "call"
        raise ValueError("fake-password")

    with recording(audit), pytest.raises(ValueError):
        await operation({"patient": "fake-patient", "authorization": "fake-sensitive",
                         "value": "fake-password"})
    text = audit.path.read_text()
    assert "fake-patient" in text
    assert "fake-sensitive" not in text and "fake-password" not in text
    assert json.loads(text.splitlines()[-1])["event"] == "call_failed"


async def test_failure_to_save_audit_prevents_execution(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    (configured.data_dir / "audits").mkdir(parents=True)
    (configured.data_dir / "audits").rmdir()
    (configured.data_dir / "audits").write_text("not a directory")
    executed = False

    async def operation(run_id: str) -> scripts.Result:
        nonlocal executed
        executed = True
        return scripts.Result(summary="fake", detail={}, coverage="complete")  # type: ignore[arg-type]

    with pytest.raises(OSError):
        await scripts.recorded(configured, draft(), "fake", operation)
    assert not executed


async def test_due_runs_use_local_approved_files_and_do_not_repeat(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch
                                                                ) -> None:
    configured = settings(tmp_path)
    install_fake(tmp_path)
    schedule = schedules.Schedule(name="fake_report", revision=REVISION, inputs={"numbers": [2, 5]})
    schedules.save(configured, schedule, "fake-staff")
    at = datetime(2026, 10, 4, 3, 2, tzinfo=UTC)
    with schedules.open_schedules(configured) as scheduler:
        scheduler.get_job(schedule.id).modify(next_run_time=at - timedelta(minutes=2))

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Runtime execution attempted Git/GitHub access")

    monkeypatch.setattr(task_files, "repository", unavailable)
    monkeypatch.setattr(task_files, "Repo", unavailable)
    assert await schedules.run_due(configured, at) == 0
    assert await schedules.run_due(configured, at) == 0
    store = Storage(configured.database_path)
    try:
        runs = store.recent_runs()
        assert len(runs) == 1
        assert runs[0].outcome is Outcome.SUCCEEDED
        assert runs[0].detail is not None and runs[0].detail["total"] == 7
        audit = configured.data_dir / "audits" / f"{runs[0].run_id}.jsonl"
        assert json.loads(audit.read_text().splitlines()[0])["script"]["revision"] == REVISION
    finally:
        store.close()


async def test_missed_occurrence_is_visible_and_not_executed(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    install_fake(tmp_path)
    schedule = schedules.Schedule(name="fake_report", revision=REVISION, inputs={"numbers": [1]})
    schedules.save(configured, schedule, "fake-staff")
    at = datetime(2026, 10, 4, 3, 10, tzinfo=UTC)
    with schedules.open_schedules(configured) as scheduler:
        scheduler.get_job(schedule.id).modify(next_run_time=at - timedelta(minutes=10))
    assert await schedules.run_due(configured, at) == 0
    store = Storage(configured.database_path)
    try:
        assert [run.outcome for run in store.recent_runs()] == [Outcome.MISSED]
    finally:
        store.close()


def test_invalid_inputs_or_uninstalled_revision_cannot_be_scheduled(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    install_fake(tmp_path)
    with pytest.raises(SchemaError):
        schedules.save(configured, schedules.Schedule(name="fake_report", revision=REVISION,
                       inputs={"numbers": "wrong"}), "fake-staff")
    with pytest.raises(FileNotFoundError):
        schedules.save(configured, schedules.Schedule(name="fake_report", revision="b" * 40),
                       "fake-staff")
    with pytest.raises(ValidationError):
        schedules.Schedule(name="fake_report", revision=REVISION, minutes=7)
    with schedules.open_schedules(configured) as scheduler:
        assert not scheduler.get_jobs()


def test_library_trigger_keeps_local_time_across_dst() -> None:
    trigger = schedules.Schedule(name="fake_report", revision=REVISION,
                                 at="16:00", weekdays="*").trigger()
    before = trigger.get_next_fire_time(None, datetime(2026, 9, 26, tzinfo=UTC))
    after = trigger.get_next_fire_time(before, before + timedelta(seconds=1))
    assert before.hour == after.hour == 16
    assert before.utcoffset() != after.utcoffset()


def test_cleanup_expires_only_inactive_unsubmitted_drafts(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    expired, active, reviewed = draft(), draft(), draft()
    for script in (expired, active, reviewed):
        scripts.save_draft(configured, script)
    old = datetime.now(UTC) - timedelta(days=91)
    for script in (expired, reviewed):
        (task_files.folder_for(configured, script.task_id) / "activity").write_text(old.isoformat())
    (task_files.folder_for(configured, reviewed.task_id) / "review.json").write_text("{}")
    Audit(configured, "retained").write("started", source=SOURCE)
    task_files.cleanup(configured)
    assert not task_files.folder_for(configured, expired.task_id).exists()
    assert task_files.folder_for(configured, active.task_id).exists()
    assert task_files.folder_for(configured, reviewed.task_id).exists()
    assert (configured.data_dir / "audits" / "retained.jsonl").exists()


def test_clean_review_contains_no_draft_history_inputs_or_audits_and_installs_only_after_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings(tmp_path).model_copy(update={"task_repository": "fake/practice"})
    script = draft().model_copy(update={"inputs": {"numbers": [42], "patient": "fake-private"}})
    identifier = scripts.save_draft(configured, script)
    definition = task_files.Definition(name="fake_report", title="Fake report",
                                      description="Synthetic arithmetic", inputs=CONTRACT)
    review = task_files.Review(draft_id=identifier, definition=definition,
                              tests="def test_fake():\n    assert 2 + 5 == 7\n",
                              checked_for_patient_data=True)
    repo = MagicMock()
    repo.default_branch = "main"
    repo.get_branch.return_value.commit.sha = "b" * 40
    repo.create_git_commit.return_value.sha = "c" * 40
    pr = SimpleNamespace(number=7, html_url="https://github.com/fake/practice/pull/7", merged=False,
        merge_commit_sha=REVISION,
        base=SimpleNamespace(repo=SimpleNamespace(full_name="fake/practice")))
    repo.create_pull.return_value = repo.get_pull.return_value = pr
    exported: dict[str, str] = {}

    def capture(tree: list[Any], base: Any) -> Any:
        for element in tree:
            wire = element._identity
            exported[wire["path"]] = wire["content"]
        return SimpleNamespace(sha="d" * 40)

    repo.create_git_tree.side_effect = capture
    monkeypatch.setattr(task_files, "repository", lambda _: repo)
    assert task_files.publish(configured, review, script.owner) == pr.html_url
    assert set(exported) == {"tasks/fake_report/" + name
                             for name in ("task.json", "source.txt", "test_task.py")}
    assert "fake-private" not in "".join(exported.values())
    assert script.revision not in "".join(exported.values())
    assert script.thread not in "".join(exported.values())
    with pytest.raises(ValueError):
        task_files.install(configured, script.task_id, script.owner)
    with pytest.raises(FileNotFoundError):
        task_files.install(configured, script.task_id, "other@fake.invalid")
    pr.merged = True
    repo.get_contents.side_effect = lambda path, ref: SimpleNamespace(
        decoded_content=exported[path].encode())
    assert task_files.install(configured, script.task_id, script.owner) == REVISION
    assert task_files.install(configured, script.task_id, script.owner) == REVISION
    loaded = task_files.load(configured, "fake_report", REVISION, {"numbers": [2, 5]}, script.owner)
    assert loaded.source == SOURCE
    with Repo(task_files.folder_for(configured, script.task_id)) as local:
        assert not local.heads


def test_public_repository_is_refused_before_any_export(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    configured = settings(tmp_path).model_copy(update={"task_repository": "fake/public",
                                                       "github_token": SecretStr("fake-gh")})
    github = MagicMock()
    github.get_repo.return_value.private = False
    monkeypatch.setattr(task_files, "Github", lambda **_: github)
    with pytest.raises(ValueError):
        task_files.repository(configured)


async def test_restart_marks_abandoned_occurrence_uncertain_without_replaying(
    tmp_path: Path,
) -> None:
    configured = settings(tmp_path)
    install_fake(tmp_path)
    schedule = schedules.Schedule(name="fake_report", revision=REVISION, inputs={"numbers": [2]})
    schedules.save(configured, schedule, "fake-staff")
    store = Storage(configured.database_path)
    identifier = store.start_run("fake_report", "scheduler", "fake")
    try:
        await schedules.run_due(configured)
        assert store.run(identifier).outcome is Outcome.UNCERTAIN  # type: ignore[union-attr]
        assert len(store.recent_runs()) == 1
    finally:
        store.close()
