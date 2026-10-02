"""Practice boundaries, response contracts and persistent incompatibility notices."""
import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from agents.tool_context import ToolContext
from pydantic import SecretStr

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.principle import CallError, PrincipleClient, PrincipleError, api_tools
from dental_practice_admin.storage import Storage


def _context(name: str) -> ToolContext[None]:
    return ToolContext(context=None, tool_name=name, tool_call_id="fake-call", tool_arguments="{}")


@pytest.mark.parametrize("in_scope", [True, False])
async def test_patient_without_practice_field_requires_scoped_id_match(
    fake_settings: Settings, in_scope: bool,
) -> None:
    patient = {"id": "fake-patient", "name": "Fake Dummy", "email": "fake@example.invalid",
               "gender": "notSpecified", "address": "Fake address", "dateOfBirth": "2000-01-01"}
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/fake-patient"):
            return httpx.Response(200, json=patient)
        assert request.url.params["practiceId"] == fake_settings.practice_id
        assert request.url.params["name"] == patient["name"]
        return httpx.Response(200, json={"data": [patient] if in_scope else []})

    async with PrincipleClient(fake_settings, transport=httpx.MockTransport(respond)) as client:
        if in_scope:
            assert await client.call("getPatient", {"patientId": patient["id"]}) == patient
        else:
            with pytest.raises(CallError, match="outside"):
                await client.call("updatePatient", {
                    **{k: v for k, v in patient.items() if k != "id"},
                    "patientId": patient["id"], "address": "Fake edit"})
    assert all(request.method == "GET" for request in requests)

async def test_generated_search_is_scoped_and_never_claims_a_patient_total(
    fake_settings: Settings,
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    tools = {t.name: t for t in await api_tools(fake_settings, httpx.MockTransport(respond))}
    result = json.loads(
        await tools["searchPatients"].on_invoke_tool(
            _context("searchPatients"),
            json.dumps({"name": "fake-patient", "dateOfBirth": None, "phoneNumber": None}),
        )
    )
    assert result == {"result": {"data": []}}
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
        for t in await api_tools(fake_settings, httpx.MockTransport(respond))
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
        for t in await api_tools(fake_settings, httpx.MockTransport(respond))
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

