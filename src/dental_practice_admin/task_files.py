"""Local draft Git history and immutable installed task files."""
from __future__ import annotations

import json
import re
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import portalocker
from github import Auth, Github, InputGitTreeElement, UnknownObjectException
from jsonschema import Draft202012Validator, FormatChecker, validate
from pydantic import BaseModel, Field, model_validator

from dental_practice_admin.audit import Audit
from dental_practice_admin.config import Settings

NAME = r"[a-z][a-z0-9_]{0,63}"
IDENTIFIER = r"[0-9a-f]{32}"
# Words a `format: date` input may hold, as a report period does in QuickBooks. Each becomes
# this host's local date only when the task runs, so a schedule saved for "today" runs for the
# day it runs, not the day it was saved. The host's clock is the scheduler's clock too.
RELATIVE_DAYS = {"today": 0, "tomorrow": 1}


class Definition(BaseModel):
    """Portable task contract; runtime values and provenance live outside this file."""

    name: str = Field(pattern="^" + NAME + "$")
    title: str
    description: str
    language: Literal["python", "playwright"] = "python"
    inputs: dict[str, Any]

    @model_validator(mode="after")
    def valid_schema(self) -> Definition:
        """Reject invalid schemas before they can be installed or scheduled."""
        Draft202012Validator.check_schema(self.inputs)
        return self


def folder_for(settings: Settings, identifier: str) -> Path:
    """Draft identifiers cannot address other host files."""
    if not re.fullmatch(IDENTIFIER, identifier):
        raise FileNotFoundError("Draft unavailable")
    return settings.data_dir / "task-drafts" / identifier


def save(settings: Settings, source: str, metadata: dict[str, Any],
         task_id: str) -> tuple[str, str]:
    """Commit a draft locally without a remote, retaining one branch per task."""
    from git import Actor, Repo
    identifier = task_id or uuid.uuid4().hex
    folder = folder_for(settings, identifier)
    folder.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(folder / "edit.lock"), timeout=30):
        if (folder / "installed").exists() or (folder / "expired").exists():
            raise ValueError("Start a new draft to refine an installed or expired task")
        audit = Audit(settings, "draft-check")
        if audit.clean(source) != source:
            raise ValueError("Task source contains a configured credential")
        if (folder / "draft.json").exists():
            previous = json.loads((folder / "draft.json").read_text(encoding="utf-8"))
            if previous["owner"] != metadata["owner"]:
                raise FileNotFoundError("Draft unavailable")
        (folder / "source.txt").write_text(source, encoding="utf-8")
        (folder / "draft.json").write_text(json.dumps(audit.clean(metadata)), encoding="utf-8")
        with Repo.init(folder, initial_branch="draft") as repo:
            repo.index.add(["source.txt", "draft.json"])
            actor = Actor("Practice task editor", "task-editor@localhost")
            commit = repo.index.commit("Save task revision", author=actor, committer=actor)
        (folder / "activity").write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        return identifier, commit.hexsha


def installed(settings: Settings, name: str, revision: str) -> tuple[Definition, str]:
    """Read an immutable local installation without Git or GitHub access."""
    if not re.fullmatch(NAME, name) or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise FileNotFoundError("Installed task unavailable")
    folder = settings.data_dir / "installed" / name / revision
    definition = Definition.model_validate_json((folder / "task.json").read_text(encoding="utf-8"))
    source = (folder / "source.txt").read_text(encoding="utf-8")
    return definition, source


def period_dates(today: date) -> dict[str, str]:
    """Each period word and the date it means on `today`."""
    return {word: (today + timedelta(days=days)).isoformat()
            for word, days in RELATIVE_DAYS.items()}


def checked(inputs: dict[str, Any], schema: dict[str, Any],
            today: date | None = None) -> dict[str, Any]:
    """The inputs with each period word replaced by its date, once they meet the contract.

    `today` is the day a scheduled run was due, which may be before the moment it starts.
    """
    periods = period_dates(today or date.today())
    dates = {key for key, field in schema.get("properties", {}).items()
             if field.get("format") == "date"}
    inputs = {key: periods[value]
              if key in dates and isinstance(value, str) and value in periods else value
              for key, value in inputs.items()}
    validate(inputs, schema, format_checker=FormatChecker())
    return inputs


def load(settings: Settings, name: str, revision: str, inputs: dict[str, Any], owner: str,
         today: date | None = None) -> Any:
    """Build an executable script from a pinned, installed task."""
    from dental_practice_admin.scripts import Script
    definition, source = installed(settings, name, revision)
    inputs = checked(inputs, definition.inputs, today)
    return Script(language=definition.language, source=source, inputs=inputs, owner=owner,
                  thread="", task_id=name, revision=revision)


def cleanup(settings: Settings, at: datetime | None = None) -> None:
    """Expire inactive local drafts; open reviews and execution evidence survive."""
    from git import Repo
    cutoff = (at or datetime.now(UTC)) - timedelta(days=90)
    root = settings.data_dir / "task-drafts"
    for folder in root.glob("*"):
        if (not re.fullmatch(IDENTIFIER, folder.name) or (folder / "review.json").exists()
                or (folder / "expired").exists()):
            continue
        activity = folder / "activity"
        if not activity.is_file() or datetime.fromisoformat(activity.read_text()) >= cutoff:
            continue
        with portalocker.Lock(str(folder / "edit.lock"), timeout=0):
            if datetime.fromisoformat(activity.read_text()) >= cutoff:
                continue
            with Repo(folder) as repo:
                repo.head.reference = repo.head.commit
                repo.delete_head("draft", force=True)
            (folder / "expired").touch()


