"""The 2,000-line budget, counted by category rather than by wc -l.

ARCHITECTURE.md fixes the scope constraint at 2,000 lines, and the number applies to **application
code** only. The reason is not accounting tidiness: application code is the only code that can make
a dentist ring up saying it is broken. It is what runs while staff are using the thing.

A test harness has never caused a phone call. Nor has a comment, nor a page of documentation. They
are reported so their size is visible, and budgeted against nothing, because shrinking them does not
make the practice's day go better:

    application    src/ and deploy/ -- everything that runs in production   <= 2,000
    comments       comments and docstrings, anywhere                       reported
    tooling        tests/ and scripts/                                     reported
    documentation  Markdown                                                reported

Counting comments or tests would actively raise the phone-call rate, by rewarding the deletion of
the sentence that records why a cursor guard exists and the test that proves it still works.

Run `uv run python -m tests.test_budget` for the breakdown.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

BUDGET = 2000

ROOT = Path(__file__).resolve().parent.parent

CODE_SUFFIXES = frozenset({".py", ".html", ".js", ".ps1", ".psm1", ".xml"})

# Files whose name is the whole identification. The Caddyfile has no extension and is the most
# phone-call-prone configuration in the repository; it was invisible to this counter until now.
COUNTED_NAMES = frozenset({"Caddyfile"})

# The split is by directory so there is no list of exceptions to keep correct.
#
#   deploy/   runs in production. A wrong reverse_proxy line or a missing uvicorn flag breaks the
#             application for staff exactly as a wrong statement in src/ would, so it is budgeted.
#   scripts/  tools, run by whoever is maintaining this. The recorder, the fingerprinter, the
#             release gate, verify.ps1. None of it is running while staff use the application, so
#             none of it can break for them.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "application": ("src", "deploy"),
    "tooling": ("tests", "scripts"),
}

SKIP_DIRS = frozenset({"__pycache__", ".venv", "node_modules", ".git", "recordings", "spec"})

# Markdown outside the dependency tree.
DOC_SUFFIXES = frozenset({".md"})


@dataclass
class Tally:
    """Counted lines, split into code and prose."""

    code: int = 0
    comments: int = 0
    per_file: dict[str, tuple[int, int]] = field(default_factory=dict)

    def add(self, path: str, code: int, comments: int) -> None:
        self.code += code
        self.comments += comments
        self.per_file[path] = (code, comments)


def _fallback(source: str) -> tuple[int, int]:
    """Counts for a file the tokeniser rejected.

    Every non-blank line counts as code. A file that will not tokenise is broken, and reporting
    a flattering number for it would hide that behind an accounting detail.
    """
    return sum(1 for line in source.splitlines() if line.strip()), 0


def python_lines(source: str) -> tuple[int, int]:
    """(code, comment) line counts for one Python file.

    Docstrings count as comments, which is the whole point of the split: a module docstring
    explaining why the fake refuses rather than guesses is documentation, not application code.
    A line carrying both a statement and a trailing comment counts as code.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _fallback(source)

    commented: set[int] = set()
    code_lines: set[int] = set()
    for token in tokens:
        if token.type == tokenize.COMMENT:
            commented.add(token.start[0])
        elif token.type in _STRUCTURAL_TOKENS:
            continue
        else:
            code_lines.update(range(token.start[0], token.end[0] + 1))

    # A trailing comment does not turn its statement into prose, so only lines carrying nothing
    # but a comment count as such.
    comment_lines = commented - code_lines
    for start, end in _docstring_ranges(source):
        span = set(range(start, end + 1))
        comment_lines |= span
        code_lines -= span

    blank = {
        number for number, line in enumerate(source.splitlines(), start=1) if not line.strip()
    }
    return len(code_lines - blank), len(comment_lines - blank)


