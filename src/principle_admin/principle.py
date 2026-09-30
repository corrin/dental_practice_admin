"""The documented Principle API, reached through one declarative catalogue of calls.

Every request the application makes is a `Call` in `CATALOGUE`. Nothing builds a URL by
hand. That single table is what lets the fake prove it serves every call we make
(`tests/test_fake_covers_catalogue.py`) and what the recorder walks to capture wire bodies
from staging, so the fake, the recordings and the client cannot drift apart.

Auth is the `X-API-Key` header (verified in SMS_Bridge/Services/PrincipleApiClient.cs and
od_data/recon_lib.py). Listings return `{"data": [...], "meta": {...}}` and page by
`offsetId`/`nextOffsetId`; `/v1/practices` returns `data` alone and does not page.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from principle_admin.config import Settings


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

    def url_path(self, path_params: Mapping[str, str]) -> str:
        """Fill the path template, refusing a missing or unexpected segment."""
        try:
            return self.path.format(**path_params)
        except KeyError as missing:
            raise CallError(f"{self.name} needs path parameter {missing}") from missing


_PAGING = frozenset({"limit", "offsetId"})
_TIMESTAMP_FILTERS = frozenset({"createdFrom", "createdTo", "updatedFrom", "updatedTo"})

CATALOGUE: tuple[Call, ...] = (
    Call(
        name="list_practices",
        method="GET",
        path="/v1/practices",
    ),
    Call(
        name="list_practitioners",
        method="GET",
        path="/v1/practices/{practice_id}/practitioners",
        # Observed 2026-10-01: this endpoint ignores `limit` and returns no `meta`. Declaring
        # a paging parameter it disregards would be claiming a filter that does not work.
    ),
    Call(
        name="list_appointments",
        method="GET",
        path="/v1/appointments",
        # `from` and `to` are required by the spec, and the window is half-open: an
        # appointment matches when event.from or event.to intersects [from, to).
        query=frozenset({"practiceId", "practitionerId", "status", "from", "to"})
        | _TIMESTAMP_FILTERS
        | _PAGING,
        required_query=frozenset({"practiceId", "from", "to"}),
        paginated=True,
    ),
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
        self, name: str, path_params: Mapping[str, str], query: Mapping[str, Any]
    ) -> tuple[Call, str, dict[str, str]]:
        call = BY_NAME.get(name)
        if call is None:
            raise CallError(f"no call named {name!r}; add it to CATALOGUE")
        undeclared = set(query) - call.query
        if undeclared:
            raise CallError(f"{name} does not accept {sorted(undeclared)}")
        sent = {key: str(value) for key, value in query.items() if value is not None}
        missing = call.required_query - set(sent)
        if missing:
            raise CallError(f"{name} requires {sorted(missing)}")
        return call, call.url_path(path_params), sent

    async def get(
        self,
        name: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One catalogue call, returning the decoded envelope.

        `query` is a mapping rather than keyword arguments because `from` is a Python
        keyword and the spec names it, and because a keyword form lets a caller's typo land
        on `page_size` instead of failing.
        """
        call, path, sent = self._prepare(name, path_params or {}, query or {})
        response = await self._client.request(call.method, path, params=sent)
        if response.status_code >= 400:
            raise PrincipleError(
                response.status_code, call.method, str(response.url), _body_of(response)
            )
        decoded = response.json()
        if not isinstance(decoded, dict):
            raise PrincipleError(
                response.status_code, call.method, str(response.url), decoded
            )
        return decoded

    async def rows(
        self,
        name: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int = 1000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Every row of a listing, following `meta.nextOffsetId` to the end.

        Two guards, both earned against the real API (observed 2026-10-01):

        A repeated cursor ends the walk. od_data hit non-advancing offsets here, and the
        spec's own PaginationMeta example has nextOffsetId equal to offsetId, so a client
        trusting the cursor to advance loops forever.

        A repeated row id raises. `nextOffsetId` is a `createdAt`, not a record id, and a
        cursor the server cannot place is ignored rather than refused -- so a stale cursor
        mid-walk silently restarts from page one. Yielding those rows again would inflate
        every count in the report, and a wrong number filed as a result is worse than a
        failure.

        `meta.total` is deliberately unused: it is the current page's row count, not the size
        of the result set.
        """
        call = BY_NAME.get(name)
        if call is None:
            raise CallError(f"no call named {name!r}; add it to CATALOGUE")
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
                        raise PrincipleError(
                            200,
                            call.method,
                            call.path,
                            f"paging returned {identifier!r} twice after offsetId={offset!r};"
                            " the cursor restarted and the rows cannot be counted",
                        )
                    yielded.add(identifier)
                yield row
            meta = envelope.get("meta") or {}
            next_offset = meta.get("nextOffsetId") if isinstance(meta, dict) else None
            if not next_offset or next_offset in seen:
                return
            seen.add(next_offset)
            offset = next_offset
        raise PrincipleError(
            200, call.method, call.path, f"still paging after {max_pages} pages"
        )


def _body_of(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]
