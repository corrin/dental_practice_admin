"""One released OpenAPI interface for chat, scripts and scheduled operations."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import jsonref
from agents import FunctionTool
from agents.tool_context import ToolContext
from fastmcp import FastMCP
from jsonschema import Draft202012Validator, FormatChecker
from openapi_core import OpenAPI
from openapi_core.datatypes import RequestParameters

from dental_practice_admin.audit import observed
from dental_practice_admin.config import PRINCIPLE_WEB_URLS, Environment, Settings
from dental_practice_admin.storage import Storage

SPEC_BYTES = Path(__file__).with_name("principle_openapi.json").read_bytes()
SPEC = json.loads(SPEC_BYTES)
INTERFACE_ID = hashlib.sha256(SPEC_BYTES).hexdigest()
RESOLVED = jsonref.replace_refs(SPEC, lazy_load=False)


def _validator() -> OpenAPI:
    """Response validation against the released document, less one construct it misuses.

    `AllocationTarget` puts a `discriminator` on inline `oneOf` branches with no mapping, so
    openapi-core looks for component schemas named after the type values, finds none, and
    rejects every invoice that has allocations. The `oneOf` alone accepts exactly the valid
    shapes. Reported in docs/principle/api-gaps.md; a release without the discriminator
    makes the `pop` fail at import, which is the cue to delete this.
    """
    spec = json.loads(SPEC_BYTES)
    spec["components"]["schemas"]["AllocationTarget"].pop("discriminator")
    return OpenAPI.from_dict(spec)


VALIDATOR = _validator()


class CallError(ValueError):
    """Arguments or scope do not permit this request."""


class PrincipleError(Exception):
    """An explicit upstream failure without patient data in its message."""

    def __init__(self, status: int, method: str, url: str, body: object) -> None:
        super().__init__(f"{method} {url}: status {status}")
        self.status, self.body = status, body


@dataclass(frozen=True)
class Call:
    """Operation identity and parameters from the released document."""

    name: str
    method: str
    path: str
    parameters: list[dict[str, Any]]

    @property
    def paginated(self) -> bool:
        return any(p["name"] == "offsetId" for p in self.parameters)


CATALOGUE = tuple(
    Call(op["operationId"], method.upper(), path,
         item.get("parameters", []) + op.get("parameters", []))
    for path, item in RESOLVED["paths"].items()
    for method, op in item.items() if method in {"get", "post", "put", "patch", "delete"}
    and not path.startswith(("/v1/oauth", "/v1/webhooks", "/v1/notifications"))
)
BY_NAME = {call.name: call for call in CATALOGUE}

# A transaction listing has one row per invoice a payment was split across, each with the
# payment's `id` and that invoice's share, so `id` alone repeats within a correct walk.
ROW_KEYS = {"listTransactionsByDateRange": ("id", "invoiceId")}


ACCEPTED_WEB_BUILDS = frozenset({"main.ecfbec0077a05029.js", "main.8e7a8bfa2c5bf44c.js"})
WEB_BUILD_CHECK = "principleWebBuild"
_ACCEPTED_ID = ",".join(sorted(ACCEPTED_WEB_BUILDS))


def check_web_build(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> str | None:
    """Warn staff when Principle's web app is a build nobody here has checked.

    A new build can change the website and Firestore behaviour docs/principle/ describes;
    docs/principle/README.md says what to re-check. The warning is recorded under the current
    accepted set, so it lasts until a release accepts the build. An unreadable page is
    recorded separately and clears when the page reads again. Returns the build observed.
    """
    build = None
    try:
        with httpx.Client(transport=transport, timeout=30) as client:
            page = client.get(PRINCIPLE_WEB_URLS[settings.environment]).raise_for_status()
        found = set(re.findall(r"\bmain\.[0-9a-f]+\.js\b", page.text))
        build = found.pop() if len(found) == 1 else None
    except httpx.HTTPError:
        pass
    store = Storage(settings.database_path)
    try:
        if build is None:
            store.interface_warning(WEB_BUILD_CHECK, "unreadable", "web_build_unreadable")
        elif build in ACCEPTED_WEB_BUILDS:
            store.resolve_interface_warning(WEB_BUILD_CHECK, _ACCEPTED_ID)
        else:
            store.interface_warning(WEB_BUILD_CHECK, _ACCEPTED_ID, f"new_web_build {build}")
    finally:
        store.close()
    return build


class PrincipleClient:
    """Shared scope, validation, errors and pagination around FastMCP's HTTP executor."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 60.0) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_base_url, transport=transport, timeout=timeout,
            headers={"X-API-Key": settings.api_key.get_secret_value()},
            event_hooks={"response": [self._response]},
        )
        self.server = FastMCP.from_openapi(SPEC, client=self._client)

    async def __aenter__(self) -> PrincipleClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _incompatible(self, call: Call, reason: str) -> PrincipleError:
        if self.settings.environment is Environment.PRODUCTION:
            store = Storage(self.settings.database_path)
            try:
                store.interface_warning(call.name, INTERFACE_ID, reason)
            finally:
                store.close()
        return PrincipleError(502, call.method, call.path, reason)

    async def _response(self, response: httpx.Response) -> None:
        await response.aread()
        request = response.request
        call = next(c for c in CATALOGUE if c.method == request.method
                    and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", c.path), request.url.path))
        if response.status_code in {404, 405, 410, 422}:
            raise self._incompatible(call, f"http_{response.status_code}")
        if response.status_code >= 400:
            raise PrincipleError(response.status_code, call.method, call.path, "upstream_failure")
        req = SimpleNamespace(
            host_url=str(request.url.copy_with(path="/", query=None)).rstrip("/"),
            path=request.url.path, method=request.method.lower(), body=request.content or None,
            content_type=request.headers.get("content-type", ""),
            parameters=RequestParameters(
                query=dict(request.url.params), header=dict(request.headers)),
        )
        resp = SimpleNamespace(status_code=response.status_code, headers=dict(response.headers),
                               content_type=response.headers.get("content-type", ""),
                               data=response.content)
        try:
            VALIDATOR.validate_response(req, resp)
        except Exception:
            raise self._incompatible(call, "response_schema") from None
        if self.settings.environment is Environment.PRODUCTION:
            store = Storage(self.settings.database_path)
            try:
                store.resolve_interface_warning(call.name, INTERFACE_ID)
            finally:
                store.close()

    @observed("principle")
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute one operation; no model-controlled practice selection reaches the wire."""
        if name not in BY_NAME:
            raise CallError("Unknown or administrative operation")
        call = BY_NAME[name]
        tool = await self.server.get_tool(name)
        if tool is None:
            raise CallError("Operation unavailable")
        parameters = {p["name"] for p in call.parameters}
        values = {k: v for k, v in arguments.items() if v is not None or k not in parameters}
        if set(values) - set(tool.parameters["properties"]):
            raise CallError(f"{name} does not accept these arguments")
        if "practiceId" in tool.parameters["properties"]:
            supplied = values.get("practiceId", self.settings.practice_id)
            if supplied != self.settings.practice_id:
                raise CallError("Practice scope mismatch")
            values["practiceId"] = self.settings.practice_id
        for p in call.parameters:
            if p["in"] == "path" and p["name"] in values:
                value = str(values[p["name"]])
                if value in {"", ".", ".."} or re.search(r"[/\\%?#]", value):
                    raise CallError("Invalid path segment")
        validator = Draft202012Validator(tool.parameters, format_checker=FormatChecker())
        if not validator.is_valid(values):
            raise CallError(f"{name} requires arguments matching its schema")
        patient_id = values.get("patientId")
        if patient_id and name != "getPatient":
            await self.call("getPatient", {"patientId": patient_id})
        try:
            result = await self.server.call_tool(name, values)
        except Exception as error:
            cause: BaseException | None = error
            while cause is not None:
                if isinstance(cause, (PrincipleError, httpx.HTTPError)):
                    raise cause from None
                cause = cause.__cause__ or cause.__context__
            raise self._incompatible(call, "response_schema") from None
        output = result.structured_content
        if not isinstance(output, dict):
            raise self._incompatible(call, "missing_data")
        if name == "getPatient":
            scoped = await self.call("searchPatients", {"name": output["name"]})
            if not any(row["id"] == patient_id for row in scoped["data"]):
                raise CallError("Patient is outside this practice or search is inconclusive")
        return output

    async def get(self, name: str, *, path_params: Mapping[str, Any] | None = None,
                  query: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Read an operation using the same executor offered to chat."""
        return await self.call(name, {**(path_params or {}), **(query or {})})

    async def rows(self, name: str, *, path_params: Mapping[str, Any] | None = None,
                   query: Mapping[str, Any] | None = None, page_size: int = 100,
                   max_pages: int = 1000) -> AsyncIterator[dict[str, Any]]:
        """Walk pages; repeated rows/cursors fail rather than produce inflated totals."""
        call = BY_NAME[name]
        key = ROW_KEYS.get(name, ("id",))
        seen: set[str] = set()
        ids: set[tuple[str, ...]] = set()
        sent = dict(query or {})
        if call.paginated:
            sent["limit"] = page_size
        for _ in range(max_pages):
            envelope = await self.get(name, path_params=path_params, query=sent)
            for row in envelope["data"]:
                identity = tuple(row[field] for field in key)
                if identity in ids:
                    raise self._incompatible(call, "duplicate_row")
                ids.add(identity)
                yield row
            cursor = envelope.get("meta", {}).get("nextOffsetId")
            if not call.paginated or not cursor:
                return
            if cursor in seen:
                raise self._incompatible(call, "repeated_cursor")
            seen.add(cursor)
            sent["offsetId"] = cursor
        raise self._incompatible(call, "paging_incomplete")


async def api_tools(settings: Settings, transport: httpx.AsyncBaseTransport | None
                    ) -> list[FunctionTool]:
    """Adapt FastMCP schemas to the agent while binding trusted practice scope."""
    async with PrincipleClient(settings, transport) as client:
        definitions = await client.server.list_tools()

    def adapt(tool: Any) -> FunctionTool:
        schema = json.loads(json.dumps(tool.parameters))
        schema["properties"].pop("practiceId", None)
        schema["required"] = [p for p in schema.get("required", []) if p != "practiceId"]

        async def invoke(_context: ToolContext[Any], arguments: str) -> str:
            try:
                async with PrincipleClient(settings, transport) as api:
                    output = await api.call(tool.name, json.loads(arguments))
                return json.dumps({"result": output})
            except (CallError, PrincipleError, httpx.HTTPError, ValueError):
                return "Principle operation failed. Inspect saved state before retrying a write."
        return FunctionTool(name=tool.name, description=tool.name,
                            params_json_schema=schema, on_invoke_tool=invoke,
                            strict_json_schema=False)
    return [adapt(tool) for tool in definitions if tool.name in BY_NAME
            and tool.name != "listPractices"]
