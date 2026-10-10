"""The fake Principle as an ASGI application: every answer computed from the store.

One dispatch core, two ways in -- `transport()` for pytest (no socket at all) and
`uvicorn tests.fake.server:app` for Playwright and manual sessions. The application above
the transport is identical on fake, staging and production, so auth, paging, timeouts and
error mapping are exercised for real here.

Three things this refuses rather than guesses, because each guess would be a belief about
Principle wearing a green tick:

  * a route it does not serve            -> FakeUnhandledRouteError
  * a query parameter it does not apply  -> FakeUnhandledParameterError
  * an error body nobody has recorded    -> FakeRefusalNotRecordedError

The third is what keeps refusal wording honest: the fake may only refuse in words captured
from the real API (scripts/record_principle_wire.py), never in words we invented.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2 as httpx

from tests.fake.store import FAKE_API_KEY, SERVER_DEFAULT_LIMIT, FakeStore, Page, seed

REFUSALS_DIR = Path(__file__).resolve().parent.parent / "recordings" / "refusals"

MAX_LIMIT = 500


class FakeUnhandledRouteError(NotImplementedError):
    """A call the fake has no route for: add it, from a recording."""


class FakeUnhandledParameterError(NotImplementedError):
    """A parameter the fake does not apply.

    Never dropped: a dropped filter returns a plausible wrong answer, which is harder to
    notice than a failure.
    """


class FakeRefusalNotRecordedError(NotImplementedError):
    """An error the fake would have to invent wording for.

    Capture it from the real API instead: provoke it against staging and save the body to
    tests/recordings/refusals/<name>.json.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"no recorded refusal {name!r}; provoke it against staging and save "
            f"{REFUSALS_DIR / f'{name}.json'} rather than inventing a body"
        )


@dataclass(frozen=True)
class Request:
    """One call as it reached the transport."""

    method: str
    path: str
    query: dict[str, str]
    headers: Mapping[str, str]

    def api_key(self) -> str | None:
        for name, value in self.headers.items():
            if name.lower() == "x-api-key":
                return value
        return None


@dataclass(frozen=True)
class Response:
    """One answer, as the status and decoded body the transport will deliver."""

    status: int
    body: object


Handler = Callable[[FakeStore, Request, "re.Match[str]"], Response]


def _refusal(name: str) -> Response:
    """An error, in the words and with the status the real API was recorded producing.

    The recording carries the status, so the fake cannot disagree with Principle about
    which code an error is either. Written by scripts/record_principle_wire.py.
    """
    recorded = REFUSALS_DIR / f"{name}.json"
    if not recorded.exists():
        raise FakeRefusalNotRecordedError(name)
    document = json.loads(recorded.read_text(encoding="utf-8"))
    return Response(int(document["status"]), document["body"])


def _paging(request: Request) -> tuple[int, str | None]:
    limit = request.query.get("limit")
    if limit is None:
        return SERVER_DEFAULT_LIMIT, request.query.get("offsetId")
    if not limit.isdigit() or not 1 <= int(limit) <= MAX_LIMIT:
        return _bad_request_limit(limit)
    return int(limit), request.query.get("offsetId")


def _bad_request_limit(limit: str) -> tuple[int, str | None]:
    raise FakeRefusalNotRecordedError(f"bad_limit_{limit}")


def _envelope(page: Page, limit: int) -> dict[str, object]:
    """The listing envelope. `total` is this page's row count, as Principle sends it."""
    meta: dict[str, object] = {"limit": limit, "total": page.total}
    if page.next_offset_id is not None:
        meta["nextOffsetId"] = page.next_offset_id
    return {"data": page.rows, "meta": meta}


def _only(request: Request, implemented: frozenset[str]) -> None:
    """Refuse a parameter this route does not actually apply."""
    unhandled = set(request.query) - implemented
    if unhandled:
        raise FakeUnhandledParameterError(
            f"{request.method} {request.path} does not apply {sorted(unhandled)}; "
            "implement it in tests/fake or stop sending it"
        )


def list_practices(store: FakeStore, request: Request, _match: re.Match[str]) -> Response:
    _only(request, frozenset())
    return Response(200, {"data": store.practices()})


def list_practitioners(store: FakeStore, request: Request, match: re.Match[str]) -> Response:
    """Every practitioner, with no `meta`: the real endpoint is not paginated."""
    _only(request, frozenset())
    return Response(200, {"data": store.practitioners(match.group("practice_id"))})


