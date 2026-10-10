"""What the production server installs must at least be readable by the tools that install it."""
from pathlib import Path
from xml.dom import minidom

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"


@pytest.mark.parametrize("path", sorted(DEPLOY.glob("*.xml")), ids=lambda p: p.name)
def test_service_and_task_definitions_are_well_formed_xml(path: Path) -> None:
    """WinSW and Task Scheduler refuse a malformed file, and only at install on the server.

    The easy slip is two hyphens in a comment, which XML forbids, from quoting a command
    line option there.
    """
    minidom.parse(str(path))
