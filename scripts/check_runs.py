"""Read-only deployment check: the latest diary run must be recent and complete."""

import argparse
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def check(database: Path) -> None:
    """A database created by readiness is not evidence that the scheduled job works."""
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT principle, outcome, coverage, finished_at FROM task_runs "
            "WHERE task = 'daily_diary' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise ValueError("No daily diary run is recorded")
    principle, outcome, coverage, finished = row
    if (principle, outcome, coverage) != ("production", "succeeded", "complete"):
        raise ValueError(
            f"Latest diary: environment={principle}, outcome={outcome}, coverage={coverage}"
        )
    if not finished:
        raise ValueError("Latest diary has no completion time")
    age = datetime.now(UTC) - datetime.fromisoformat(finished)
    if age < timedelta(0) or age > timedelta(hours=26):
        raise ValueError("Latest completed diary is outside the daily schedule's 26-hour window")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    check(args.database)
    print("Latest production diary completed successfully within 26 hours")
