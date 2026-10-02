"""Authentication recovery, scoped reads and immutable script promotion behaviour."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from dental_practice_admin.config import Environment, Settings, SignIn
from dental_practice_admin.firestore import Firestore
from dental_practice_admin.scripts import Script, execute, load_draft, released, run, save_draft
from dental_practice_admin.storage import Coverage, Outcome, Storage


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process ownership")
async def test_cancelling_a_draft_stops_its_spawned_children(tmp_path: Path) -> None:
    import win32api
    import win32event

    marker = tmp_path / "child.pid"
    source = '''import asyncio, subprocess, sys
from pathlib import Path
async def run(services, inputs):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])
    Path(inputs['marker']).write_text(str(child.pid))
    await asyncio.sleep(300)
'''
    script = draft().model_copy(update={"source": source, "inputs": {"marker": str(marker)}})
    task = asyncio.create_task(run(settings(tmp_path), script, "fake-process-test"))

    async def child_started() -> None:
        while not marker.exists():
            if task.done():
                await task
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(child_started(), 30)
        handle = win32api.OpenProcess(0x00100000, False, int(marker.read_text()))
        try:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert win32event.WaitForSingleObject(handle, 5000) == win32event.WAIT_OBJECT_0
        finally:
            handle.Close()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_draft_source_and_results_are_private_on_staff_pages(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from dental_practice_admin.app import create_app
    from dental_practice_admin.auth import FAKE_STAFF

    configured = settings(tmp_path).model_copy(update={
        "sign_in": SignIn.DEVELOPER,
        "openai_api_key": SecretStr("fake-ai"),
    })
    own = save_draft(configured, draft().model_copy(update={"owner": FAKE_STAFF}))
    foreign = save_draft(configured, draft())
    store = Storage(configured.database_path)
    identifier = store.start_run("draft:" + foreign, draft().owner, "fake")
    store.finish_run(identifier, Outcome.SUCCEEDED, Coverage.COMPLETE, "fake-private-summary")
    store.close()
    with TestClient(create_app(configured)) as client:
        assert client.get(f"/drafts/{own}").text == SOURCE
        assert client.get(f"/drafts/{foreign}").status_code == 404
        assert client.get(f"/runs/{identifier}").status_code == 404
        assert "fake-private-summary" not in client.get("/").text


def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment=Environment.FAKE,
        data_root=tmp_path,
        ui_email="fake@fake.invalid",
        ui_password=SecretStr("fake-password"),
        firebase_key="fake-key",
        firebase_project="fake-project",
        firestore_root="organisations/fake/brands/fake",
    )


async def test_expired_firestore_token_is_renewed_without_a_browser(tmp_path: Path) -> None:
    tokens: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "identitytoolkit.googleapis.com":
            return httpx.Response(
                200,
                json={"idToken": "fake-first", "refreshToken": "fake-refresh", "expiresIn": "3600"},
            )
        if request.url.host == "securetoken.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "id_token": "fake-renewed",
                    "refresh_token": "fake-refresh",
                    "expires_in": "3600",
                },
            )
        tokens.append(request.headers["authorization"])
        assert "/organisations/fake/brands/fake/" in request.url.path
        return httpx.Response(200, json={"fields": {"name": {"stringValue": "Synthetic"}}})

    client = Firestore(settings(tmp_path), httpx.MockTransport(respond))
    try:
        await client.read("patients/fake")
        client.expires = 0
        await client.read("patients/fake")
    finally:
        await client.aclose()
    assert tokens == ["Bearer fake-first", "Bearer fake-renewed"]


async def test_rejected_refresh_gets_one_signin_and_invalid_credentials_fail(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(400, json={"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})

    client = Firestore(settings(tmp_path), httpx.MockTransport(respond))
    client.refresh_token = "fake-revoked"
    try:
        with pytest.raises(RuntimeError):
            await client.read("patients/fake")
    finally:
        await client.aclose()
    assert calls == ["securetoken.googleapis.com", "identitytoolkit.googleapis.com"]


@pytest.mark.parametrize(
    "path",
    [
        "../other",
        "/other",
        "patients/%2e%2e",
        "patients/x:commit",
        "patients/x?updateMask=x",
        "patients//x",
        "patients\\x",
    ],
)
async def test_firestore_refuses_paths_outside_its_read_interface(
    tmp_path: Path, path: str
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid path reached the network")

    client = Firestore(settings(tmp_path), httpx.MockTransport(unexpected))
    try:
        with pytest.raises(ValueError):
            await client.read(path)
    finally:
        await client.aclose()


SOURCE = """async def run(services, inputs):
    return {"summary": "Synthetic report", "detail": {"total": sum(inputs["numbers"])},
            "coverage": "complete"}
"""


def draft() -> Script:
    return Script(
        language="python",
        source=SOURCE,
        inputs={"numbers": [2, 5]},
        owner="fake@fake.invalid",
        thread="fake-thread",
    )


def test_drafts_are_immutable_and_owner_scoped(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    first = save_draft(configured, draft())
    second = save_draft(configured, draft().model_copy(update={"source": "different"}))
    assert first != second
    assert load_draft(configured, first, draft().owner).source == SOURCE
    with pytest.raises(FileNotFoundError):
        load_draft(configured, first, "another@fake.invalid")
    with pytest.raises(FileNotFoundError):
        load_draft(configured, "../outside", draft().owner)


async def test_draft_and_promoted_source_produce_identical_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings(tmp_path)
    (tmp_path / "report.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "language": "python",
                "source": "report.py",
                "inputs": {
                    "type": "object",
                    "required": ["numbers"],
                    "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("dental_practice_admin.scripts.WORKFLOWS", tmp_path)
    promoted = released("report", draft().inputs, draft().owner)
    assert promoted.source == draft().source
    assert (await execute(configured, promoted)) == (await execute(configured, draft()))
    from dental_practice_admin.tasks import main

    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(draft().inputs), encoding="utf-8")
    monkeypatch.setattr("dental_practice_admin.tasks.Settings", lambda: configured)
    assert await asyncio.to_thread(main, ["run", "report", "--inputs", str(inputs)]) == 0
    store = Storage(configured.database_path)
    try:
        result = store.recent_runs()[0]
        assert result.task == "report"
        assert result.outcome is Outcome.SUCCEEDED
        assert result.coverage is Coverage.COMPLETE
        assert result.detail is not None and result.detail["total"] == 7
    finally:
        store.close()


async def test_cancellation_does_not_leave_a_run_claiming_success(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    source = "import asyncio\nasync def run(services, inputs):\n    await asyncio.sleep(1000)\n"
    execution = asyncio.create_task(
        run(configured, draft().model_copy(update={"source": source}), "test")
    )
    await asyncio.sleep(0.3)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    store = Storage(configured.database_path)
    try:
        assert store.recent_runs()[0].outcome is Outcome.UNCERTAIN
    finally:
        store.close()
