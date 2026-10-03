"""Read-only verification of the application's five-minute launcher audit."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dental_practice_admin.schedules import POLL_SECONDS


def check(path: Path) -> None:
    """An OS process exit alone does not prove that application schedules were checked."""
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    age = (datetime.now(UTC) - datetime.fromisoformat(event["at"])).total_seconds()
    if event["event"] != "checked" or event["failures"] or not 0 <= age <= 2 * POLL_SECONDS:
        raise ValueError("Application launcher audit is stale or records a failure")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
    print("Application checked its schedules within two polling intervals")
