"""What the production server installs must at least be readable by the tools that install it."""
from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"
TASK = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


@pytest.mark.parametrize("path", sorted(DEPLOY.glob("*.xml")), ids=lambda p: p.name)
def test_service_and_task_definitions_are_well_formed_xml(path: Path) -> None:
    """WinSW and Task Scheduler refuse a malformed file, and only at install on the server.

    The easy slip is two hyphens in a comment, which XML forbids, from quoting a command
    line option there.
    """
    minidom.parse(str(path))


def test_the_task_runner_lets_a_long_run_finish_and_the_next_launch_start() -> None:
    """ADR 0009: Windows must neither stop a long run nor hold back the next launch."""
    task = ElementTree.parse(DEPLOY / "task-runner.xml").getroot()
    settings = task.find(f"{TASK}Settings")
    assert settings is not None
    assert settings.findtext(f"{TASK}MultipleInstancesPolicy") == "Parallel"
    assert settings.findtext(f"{TASK}ExecutionTimeLimit") == "PT0S"
