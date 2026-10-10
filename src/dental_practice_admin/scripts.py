"""Immutable chat drafts and reviewed tasks run through the same supervised worker."""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
import uuid
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from typing import Any, Literal

import portalocker
from pydantic import BaseModel, SecretStr

from dental_practice_admin import browser
from dental_practice_admin.audit import Audit, recording
from dental_practice_admin.config import Settings
from dental_practice_admin.firestore import Firestore
from dental_practice_admin.principle import PrincipleClient
from dental_practice_admin.processes import own_process_tree
from dental_practice_admin.storage import Coverage, Outcome, Storage


class Script(BaseModel):
    """Source and explicit inputs; runtime drafts also carry their conversation owner."""

    language: Literal["python", "playwright"]
    source: str
    inputs: dict[str, Any]
    owner: str
    thread: str
    task_id: str = ""
    revision: str = ""
    reusable: bool = True


class Result(BaseModel):
    """Only a verified complete result may claim complete coverage."""

    summary: str
    detail: dict[str, Any]
    coverage: Coverage


def save_draft(settings: Settings, script: Script) -> str:
    """Create a new immutable version; identifiers never contain caller-controlled paths."""
    from dental_practice_admin.task_files import cleanup, save
    cleanup(settings)
    script.task_id, script.revision = save(settings, script.source,
        script.model_dump(exclude={"source", "revision", "inputs"}), script.task_id)
    identifier = uuid.uuid4().hex
    folder = settings.data_dir / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{identifier}.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(Audit(settings, identifier).clean(script.model_dump())))
    return identifier


def load_draft(settings: Settings, identifier: str, owner: str) -> Script:
    """Refuse missing and foreign drafts without disclosing their contents."""
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise FileNotFoundError("Draft unavailable")
    script = Script.model_validate_json(
        (settings.data_dir / "drafts" / f"{identifier}.json").read_text(encoding="utf-8"))
    if script.owner != owner:
        raise FileNotFoundError("Draft unavailable")
    return script


class Services:
    """The same integrations for exploratory and released Python scripts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api = PrincipleClient(settings)
        self.firestore = Firestore(settings)

    async def browser(self, source: str, inputs: dict[str, Any]) -> Any:
        """Run a deterministic Playwright function with exclusive browser ownership."""
        async with browser.session(self.settings) as server:
            return await browser.code(server, source, inputs)

    async def aclose(self) -> None:
        await self.api.aclose()
        await self.firestore.aclose()


async def execute(settings: Settings, script: Script) -> Result:
    """Execute source without involving a model."""
    services = Services(settings)
    try:
        if script.language == "playwright":
            return Result.model_validate(await services.browser(script.source, script.inputs))
        # Installed tasks import the practice repository's shared/ modules; drafts have none.
        # Each run is a fresh worker process, so sys.path and sys.modules never leak between runs.
        shared = str(settings.data_dir / "installed" / script.task_id / script.revision / "shared")
        sys.path.insert(0, shared)
        try:
            namespace: dict[str, Any] = {}
            exec(compile(script.source, "<workflow>", "exec"), namespace)
            return Result.model_validate(await namespace["run"](services, script.inputs))
        finally:
            sys.path.remove(shared)
    finally:
        await services.aclose()


async def run(settings: Settings, script: Script, task: str) -> str:
    """Record each attempt and stop the complete worker tree on cancellation."""
    return await recorded(settings, script, task,
                          lambda run_id: worker_run(settings, script, run_id))


def execution_lock(settings: Settings, script: Script) -> portalocker.Lock:
    """The executing process holds this lock, including if its launcher disappears."""
    name = uuid.uuid5(uuid.NAMESPACE_URL, script.task_id or script.source).hex
    folder = settings.data_dir / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    return portalocker.Lock(str(folder / (name + ".lock")), timeout=0)


async def recorded(settings: Settings, script: Script, task: str,
                   operation: Callable[[str], Awaitable[Result]], run_id: str | None = None) -> str:
    """File evidence precedes execution; SQLite indexes the same run for staff pages."""
    identifier = run_id or uuid.uuid4().hex
    audit = Audit(settings, identifier)
    storage = Storage(settings.database_path)
    run_id = run_id or storage.start_run(task, script.owner, settings.environment.value, identifier)
    try:
        audit.write("started", run_id=run_id, task=task, script=script.model_dump(),
                    application=version("dental-practice-admin"), environment=settings.environment)
        with recording(audit):
            result = await operation(run_id)
        audit.write("finished", result=result.model_dump())
        storage.finish_run(run_id, Outcome.SUCCEEDED, result.coverage, result.summary,
                           {**result.detail, "inputs": script.inputs, "revision": script.revision})
        return run_id
    except BaseException as error:
        storage.finish_run(run_id, Outcome.UNCERTAIN, Coverage.PARTIAL,
                           "Execution incomplete; inspect the audit before retrying")
        audit.write("interrupted", error=type(error).__name__)
        raise
    finally:
        storage.close()


async def worker_run(settings: Settings, script: Script, run_id: str) -> Result:
    """Execute trusted source in a supervised subprocess with its own audit context."""
    worker: asyncio.subprocess.Process | None = None
    try:
        values = settings.model_dump(mode="json")
        for name in Settings.model_fields:
            value = getattr(settings, name)
            if isinstance(value, SecretStr):
                values[name] = value.get_secret_value()
        payload = json.dumps({"settings": values, "script": script.model_dump(mode="json"),
                              "run_id": run_id})
        worker = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "dental_practice_admin.scripts", "--worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, _stderr = await worker.communicate(payload.encode())
        if worker.returncode:
            raise RuntimeError(f"Script failed ({stdout.decode().strip()}); inspect saved state")
        return Result.model_validate_json(stdout)
    finally:
        if worker is not None and worker.returncode is None:
            worker.kill()
            await worker.communicate()


def main() -> int:
    """Worker protocol uses stdin; credentials never appear in command-line arguments."""
    own_process_tree()
    payload = json.load(sys.stdin)
    settings = Settings(**{"_env_file": None, **payload["settings"]})
    settings.require_credentials()
    script = Script.model_validate(payload["script"])
    try:
        with (contextlib.redirect_stdout(sys.stderr),
              recording(Audit(settings, payload["run_id"])), execution_lock(settings, script)):
            result = asyncio.run(execute(settings, script))
    except Exception as error:
        print(type(error).__name__)
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
