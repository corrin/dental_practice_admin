"""The 2,000-line budget, counted rather than hoped for.

ARCHITECTURE.md fixes the scope constraint at 2,000 lines of maintained Python, HTML,
JavaScript and setup scripts, with tests and generated code reported separately. A number
in prose drifts; this fails the build instead.

Run `uv run python -m tests.test_budget` for the breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BUDGET = 2000

ROOT = Path(__file__).resolve().parent.parent

COUNTED_SUFFIXES = frozenset({".py", ".html", ".js", ".ps1", ".psm1"})

# Maintained application code, the thing the budget constrains.
MAINTAINED = ("src", "deploy", "scripts")

# Reported, never budgeted: the fake and the suite are how the application is trusted, and
# ARCHITECTURE.md accounts for them separately.
REPORTED = ("tests",)

SKIP_DIRS = frozenset({"__pycache__", ".venv", "node_modules", ".git", "recordings", "spec"})


@dataclass(frozen=True)
class Count:
    """Non-blank counted lines, in total and per file."""

    lines: int
    files: dict[str, int]


def _count(roots: tuple[str, ...]) -> Count:
    """Non-blank lines of counted source under these directories."""
    files: dict[str, int] = {}
    for name in roots:
        base = ROOT / name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in COUNTED_SUFFIXES or not path.is_file():
                continue
            if SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            text = path.read_text(encoding="utf-8").splitlines()
            files[str(path.relative_to(ROOT)).replace("\\", "/")] = sum(
                1 for line in text if line.strip()
            )
    return Count(lines=sum(files.values()), files=files)


def report() -> str:
    maintained = _count(MAINTAINED)
    reported = _count(REPORTED)
    lines = [f"maintained {maintained.lines:>5} / {BUDGET}"]
    lines += [f"  {path:<52} {count:>5}" for path, count in maintained.files.items()]
    lines.append(f"tests and fake (reported, not budgeted) {reported.lines:>5}")
    lines += [f"  {path:<52} {count:>5}" for path, count in reported.files.items()]
    return "\n".join(lines)


def test_maintained_code_is_within_budget() -> None:
    """Exceeding the budget must break the build, not appear in a later review.

    ARCHITECTURE.md is explicit that a feature which would exceed the limit should be
    narrowed instead; that decision can only be made if the number is visible.
    """
    maintained = _count(MAINTAINED)
    assert maintained.lines <= BUDGET, f"maintained code is {maintained.lines} lines:\n{report()}"


def test_the_suite_is_not_counted_against_the_application() -> None:
    """The fake must never be an argument for cutting tested behaviour.

    If the suite were budgeted, the cheapest way to pass this file would be to delete the
    fake -- which is precisely backwards.
    """
    assert not (set(MAINTAINED) & set(REPORTED))
    assert _count(REPORTED).lines > 0


if __name__ == "__main__":
    print(report())
