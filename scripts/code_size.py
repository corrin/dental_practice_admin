"""Application size, counted by category rather than by wc -l.

The number that matters is **application
code** only. The reason is not accounting tidiness: application code is the only code that can make
a dentist ring up saying it is broken. It is what runs while staff are using the thing.

A test harness has never caused a phone call. Nor has a comment, nor a page of documentation. They
are reported so their size is visible, and judged against nothing, because shrinking them does not
make the practice's day go better:

    application    src/ and deploy/ -- everything that runs in production   reported
    comments       comments and docstrings, anywhere                       reported
    tooling        tests/ and scripts/                                     reported
    documentation  Markdown                                                reported

Counting comments or tests would actively raise the phone-call rate, by rewarding the deletion of
the sentence that records why a cursor guard exists and the test that proves it still works.

Application code also has its branches counted: every place execution can go two ways is another
case someone must hold in their head and another way to fail for a week unnoticed. Branches are
reported, not capped. The pre-commit review prints both numbers and what the commit does to them.

The count informs a judgement (Greybeard's Simplicity First, AGENTS.md); it is not a gate.
Run `uv run python -m scripts.code_size` for the breakdown.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

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
    """Counted lines, split into code and prose, and branches."""

    code: int = 0
    comments: int = 0
    branches: int = 0
    per_file: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    def add(self, path: str, code: int, comments: int, branches: int) -> None:
        self.code += code
        self.comments += comments
        self.branches += branches
        self.per_file[path] = (code, comments, branches)


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


_DECISIONS = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.match_case
)

TEMPLATE_BRANCH = re.compile(r"{%-?\s*(?:if|elif|for)\b")


def branches(source: str, suffix: str) -> int:
    """Places where execution can go more than one way.

    Python: if and elif, loops, except clauses, conditional expressions, match cases, each extra
    operand of `and`/`or`, and comprehension filters. Templates: `{% if %}`, `{% elif %}` and
    `{% for %}`; an inline `x if y else z` inside `{{ }}` is not seen. Other files have none.
    """
    if suffix == ".html":
        return len(TEMPLATE_BRANCH.findall(source))
    if suffix != ".py":
        return 0
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, _DECISIONS):
            total += 1
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            total += len(node.ifs)
    return total


def counted(path: str) -> bool:
    """Whether a repository-relative path is code this file counts."""
    parts = Path(path)
    is_code = parts.suffix in CODE_SUFFIXES or parts.name in COUNTED_NAMES
    return is_code and not SKIP_DIRS & set(parts.parts)


def measure(path: str, source: str) -> tuple[int, int, int]:
    """(code, comment, branch) counts for one file's contents."""
    suffix = Path(path).suffix
    code, comments = (
        python_lines(source) if suffix == ".py" else markup_lines(source, suffix)
    )
    return code, comments, branches(source, suffix)


def count(roots: tuple[str, ...]) -> Tally:
    """Every counted file under the given top-level directories, as it is on disk."""
    tally = Tally()
    for name in roots:
        base = ROOT / name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(ROOT).as_posix()
            if not path.is_file() or not counted(relative):
                continue
            tally.add(relative, *measure(relative, path.read_text(encoding="utf-8")))
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
    """The per-category, per-file breakdown printed by `python -m scripts.code_size`."""
    lines: list[str] = []
    for label, roots in CATEGORIES.items():
        tally = count(roots)
        application = label == "application"
        tail = f"   branches {tally.branches:>4}" if application else ""
        lines.append(
            f"{label:<14} code {tally.code:>5}   comments {tally.comments:>5}{tail}"
        )
        for path, (code, comments, branched) in tally.per_file.items():
            tail = f" {branched:>5}" if application else ""
            lines.append(f"  {path:<48} {code:>5} {comments:>5}{tail}")
    lines.append(f"{'documentation':<14}             {documentation():>5}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
