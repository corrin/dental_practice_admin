"""Principle's published specification, compared against the operations we call.

Marked `integration` because it fetches the live specification. It needs no credentials -- the
specification is public -- but it needs the network, so it does not belong in the hermetic
suite.

Only released operations are compared. Unrelated additions do not require an application update.
"""

from __future__ import annotations

import json

import pytest
from scripts.refresh_spec import FINGERPRINT, build, difference, fetch

pytestmark = pytest.mark.integration


def test_the_fingerprint_matches_the_published_specification() -> None:
    """Published changes to released operations produce an actionable structural difference."""
    committed = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    published = build(fetch(), set(committed["operations"]))

    for name in committed["operations"]:
        delta = difference(committed["operations"][name], published["operations"].get(name))
        assert not delta, f"{delta}\nRun: uv run python -m scripts.refresh_spec --update"


def test_every_catalogue_call_is_still_documented() -> None:
    """A call Principle no longer documents must surface as a failure.

    `build()` raises when a catalogue path is absent from the specification, which is the case
    worth catching: an endpoint withdrawn upstream still "works" until it does not.
    """
    committed = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    published = build(fetch(), set(committed["operations"]))
    assert set(committed["operations"]) <= set(published["operations"])
