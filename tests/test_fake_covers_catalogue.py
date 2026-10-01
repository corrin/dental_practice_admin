"""The fake must serve every call the application makes, and refuse everything else."""

from __future__ import annotations

import re

import pytest

from dental_practice_admin.principle import CATALOGUE, Call
from tests.fake import FakeStore, dispatch
from tests.fake.server import ROUTES, FakeUnhandledRouteError, Request
from tests.fake.store import FAKE_API_KEY

SAMPLE_SEGMENT = "sample"


def _concrete_path(call: Call) -> str:
    """The call's path with every template segment filled, so a route regex can match it."""
    return re.sub(r"\{[^}]+\}", SAMPLE_SEGMENT, call.path)


@pytest.mark.parametrize("call", CATALOGUE, ids=lambda call: call.name)
def test_every_catalogue_call_is_routed(call: Call) -> None:
    """Adding a call to CATALOGUE without a fake route is caught on the first fake run.

    Without this, a new operation would appear to work locally only when the developer
    happened to exercise it, and a missing route would surface as an unhandled exception
    during a real run instead.
    """
    path = _concrete_path(call)
    matched = [
        pattern.pattern
        for method, pattern, _ in ROUTES
        if method == call.method and pattern.match(path)
    ]
    assert matched, (
        f"{call.name} ({call.method} {call.path}) has no route in tests/fake/server.py"
    )


def test_unrouted_call_refuses(fake_store: FakeStore) -> None:
    """A future edit that makes the fake answer an unknown path would be caught here.

    Guessing an answer is the failure this fake exists to avoid: it would return a shape
    nobody verified and the suite would go green on it.
    """
    request = Request(
        method="GET",
        path="/v1/patients/anything/invoices",
        query={},
        headers={"x-api-key": FAKE_API_KEY},
    )
    with pytest.raises(FakeUnhandledRouteError, match="no route for"):
        dispatch(fake_store, request)
