"""Score the staged diff against AGENTS.md, by asking Claude. Advisory: never refuses a commit.

The smells AGENTS.md names -- drift between two sources of truth, silent failures, speculative
features, "while I'm here" work -- need judgment, so no pattern can find them. A model's verdict on
the same diff varies between runs, and a gate that fails at random gets bypassed with --no-verify.
So this prints a score and findings, and always exits 0.

The rubric is AGENTS.md itself, read at run time, so there is no second copy to keep in step.

Run by the pre-commit hook only after scripts/scan_for_leaks.py passes: the diff is sent to
Anthropic, and the leak scan is what makes that safe.

    uv run python scripts/review_smells.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TIMEOUT_SECONDS = 120

INSTRUCTIONS = """\
You review one staged git diff for this repository. The engineering rules are below, between the
markers. Judge the diff only against them; ignore style, formatting and anything the rules do not
mention.

<rules>
{rules}
</rules>

Reply in plain text, no Markdown headings, in exactly this shape:

Score: N/10  (10 = nothing the rules would object to)
- path:line -- the rule it breaks -- one sentence on why
(one line per finding, most serious first; write "- none" if there are none)

Report only what the diff itself shows. Do not speculate about code you cannot see."""


def main() -> int:
    """Print the review, or why there is none. Always 0, so a commit is never refused here."""
    diff = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, check=True,
    ).stdout
    if not diff.strip():
        return 0

    claude = shutil.which("claude")
    if claude is None:
        print("Smell review skipped: the claude CLI is not on PATH.")
        return 0

    rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    print("Reviewing the staged diff against AGENTS.md (advisory)...")
    try:
        # No tools: the reviewer sees the diff and the rules, and can neither read nor change the
        # working tree.
        result = subprocess.run(
            [claude, "-p", INSTRUCTIONS.format(rules=rules), "--tools", "",
             "--no-session-persistence"],
            input=diff, capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
            timeout=TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"Smell review skipped: no answer within {TIMEOUT_SECONDS} seconds.")
        return 0

    if result.returncode != 0:
        print(f"Smell review skipped: claude exited {result.returncode}.\n{result.stderr.strip()}")
        return 0
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
