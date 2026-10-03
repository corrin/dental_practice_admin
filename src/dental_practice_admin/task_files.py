"""Local draft Git history and immutable installed task files."""
from __future__ import annotations

import json
import re
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
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


def load(settings: Settings, name: str, revision: str, inputs: dict[str, Any], owner: str) -> Any:
    """Build an executable script from a pinned, installed task."""
    from dental_practice_admin.scripts import Script
    definition, source = installed(settings, name, revision)
    validate(inputs, definition.inputs, format_checker=FormatChecker())
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
    """Publication credentials are used only by explicit review and installation actions."""
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
        if (folder / "review.json").exists():
            raise ValueError("This draft already has a review; check its existing PR")
        repo = repository(settings)
        base = repo.get_git_commit(repo.get_branch(repo.default_branch).commit.sha)
        tree = repo.create_git_tree([InputGitTreeElement(f"tasks/{review.definition.name}/{name}",
            "100644", "blob", content=content) for name, content in files.items()], base.tree)
        commit = repo.create_git_commit(f"Review task {review.definition.name}", tree, [base])
        branch = "review/" + script.task_id
        repo.create_git_ref("refs/heads/" + branch, commit.sha)
        pr = repo.create_pull(title=review.definition.title, body=review.definition.description,
                              head=branch, base=repo.default_branch)
        (folder / "review.json").write_text(json.dumps({"number": pr.number, "url": pr.html_url,
            "name": review.definition.name}), encoding="utf-8")
        return str(pr.html_url)


def install(settings: Settings, task_id: str, owner: str) -> str:
    """Install only the merged PR's files, then retire its local development branch."""
    from git import Repo
    folder = folder_for(settings, task_id)
    metadata = json.loads((folder / "draft.json").read_text(encoding="utf-8"))
    if metadata["owner"] != owner:
        raise FileNotFoundError("Draft unavailable")
    review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    repo = repository(settings)
    pr = repo.get_pull(review["number"])
    if not pr.merged or pr.base.repo.full_name != settings.task_repository:
        raise ValueError("Task PR has not been merged into the configured repository")
    revision = pr.merge_commit_sha
    target = settings.data_dir / "installed" / review["name"] / revision
    files = {name: repo.get_contents(f"tasks/{review['name']}/{name}", ref=revision).decoded_content
             for name in ("task.json", "source.txt", "test_task.py")}
    definition = Definition.model_validate_json(files["task.json"])
    if definition.name != review["name"]:
        raise ValueError("Merged task name disagrees with its installation path")
    if not target.exists():
        staging = target.with_name("install-" + uuid.uuid4().hex)
        staging.mkdir(parents=True)
        for name, content in files.items():
            (staging / name).write_bytes(content)
        staging.rename(target)
    (folder / "installed").write_text(revision, encoding="utf-8")
    with Repo(folder) as local:
        local.head.reference = local.head.commit
        if "draft" in local.heads:
            local.delete_head("draft", force=True)
    with suppress(UnknownObjectException):
        repo.get_git_ref("heads/review/" + task_id).delete()
    return str(revision)
