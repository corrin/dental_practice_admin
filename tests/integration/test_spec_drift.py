"""Released API shape must match the published contract before release."""
import json

import pytest
from scripts.refresh_spec import ROOT, SNAPSHOT, build, fetch

pytestmark = pytest.mark.integration

def test_the_released_specification_matches_upstream() -> None:
    assert json.loads((ROOT / SNAPSHOT).read_text(encoding="utf-8")) == build(fetch())