_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_ranges(source: str) -> list[tuple[int, int]]:
    """Line spans of every docstring, so they are counted as prose."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
        ):
            ranges.append((first.lineno, first.end_lineno))
    return ranges


# Tokens that occupy a line without being a statement.
_STRUCTURAL_TOKENS = frozenset(
    {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
)

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
PS_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.DOTALL)
# The Caddyfile has only "#" comments, so nothing spans lines.
HASH_ONLY = re.compile(r"(?!x)x")


def markup_lines(source: str, suffix: str) -> tuple[int, int]:
    """(code, comment) counts for a non-Python file, by stripping comment spans.

    Approximate by nature -- a `//` inside a string literal reads as a comment -- and good enough
    for an accounting report. The Python count, which carries almost all the lines, is exact.
    """
    total = sum(1 for line in source.splitlines() if line.strip())
    pattern = {
        ".html": HTML_COMMENT,
        ".xml": HTML_COMMENT,
        ".js": BLOCK_COMMENT,
        ".ps1": PS_BLOCK_COMMENT,
        ".psm1": PS_BLOCK_COMMENT,
        "": HASH_ONLY,
    }[suffix]
    stripped = pattern.sub("", source)
    single = "#" if suffix in {".ps1", ".psm1", ""} else "//"
    kept = [
        line
        for line in stripped.splitlines()
        if line.strip() and not line.strip().startswith(single)
    ]
    return len(kept), total - len(kept)


def count(roots: tuple[str, ...]) -> Tally:
    tally = Tally()
    for name in roots:
        base = ROOT / name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            counted = path.suffix in CODE_SUFFIXES or path.name in COUNTED_NAMES
            if not counted or not path.is_file():
                continue
            if SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            source = path.read_text(encoding="utf-8")
            code, comments = (
                python_lines(source)
                if path.suffix == ".py"
                else markup_lines(source, path.suffix)
            )
            tally.add(str(path.relative_to(ROOT)).replace("\\", "/"), code, comments)
    return tally


def documentation() -> int:
    """Non-blank Markdown lines, excluding the vendored licence."""
    total = 0
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in DOC_SUFFIXES or not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return total


def report() -> str:
    lines: list[str] = []
    for label, roots in CATEGORIES.items():
        tally = count(roots)
        cap = f" / {BUDGET}" if label == "application" else ""
        lines.append(f"{label:<14} code {tally.code:>5}{cap}   comments {tally.comments:>5}")
        for path, (code, comments) in tally.per_file.items():
            lines.append(f"  {path:<48} {code:>5} {comments:>5}")
    lines.append(f"{'documentation':<14}             {documentation():>5}")
    return "\n".join(lines)


def test_application_code_is_within_budget() -> None:
    """Exceeding the budget must break the build, not appear in a later review.

    ARCHITECTURE.md is explicit that a feature which would exceed the limit should be narrowed
    instead; that decision can only be made if the number is visible.
    """
    application = count(CATEGORIES["application"])
    assert application.code <= BUDGET, (
        f"application code is {application.code} lines:\n{report()}"
    )


def test_comments_are_not_counted_against_the_application() -> None:
    """A budget that priced comments would reward deleting the explanations.

    No comment has ever caused a dentist to ring up. The hard-won lines in this codebase are prose:
    why the cursor guard exists, why the fake refuses rather than guesses, why a practice day is not
    a UTC day. Counting them would make removing them the cheapest way to pass this file.
    """
    application = count(CATEGORIES["application"])
    assert application.comments > 0
    assert application.code < application.code + application.comments


def test_production_configuration_is_counted() -> None:
    """A wrong line in the Caddyfile or the service definition breaks the application for staff.

    Those files were invisible to this counter until the rule was stated properly: the budget is
    for whatever can ring the phone, and `reverse_proxy` pointing at the wrong port rings it just as
    loudly as a bad statement in src/.
    """
    counted = count(CATEGORIES["application"]).per_file
    assert "deploy/Caddyfile" in counted
    assert "deploy/principle-admin.xml" in counted


def test_the_harness_is_not_counted_against_the_application() -> None:
    """The fake must never be an argument for cutting tested behaviour.

    The harness does not run while staff are using the application, so it cannot break for them.
    If it were budgeted, the cheapest way to pass would be to delete the fake -- trading something
    that cannot cause a phone call for something that can.
    """
    assert not set(CATEGORIES["application"]) & set(CATEGORIES["tooling"])
    assert count(CATEGORIES["tooling"]).code > 0


def test_docstrings_count_as_comments_not_code() -> None:
    """The classifier itself needs a regression test.

    If docstrings were counted as code, the budget would silently tighten by several hundred
    lines and the failure would look like a feature being too large.
    """
    source = (
        '"""Module docstring.\n\nSecond line.\n"""\n\n\n'
        'def f() -> int:\n    """One line."""\n'
        "    # A comment.\n    return 1  # trailing\n"
    )
    code, comments = python_lines(source)
    assert code == 2, f"expected `def` and `return` only, got {code}"
    assert comments == 5, f"expected 4 docstring lines and 1 comment line, got {comments}"


if __name__ == "__main__":
    print(report())
