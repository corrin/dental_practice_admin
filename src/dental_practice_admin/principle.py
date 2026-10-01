"""The documented Principle API, reached through a generated, released catalogue.

Regenerate definitions with scripts.refresh_spec; the shared executor owns transport,
schema validation, pagination and production incompatibility observations.

Auth is the `X-API-Key` header (verified in SMS_Bridge/Services/PrincipleApiClient.cs and
od_data/recon_lib.py). Listings return `{"data": [...], "meta": {...}}` and page by
`offsetId`/`nextOffsetId`; `/v1/practices` returns `data` alone and does not page.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from dental_practice_admin.config import Environment, Settings
from dental_practice_admin.storage import Storage


class PrincipleError(Exception):
    """Principle answered, but not with success."""

    def __init__(self, status: int, method: str, url: str, body: object) -> None:
        super().__init__(f"{method} {url} -> {status}: {body!r}")
        self.status = status
        self.body = body


class CallError(Exception):
    """The application asked for something the catalogue does not describe."""


@dataclass(frozen=True)
class Call:
    """One documented operation, with the parameters it is allowed to carry.

    `query` and `required_query` are enforced on the way out: a parameter that is not
    declared is a `CallError` rather than a silently dropped filter, because a dropped
    filter returns a plausible wrong answer instead of failing.
    """

    name: str
    method: str
    path: str
    query: frozenset[str] = field(default_factory=frozenset)
    required_query: frozenset[str] = field(default_factory=frozenset)
    paginated: bool = False
    definition: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_definition(cls, op: dict[str, Any]) -> Call:
        """Load one generated operation without an endpoint-specific wrapper."""
        return cls(
            name=op["name"],
            method=op["method"],
            path=op["path"],
            query=frozenset(p["name"] for p in op["parameters"] if p["in"] == "query"),
            required_query=frozenset(
                p["name"] for p in op["parameters"] if p["in"] == "query" and p["required"]
            ),
            paginated=op["paginated"],
            definition=op,
        )

    def url_path(self, path_params: Mapping[str, str]) -> str:
        """Fill the path template, refusing a missing or unexpected segment."""
        if set(path_params) != set(re.findall(r"\{([^}]+)\}", self.path)):
            raise CallError(f"{self.name} requires its declared path parameters")
        if any(
            not value or value in {".", ".."} or re.search(r"[/\\%?#]", value)
            for value in path_params.values()
        ):
            raise CallError("Invalid path segment")
        try:
            return self.path.format(**path_params)
        except KeyError as missing:
            raise CallError(f"{self.name} needs path parameter {missing}") from missing


_ARTIFACT = Path(__file__).with_name("generated_principle.json").read_bytes()
INTERFACE_ID = hashlib.sha256(_ARTIFACT).hexdigest()
CATALOGUE: tuple[Call, ...] = tuple(
    Call.from_definition(op) for op in json.loads(_ARTIFACT)["operations"].values()
)

BY_NAME: Mapping[str, Call] = {call.name: call for call in CATALOGUE}


class PrincipleClient:
    """Issues catalogue calls against whichever Principle the settings name.

    The transport is injectable so the fake substitutes only the bottom inch: auth,
    pagination, error mapping and timeouts are the same code on fake, staging and
    production, which is what makes a green fake run mean anything at all.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            headers={
                "X-API-Key": settings.api_key.get_secret_value(),
                "Accept": "application/json",
            },
            transport=transport,
            timeout=timeout,
        )

    async def __aenter__(self) -> PrincipleClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _prepare(
        self, name: str, path_params: Mapping[str, Any], query: Mapping[str, Any]
    ) -> tuple[Call, str, dict[str, str]]:
        call = BY_NAME.get(name)
        if call is None:
            raise CallError(f"no call named {name!r} in the released interface")
        undeclared = set(query) - call.query
        if undeclared:
            raise CallError(f"{name} does not accept {sorted(undeclared)}")
        sent = dict(
            httpx.QueryParams({key: value for key, value in query.items() if value is not None})
        )
        missing = call.required_query - set(sent)
        if missing:
            raise CallError(f"{name} requires {sorted(missing)}")
        values = {**path_params, **{k: v for k, v in query.items() if v is not None}}
        validator = Draft202012Validator(
            call.definition["request_schema"], format_checker=FormatChecker()
        )
        if not validator.is_valid(values):
            raise CallError(f"{name} requires arguments matching its generated schema")
        return call, call.url_path(dict(httpx.QueryParams(path_params))), sent

    def _incompatible(self, call: Call, reason: str) -> PrincipleError:
        """Persist only operation names and reason codes, never response bodies or arguments."""
        if self.settings.environment is Environment.PRODUCTION:
            store = Storage(self.settings.database_path)
            try:
                store.interface_warning(call.name, INTERFACE_ID, reason)
            finally:
                store.close()
        return PrincipleError(502, call.method, call.path, reason)

    async def get(
        self,
        name: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One catalogue call, returning the decoded envelope.

        `query` is a mapping rather than keyword arguments because `from` is a Python
        keyword and the spec names it, and because a keyword form lets a caller's typo land
        on `page_size` instead of failing.
        """
        call, path, sent = self._prepare(name, path_params or {}, query or {})
        response = await self._client.request(call.method, path, params=sent)
        if response.status_code in {404, 405, 410, 422}:
            raise self._incompatible(call, f"http_{response.status_code}")
        if response.status_code >= 400:
            raise PrincipleError(
                response.status_code, call.method, str(response.url), _body_of(response)
            )
        try:
            decoded = response.json()
        except ValueError:
            raise self._incompatible(call, "invalid_json") from None
        if not Draft202012Validator(call.definition["response"]).is_valid(decoded):
            raise self._incompatible(call, "response_schema")
        if not isinstance(decoded, dict) or "data" not in decoded:
            raise self._incompatible(call, "missing_data")
        if self.settings.environment is Environment.PRODUCTION:
            store = Storage(self.settings.database_path)
            try:
                store.resolve_interface_warning(call.name, INTERFACE_ID)
            finally:
                store.close()
        return decoded

    async def rows(
        self,
        name: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int = 1000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Every row of a listing, following `meta.nextOffsetId` to the end.

        Two guards required by the live API's cursor behaviour:

        A repeated cursor fails the walk. od_data hit non-advancing offsets here, and the
        spec's own PaginationMeta example has nextOffsetId equal to offsetId, so a client
        trusting the cursor to advance loops forever.

        A repeated row id raises. `nextOffsetId` is a `createdAt`, not a record id, and a
        cursor the server cannot place is ignored rather than refused -- so a stale cursor
        mid-walk silently restarts from page one. Yielding those rows again would inflate
        every count in the report, and a wrong number filed as a result is worse than a
        failure.

        `meta.total` is the current page's row count, not the size
        of the result set.
        """
        call = BY_NAME.get(name)
        if call is None:
            raise CallError(f"no call named {name!r} in the released interface")
        if not call.paginated:
            envelope = await self.get(name, path_params=path_params, query=query)
            for row in envelope.get("data") or []:
                yield row
            return

        seen: set[str] = set()
        yielded: set[str] = set()
        offset: str | None = None
        for _ in range(max_pages):
            page_query = dict(query or {}, limit=page_size)
            if offset is not None:
                page_query["offsetId"] = offset
            envelope = await self.get(name, path_params=path_params, query=page_query)
            for row in envelope.get("data") or []:
                identifier = row.get("id")
                if isinstance(identifier, str):
                    if identifier in yielded:
                        raise self._incompatible(call, "duplicate_row")
                    yielded.add(identifier)
                yield row
            meta = envelope.get("meta") or {}
            next_offset = meta.get("nextOffsetId") if isinstance(meta, dict) else None
            if next_offset in seen:
                raise self._incompatible(call, "repeated_cursor")
            if not next_offset:
                return
            seen.add(next_offset)
            offset = next_offset
        raise PrincipleError(200, call.method, call.path, f"still paging after {max_pages} pages")


def _body_of(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]
