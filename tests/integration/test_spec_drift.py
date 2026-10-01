"""Principle's published specification, compared against the operations we call.

Marked `integration` because it fetches the live specification. It needs no credentials -- the
specification is public -- but it needs the network, so it does not belong in the hermetic
suite.

Scoped deliberately: only the three operations in CATALOGUE are compared. A full-document diff
would fire on every unrelated Principle addition and would be muted within a month.
"""

from __future__ import annotations

import json

import pytest
from scripts.refresh_spec import FINGERPRINT, build, fetch

pytestmark = pytest.mark.integration


def test_the_fingerprint_matches_the_published_specification() -> None:
    """Principle changing an operation we call must fail here, not in a staff report.

    The version string is no help: the published specification gained 631 lines and four paths
    over SMS_Bridge's copy while both declared 1.1.0. Only content shows the change.

    When this fails, read the difference and then run:
        uv run python scripts/refresh_spec.py
    """
    committed = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    published = build(fetch())

    assert published["operations"].keys() == committed["operations"].keys()
    for name in committed["operations"]:
        assert published["operations"][name] == committed["operations"][name], (
            f"Principle's specification for {name} has changed"
        )


def test_every_catalogue_call_is_still_documented() -> None:
    """A call Principle no longer documents must surface as a failure.

    `build()` raises when a catalogue path is absent from the specification, which is the case
    worth catching: an endpoint withdrawn upstream still "works" until it does not.
    """
    published = build(fetch())
    assert published["operations"], "the catalogue produced no operations"