class Review(BaseModel):
    """Only explicitly reviewed source, schema and synthetic tests can be published."""

    draft_id: str = Field(pattern="^" + IDENTIFIER + "$")
    definition: Definition
    tests: str = Field(min_length=1)
    checked_for_patient_data: Literal[True]


def repository(settings: Settings) -> Any:
    """Publication credentials are used only by explicit review and by installing merged tasks."""
    if not settings.task_repository or not settings.github_token.get_secret_value():
        raise ValueError("Configure the private task repository and GitHub token for review")
    repo = Github(auth=Auth.Token(settings.github_token.get_secret_value())).get_repo(
        settings.task_repository)
    if not repo.private:
        raise ValueError("Practice tasks require a private repository")
    return repo


def publish(settings: Settings, review: Review, owner: str) -> str:
    """Create a clean PR via PyGithub; no local Git objects or execution data leave the host."""
    from dental_practice_admin.scripts import load_draft
    script = load_draft(settings, review.draft_id, owner)
    if not script.reusable or script.language != review.definition.language:
        raise ValueError("Review requires a deterministic task with its matching language")
    files = {"task.json": review.definition.model_dump_json(indent=2),
             "source.txt": script.source, "test_task.py": review.tests}
    audit = Audit(settings, "review-check")
    if any(audit.clean(value) != value for value in files.values()):
        raise ValueError("Review files contain a configured credential")
    folder = folder_for(settings, script.task_id)
    with portalocker.Lock(str(folder / "edit.lock"), timeout=30):
        repo = repository(settings)
        branch = "review/" + script.task_id
        if (folder / "review.json").exists():
            previous = repo.get_pull(json.loads((folder / "review.json").read_text(
                encoding="utf-8"))["number"])
            if previous.merged or previous.state == "open":
                raise ValueError("This script is already waiting for approval or approved; "
                                 "see its pull request")
            # Closed without merging: submitting again opens a new PR from a fresh branch.
            with suppress(UnknownObjectException):
                repo.get_git_ref("heads/" + branch).delete()
        base = repo.get_git_commit(repo.get_branch(repo.default_branch).commit.sha)
        tree = repo.create_git_tree([InputGitTreeElement(f"tasks/{review.definition.name}/{name}",
            "100644", "blob", content=content) for name, content in files.items()], base.tree)
        commit = repo.create_git_commit(f"Review task {review.definition.name}", tree, [base])
        repo.create_git_ref("refs/heads/" + branch, commit.sha)
        pr = repo.create_pull(title=review.definition.title, body=review.definition.description,
                              head=branch, base=repo.default_branch)
        (folder / "review.json").write_text(json.dumps({"number": pr.number, "url": pr.html_url,
            "name": review.definition.name}), encoding="utf-8")
        return str(pr.html_url)


def title(settings: Settings, task: str) -> str:
    """What staff call a run's task: its installed or saved title, else its name in words."""
    if task.startswith("draft:"):
        return "Test run"
    if re.fullmatch(NAME, task):
        with suppress(FileNotFoundError):
            return installed(settings, task, current(settings, task))[0].title
        for path in (settings.data_dir / "saved" / task).glob("*/task.json"):
            return Definition.model_validate_json(path.read_text(encoding="utf-8")).title
    return task.replace("_", " ").capitalize()


def current(settings: Settings, name: str) -> str:
    """The installed revision of a task on the practice repository's default branch."""
    if not re.fullmatch(NAME, name):
        raise FileNotFoundError("Installed task unavailable")
    return (settings.data_dir / "installed" / name / "current").read_text(encoding="utf-8")


def sync(settings: Settings) -> None:
    """Install every task on the practice repository's default branch; a merge is the approval.

    Each task installs at the branch's head commit into its own immutable folder, and `current`
    names it, so a run already under way keeps its files. A task removed from the branch stops
    being offered; its folders stay as execution evidence. Nothing changes unless every task
    reads and validates.
    """
    root = settings.data_dir / "installed"
    repo = repository(settings)
    revision = repo.get_branch(repo.default_branch).commit.sha
    synced = root / "synced"
    if synced.is_file() and synced.read_text(encoding="utf-8") == revision:
        return
    shared = {}
    with suppress(UnknownObjectException):
        shared = {item.name: item.decoded_content
                  for item in repo.get_contents("shared", ref=revision)
                  if item.type == "file" and item.name.endswith(".py")}
    tasks = {}
    for item in repo.get_contents("tasks", ref=revision):
        if item.type != "dir":
            continue
        files = {filename: repo.get_contents(f"tasks/{item.name}/{filename}",
                                             ref=revision).decoded_content
                 for filename in ("task.json", "source.txt", "test_task.py")}
        if Definition.model_validate_json(files["task.json"]).name != item.name:
            raise ValueError(f"tasks/{item.name}/task.json names a different task")
        tasks[item.name] = files
    for name, files in tasks.items():
        target = root / name / revision
        if not target.exists():
            staging = target.with_name("install-" + uuid.uuid4().hex)
            (staging / "shared").mkdir(parents=True)
            for filename, content in files.items():
                (staging / filename).write_bytes(content)
            for filename, content in shared.items():
                (staging / "shared" / filename).write_bytes(content)
            staging.rename(target)
        (root / name / "current").write_text(revision, encoding="utf-8")
    for pointer in root.glob("*/current"):
        if pointer.parent.name not in tasks:
            pointer.unlink()
    synced.write_text(revision, encoding="utf-8")
