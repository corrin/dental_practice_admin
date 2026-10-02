"""Immutable chat drafts and reviewed tasks run through the same supervised worker."""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

from jsonschema import FormatChecker, validate
from pydantic import BaseModel, SecretStr

from dental_practice_admin import browser
from dental_practice_admin.config import Settings
from dental_practice_admin.firestore import Firestore
from dental_practice_admin.principle import PrincipleClient
from dental_practice_admin.processes import own_process_tree
from dental_practice_admin.storage import Coverage, Outcome, Storage

WORKFLOWS = Path(__file__).with_name("workflows")


class Script(BaseModel):
    """Source and explicit inputs; runtime drafts also carry their conversation owner."""

    language: Literal["python", "playwright"]
    source: str
    inputs: dict[str, Any]
    owner: str
    thread: str


class Result(BaseModel):
    """Only a verified complete result may claim complete coverage."""

    summary: str
    detail: dict[str, Any]
    coverage: Coverage


def save_draft(settings: Settings, script: Script) -> str:
    """Create a new immutable version; identifiers never contain caller-controlled paths."""
    identifier = uuid.uuid4().hex
    folder = settings.data_dir / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{identifier}.json").open("x", encoding="utf-8") as output:
        output.write(script.model_dump_json())
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


def released(name: str, inputs: dict[str, Any], initiator: str) -> Script:
    """Load a reviewed source file and validate its inputs against the released contract."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Invalid task name")
    definition = json.loads((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))
    validate(inputs, definition["inputs"], format_checker=FormatChecker())
    return Script(language=definition["language"],
                  source=(WORKFLOWS / definition["source"]).read_text(encoding="utf-8"),
                  inputs=inputs, owner=initiator, thread="")


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
        namespace: dict[str, Any] = {}
        exec(compile(script.source, "<workflow>", "exec"), namespace)
        return Result.model_validate(await namespace["run"](services, script.inputs))
    finally:
        await services.aclose()


async def run(settings: Settings, script: Script, task: str) -> str:
    """Record each attempt and stop the complete worker tree on cancellation."""
    storage = Storage(settings.database_path)
    run_id = storage.start_run(task, script.owner, settings.environment.value)
    worker: asyncio.subprocess.Process | None = None
    try:
        values = settings.model_dump(mode="json")
        for name in Settings.model_fields:
            value = getattr(settings, name)
            if isinstance(value, SecretStr):
                values[name] = value.get_secret_value()
        payload = json.dumps({"settings": values, "script": script.model_dump(mode="json")})
        worker = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "dental_practice_admin.scripts", "--worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, _stderr = await worker.communicate(payload.encode())
        if worker.returncode:
            raise RuntimeError("Script failed; inspect saved state before repeating a change")
        result = Result.model_validate_json(stdout)
        storage.finish_run(run_id, Outcome.SUCCEEDED, result.coverage, result.summary,
                           {**result.detail, "inputs": script.inputs})
        return run_id
    except BaseException:
        storage.finish_run(run_id, Outcome.UNCERTAIN, Coverage.PARTIAL,
                           "Script interrupted or failed; completion has not been established")
        raise
    finally:
        if worker is not None and worker.returncode is None:
            worker.kill()
            await worker.wait()
        storage.close()


def main() -> int:
    """Worker protocol uses stdin; credentials never appear in command-line arguments."""
    own_process_tree()
    payload = json.load(sys.stdin)
    settings = Settings(**{"_env_file": None, **payload["settings"]})
    settings.require_credentials()
    script = Script.model_validate(payload["script"])
    with contextlib.redirect_stdout(sys.stderr):
        result = asyncio.run(execute(settings, script))
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
