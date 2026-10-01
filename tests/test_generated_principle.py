"""Generated interface behaviour, staged-file isolation, and production incompatibility notices."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from agents.tool_context import ToolContext
from pydantic import SecretStr
from scripts.refresh_spec import (
    ARTIFACT,
    POLICY,
    ROOT,
    SNAPSHOT,
    build,
    check,
    check_staged,
    generate,
    rendered,
)

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.principle import (
    BY_NAME,
    CATALOGUE,
    Call,
    PrincipleClient,
    PrincipleError,
)
from dental_practice_admin.principle_tools import api_tools
from dental_practice_admin.storage import Storage


def test_committed_artifact_is_reproducible() -> None:
    assert check(ROOT), "Run: uv run python -m scripts.refresh_spec --generate"


def _context(name: str) -> ToolContext[None]:
    return ToolContext(context=None, tool_name=name, tool_call_id="fake-call", tool_arguments="{}")


def _spec() -> dict[str, Any]:
    return {
        "paths": {
            "/v1/things": {
                "parameters": [{"$ref": "#/components/parameters/scope"}],
                "get": {
                    "operationId": "listThings",
                    "parameters": [
                        {
                            "name": "status",
                            "in": "query",
                            "schema": {"$ref": "#/components/schemas/status"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["data"],
                                        "properties": {
                                            "data": {"type": "array", "items": {"type": "object"}}
                                        },
                                    }
                                }
                            }
                        }
                    },
                },
            }
        },
        "components": {
            "parameters": {
                "scope": {
                    "name": "practiceId",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                }
            },
            "schemas": {"status": {"type": "string", "enum": ["active", "archived"]}},
        },
    }


def test_new_operation_generates_without_a_wrapper() -> None:
    snapshot = build(yaml.safe_dump(_spec()))
    compiled = generate(snapshot, {"patches": [], "notes": {}, "complete_listings": []})
    op = compiled["operations"]["listThings"]
    assert op["exposed"]
    assert "practiceId" not in op["tool_schema"]["properties"]
    assert op["tool_schema"]["properties"]["status"] == {
        "anyOf": [{"type": "string", "enum": ["active", "archived"]}, {"type": "null"}]
    }


def test_unknown_schema_constraints_fail_generation() -> None:
    spec = _spec()
    spec["components"]["schemas"]["status"]["unknownConstraint"] = True
    with pytest.raises(ValueError):
        build(yaml.safe_dump(spec))


def test_write_and_unscoped_operations_are_not_generated() -> None:
    spec = _spec()
    spec["paths"]["/v1/things"]["post"] = copy.deepcopy(spec["paths"]["/v1/things"]["get"])
    spec["paths"]["/v1/patients/{patientId}"] = {
        "get": {"operationId": "getPatient", "parameters": []}
    }
    assert set(build(yaml.safe_dump(spec))["operations"]) == {"listThings"}


def test_live_comparison_ignores_unrelated_new_operations() -> None:
    spec = _spec()
    expected = build(yaml.safe_dump(spec))
    spec["paths"]["/v1/new-resource"] = {
        "get": {"operationId": "newResource", "parameters": [{"in": "query", "name": "practiceId"}]}
    }
    assert build(yaml.safe_dump(spec), {"listThings"}) == expected


def test_compatibility_patch_applies_only_to_its_expected_input() -> None:
    snapshot = build(yaml.safe_dump(_spec()))
    policy = {
        "patches": [
            {
                "operation": "listThings",
                "path": ["response", "required"],
                "expected": ["data"],
                "value": [],
            }
        ],
        "notes": {},
        "complete_listings": [],
    }
    artifact = generate(snapshot, policy)
    assert artifact["operations"]["listThings"]["response"]["required"] == []
    assert snapshot["operations"]["listThings"]["response"]["required"] == ["data"]


def test_obsolete_compatibility_patch_requires_review() -> None:
    policy = {
        "patches": [
            {"operation": "listThings", "path": ["method"], "expected": "POST", "value": "GET"}
        ],
        "notes": {},
        "complete_listings": [],
    }
    with pytest.raises(ValueError):
        generate(build(yaml.safe_dump(_spec())), policy)


def _index_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for relative in (SNAPSHOT, POLICY, ARTIFACT, Path("scripts/refresh_spec.py")):
        destination = path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)


def test_staged_check_ignores_unstaged_generator_and_artifact_edits(tmp_path: Path) -> None:
    _index_repo(tmp_path)
    (tmp_path / ARTIFACT).write_text("invalid working tree", encoding="utf-8")
    (tmp_path / "scripts/refresh_spec.py").write_text("raise RuntimeError()", encoding="utf-8")
    assert check_staged(tmp_path) == 0
    assert (tmp_path / ARTIFACT).read_text() == "invalid working tree"


def test_staged_stale_artifact_fails_even_with_regenerated_working_tree(tmp_path: Path) -> None:
    _index_repo(tmp_path)
    snapshot = json.loads((tmp_path / SNAPSHOT).read_text())
    snapshot["operations"]["searchPatients"]["parameters"][0]["schema"]["maxLength"] = 50
    (tmp_path / SNAPSHOT).write_text(rendered(snapshot), encoding="utf-8")
    subprocess.run(["git", "add", SNAPSHOT.as_posix()], cwd=tmp_path, check=True)
    policy = json.loads((tmp_path / POLICY).read_text())
    (tmp_path / ARTIFACT).write_text(rendered(generate(snapshot, policy)), encoding="utf-8")
    assert check(tmp_path)
    assert check_staged(tmp_path) == 1


def test_hand_edited_generated_code_fails(tmp_path: Path) -> None:
    _index_repo(tmp_path)
    artifact = json.loads((tmp_path / ARTIFACT).read_text())
    artifact["operations"]["searchPatients"]["path"] = "/v1/other"
    (tmp_path / ARTIFACT).write_text(rendered(artifact), encoding="utf-8")
    assert not check(tmp_path)


def test_staged_legacy_checker_cannot_run_its_online_check(tmp_path: Path) -> None:
    _index_repo(tmp_path)
    marker = tmp_path / "online-check-ran"
    (tmp_path / "scripts/refresh_spec.py").write_text(
        "import argparse\nfrom pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--check', action='store_true')\n"
        "parser.parse_args()\n"
        f"Path({str(marker)!r}).write_text('called')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "scripts/refresh_spec.py"], cwd=tmp_path, check=True)
    assert check_staged(tmp_path) != 0
    assert not marker.exists()


async def test_generated_search_is_scoped_and_never_claims_a_patient_total(
    fake_settings: Settings,
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    tools = {t.name: t for t in api_tools(fake_settings, httpx.MockTransport(respond))}
    result = json.loads(
        await tools["searchPatients"].on_invoke_tool(
            _context("searchPatients"),
            json.dumps({"name": "fake-patient", "dateOfBirth": None, "phoneNumber": None}),
        )
    )
    assert result["coverage"] == "partial"
    assert result["result"] == {"data": []}
    assert requests[0].url.params["practiceId"] == fake_settings.practice_id
    assert requests[0].url.params["name"] == "fake-patient"
    assert "dateOfBirth" not in requests[0].url.params
    assert "listPractices" not in tools


@pytest.mark.parametrize(
    "arguments",
    [
        {"practiceId": "another-practice"},
        {"url": "https://example.com"},
        {"name": 42, "dateOfBirth": None, "phoneNumber": None},
        {"name": None, "dateOfBirth": "not-a-date", "phoneNumber": None},
    ],
)
async def test_invalid_tool_arguments_cannot_reach_principle(
    fake_settings: Settings,
    arguments: dict[str, Any],
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    tool = next(
        t
        for t in api_tools(fake_settings, httpx.MockTransport(respond))
        if t.name == "searchPatients"
    )
    await tool.on_invoke_tool(_context(tool.name), json.dumps(arguments))
    assert not requests


def _production(tmp_path: Path) -> Settings:
    return Settings(
        environment=Environment.PRODUCTION,
        api_key=SecretStr("fake-key"),
        practice_id="fake-practice",
        data_root=tmp_path,
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"data": "private patient data"}),
        httpx.Response(200, text="not JSON"),
        httpx.Response(405),
    ],
)
async def test_production_incompatibility_is_persistent_and_contains_no_record_values(
    tmp_path: Path,
    response: httpx.Response,
) -> None:
    settings = _production(tmp_path)
    async with PrincipleClient(
        settings, transport=httpx.MockTransport(lambda _: response)
    ) as client:
        for _ in range(2):
            with pytest.raises(PrincipleError):
                await client.get("searchPatients", query={"practiceId": "fake-practice"})
    store = Storage(settings.database_path)
    try:
        warnings = store.interface_warnings()
        assert len(warnings) == 1
        assert warnings[0]["operation"] == "searchPatients"
        assert "private patient data" not in json.dumps(warnings)
    finally:
        store.close()


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_access_and_service_failures_do_not_claim_an_interface_change(
    tmp_path: Path,
    status: int,
) -> None:
    settings = _production(tmp_path)
    async with PrincipleClient(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"message": "fake failure"})
        ),
    ) as client:
        with pytest.raises(PrincipleError):
            await client.get("searchPatients", query={"practiceId": "fake-practice"})
    store = Storage(settings.database_path)
    try:
        assert not store.interface_warnings()
    finally:
        store.close()


async def test_extra_response_fields_do_not_trigger_an_incompatibility(tmp_path: Path) -> None:
    settings = _production(tmp_path)
    async with PrincipleClient(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [], "newMetadata": "fake-extra"})
        ),
    ) as client:
        result = await client.get("searchPatients", query={"practiceId": "fake-practice"})
    assert result["data"] == []
    store = Storage(settings.database_path)
    try:
        assert not store.interface_warnings()
    finally:
        store.close()


@pytest.mark.parametrize("segment", ["..", "../another-practice", "x/y", "%2e%2e", "x?y=z"])
async def test_path_parameters_cannot_escape_the_generated_route(
    fake_settings: Settings,
    segment: str,
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": {}})

    tool = next(
        t
        for t in api_tools(fake_settings, httpx.MockTransport(respond))
        if t.name == "getPractitioner"
    )
    await tool.on_invoke_tool(_context(tool.name), json.dumps({"practitionerId": segment}))
    assert not requests


def test_warning_only_resolves_after_a_different_released_interface(tmp_path: Path) -> None:
    store = Storage(tmp_path / "warnings.db")
    try:
        store.interface_warning("searchPatients", "release-one", "response_schema")
        store.resolve_interface_warning("searchPatients", "release-one")
        assert store.interface_warnings()
        store.resolve_interface_warning("searchPatients", "release-two")
        assert not store.interface_warnings()
    finally:
        store.close()


def test_all_exposed_operations_have_scope_and_only_declared_path_parameters() -> None:
    for call in CATALOGUE:
        if not call.definition["exposed"]:
            continue
        assert call.method == "GET"
        assert "practiceId" in call.definition["request_schema"]["properties"]
        assert "practiceId" not in call.definition["tool_schema"]["properties"]
        assert "patientId" not in call.definition["request_schema"]["properties"]
    assert BY_NAME["searchPatients"].definition["complete_listing"] is False


async def test_generated_executor_accepts_a_new_operation_and_new_optional_argument(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: Settings,
) -> None:
    spec = _spec()
    spec["paths"]["/v1/things"]["get"]["parameters"].append(
        {"name": "count", "in": "query", "schema": {"type": "integer", "minimum": 1}}
    )
    spec["paths"]["/v1/things"]["get"]["parameters"].extend(
        [
            {"name": "active", "in": "query", "schema": {"type": "boolean"}},
            {"name": "thingId", "in": "path", "required": True, "schema": {"type": "integer"}},
        ]
    )
    spec["paths"]["/v1/things/{thingId}"] = spec["paths"].pop("/v1/things")
    compiled = generate(
        build(yaml.safe_dump(spec)), {"patches": [], "notes": {}, "complete_listings": []}
    )
    call = Call.from_definition(compiled["operations"]["listThings"])
    monkeypatch.setattr("dental_practice_admin.principle_tools.CATALOGUE", (call,))
    monkeypatch.setattr("dental_practice_admin.principle.BY_NAME", {call.name: call})
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "fake-thing"}]})

    tool = api_tools(fake_settings, httpx.MockTransport(respond))[0]
    result = json.loads(
        await tool.on_invoke_tool(
            _context(tool.name),
            json.dumps({"status": "active", "count": 2, "active": True, "thingId": 7}),
        )
    )
    assert result["result"]["data"] == [{"id": "fake-thing"}]
    assert requests[0].url.path == "/v1/things/7"
    assert dict(requests[0].url.params) == {
        "practiceId": fake_settings.practice_id,
        "status": "active",
        "count": "2",
        "active": "true",
    }
    await tool.on_invoke_tool(
        _context(tool.name),
        json.dumps({"status": None, "count": None, "active": None, "thingId": 0}),
    )
    assert requests[1].url.path == "/v1/things/0"
    assert dict(requests[1].url.params) == {"practiceId": fake_settings.practice_id}