def list_appointments(store: FakeStore, request: Request, _match: re.Match[str]) -> Response:
    _only(
        request,
        frozenset({"practiceId", "practitionerId", "status", "from", "to", "limit", "offsetId"}),
    )
    limit, offset = _paging(request)
    page = store.appointments(
        practice_id=request.query["practiceId"],
        window_from=request.query["from"],
        window_to=request.query["to"],
        limit=limit,
        offset_id=offset,
        practitioner_id=request.query.get("practitionerId"),
        status=request.query.get("status"),
    )
    return Response(200, _envelope(page, limit))


def get_patient(store: FakeStore, request: Request, match: re.Match[str]) -> Response:
    _only(request, frozenset())
    patient = store.patient(match.group("patient_id"))
    if patient is None:
        return _refusal("patient_not_found")
    return Response(200, patient)


def search_patients(store: FakeStore, request: Request, _match: re.Match[str]) -> Response:
    """Every match at once, with no `meta`: the endpoint is not paginated."""
    _only(request, frozenset({"practiceId", "name"}))
    return Response(200, {"data": store.search_patients(request.query["practiceId"],
                                                        request.query["name"])})


DATE_RANGE = frozenset({"practiceId", "createdFrom", "createdTo", "updatedFrom", "updatedTo",
                        "limit", "offsetId"})


def list_invoices(store: FakeStore, request: Request, _match: re.Match[str]) -> Response:
    _only(request, DATE_RANGE)
    limit, offset = _paging(request)
    page = store.changed("invoices", request.query["practiceId"], request.query, limit, offset)
    return Response(200, _envelope(page, limit))


def list_transactions(store: FakeStore, request: Request, _match: re.Match[str]) -> Response:
    _only(request, DATE_RANGE)
    limit, offset = _paging(request)
    page = store.changed("transactions", request.query["practiceId"], request.query, limit,
                         offset)
    return Response(200, _envelope(page, limit))


def _route(method: str, pattern: str, handler: Handler) -> tuple[str, re.Pattern[str], Handler]:
    return method, re.compile(f"^{pattern}$"), handler


ROUTES: tuple[tuple[str, re.Pattern[str], Handler], ...] = (
    _route("GET", r"/v1/practices", list_practices),
    _route(
        "GET",
        r"/v1/practices/(?P<practice_id>[^/]+)/practitioners",
        list_practitioners,
    ),
    _route("GET", r"/v1/appointments", list_appointments),
    _route("GET", r"/v1/patients", search_patients),
    _route("GET", r"/v1/patients/(?P<patient_id>[^/]+)", get_patient),
    _route("GET", r"/v1/invoices", list_invoices),
    _route("GET", r"/v1/transactions", list_transactions),
)


def routes_served() -> frozenset[tuple[str, str]]:
    """Every (method, path pattern) the table answers, for the catalogue-coverage test."""
    return frozenset((method, pattern.pattern) for method, pattern, _ in ROUTES)


def dispatch(store: FakeStore, request: Request) -> Response:
    """Answer from the first matching route, or refuse."""
    if request.api_key() != FAKE_API_KEY:
        return _refusal("unauthorised")
    for method, pattern, handler in ROUTES:
        match = pattern.match(request.path)
        if match is None or method != request.method:
            continue
        return handler(store, request, match)
    raise FakeUnhandledRouteError(
        f"the fake Principle has no route for {request.method} {request.path}; "
        "add it to tests/fake/server.py, from a recording"
    )


class FakePrinciple:
    """ASGI application wrapping one store."""

    def __init__(self, store: FakeStore | None = None) -> None:
        self.store = store if store is not None else seed()

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            raise FakeUnhandledRouteError(f"the fake Principle serves http, not {scope['type']!r}")
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        request = Request(
            method=scope["method"],
            path=scope["path"],
            query={
                key: values[0]
                for key, values in parse_qs(scope.get("query_string", b"").decode()).items()
            },
            headers={
                name.decode().lower(): value.decode() for name, value in scope.get("headers", [])
            },
        )
        response = dispatch(self.store, request)
        body = json.dumps(response.body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": response.status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def transport(store: FakeStore | None = None) -> httpx.ASGITransport:
    """The fake installed where the socket would be, for the real client to use."""
    return httpx.ASGITransport(app=FakePrinciple(store))


app = FakePrinciple()
