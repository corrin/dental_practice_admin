"""Refuse to commit patient data, staff identities or credentials.

Patient privacy is a high-consequence failure mode, and the defences before this were passive: a
.gitignore and remembering. That is how a patient identifier reached tests/recordings/ and sat
there until someone thought to look.

Three checks, each chosen to almost never fire wrongly. A scanner that cries wolf gets bypassed
with --no-verify, which is worse than no scanner.

1. **No value from .env appears in tracked content.** The strongest check and the least clever: it
   knows the real API key, the real Google secret and the real staff addresses because .env holds
   them, and .env is gitignored. No pattern to guess and no secret written down here.

2. **Shapes that are never legitimate in source.** Provider key prefixes, private key blocks, and
   NZ NHI numbers -- the health identifier, which has no business in a repository.

3. **Files that should never be tracked.** Anything under tests/recordings/ that is not a refusal
   body, since that directory is where real wire data lands.

Run on staged content by the pre-commit hook, and over the whole tree by
tests/test_no_leaked_data.py so that CI catches whatever a local commit skipped.

    uv run python scripts/scan_for_leaks.py            # the whole tree
    uv run python scripts/scan_for_leaks.py --staged   # what is about to be committed
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Values in .env that are not secret, and which do legitimately appear in tracked files: the
# practice's own domain in a Caddyfile, a localhost URL in documentation, a model name.
PUBLIC_SETTINGS = frozenset(
    {
        "PRINCIPLE_ENVIRONMENT",
        "PRINCIPLE_API_BASE_URL_FAKE",
        "PRINCIPLE_API_BASE_URL_STAGING",
        "PRINCIPLE_API_BASE_URL_PROD",
        "PRINCIPLE_API_KEY_FAKE",
        "ADMIN_SIGN_IN",
        "ADMIN_DATA_ROOT",
        "ADMIN_AGENT_MODEL",
        "ADMIN_STAFF_DOMAIN",
        # Rendered into the chat page by design; public by construction.
        "ADMIN_CHATKIT_DOMAIN_KEY",
        "OPENAI_BASE_URL",
    }
)

# Short values match by accident. A real key, address or identifier is longer than this.
MIN_SECRET_LENGTH = 12

# Everything invented carries this marker, which is what makes a scanner possible at all: real
# patient data cannot be enumerated, but fake data can be declared. A value carrying the marker is
# not a secret however much it looks like one, and a name that lacks it in test data is suspect.
FAKE_MARKER = re.compile(r"fake", re.IGNORECASE)

SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The lookbehind matters: without it "task-scheduler-start-page" reads as an OpenAI key.
    (
        "an OpenAI key",
        re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    ),
    ("a Google client secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}")),
    ("a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # NZ National Health Index: three letters (no I or O), four digits. Never legitimately in code.
    ("an NHI number", re.compile(r"\b[A-HJ-NP-Z]{3}\d{4}\b")),
)

# Where real wire data lands. Only recorded refusals -- the API's own error wording -- are safe to
# track; success bodies derive from patient records.
RECORDINGS = "tests/recordings/"
ALLOWED_RECORDINGS = (f"{RECORDINGS}README.md", f"{RECORDINGS}refusals/")

SKIP_SUFFIXES = frozenset({".png", ".jpg", ".pdf", ".db", ".lock"})

# These two necessarily contain the shapes, because they are what defines and proves them. The
# exemption is by exact path and nothing wider: a directory-level exemption would be a hole.
SELF_REFERENTIAL = frozenset(
    {"scripts/scan_for_leaks.py", "tests/test_no_leaked_data.py"}
)


@dataclass(frozen=True)
class Finding:
    """One reason a file must not be committed."""

    path: str
    reason: str

    def __str__(self) -> str:
        return f"  {self.path}: {self.reason}"


def secrets_from_env() -> dict[str, str]:
    """The real values to look for, read from the gitignored .env.

    Reading them rather than hard-coding them is the point: the scanner knows the practice's actual
    key and addresses without any of them being written into a tracked file.
    """
    env = ROOT / ".env"
    if not env.exists():
        return {}
    found: dict[str, str] = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if name in PUBLIC_SETTINGS or len(value) < MIN_SECRET_LENGTH:
            continue
        if FAKE_MARKER.search(value):
            continue
        # A comma-separated allowlist is several values, each worth finding on its own.
        for part in value.split(","):
            part = part.strip()
            if len(part) >= MIN_SECRET_LENGTH:
                found[part] = name
    return found


def tracked_files(staged: bool) -> list[str]:
    """Everything tracked, or just what is about to be committed."""
    command = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
        if staged
        else ["git", "ls-files"]
    )
    result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT, check=True)
    return [name for name in result.stdout.split("\n") if name.strip()]


def content_of(path: str, staged: bool) -> str | None:
    """What will actually be committed, which is not necessarily what is on disk."""
    if staged:
        result = subprocess.run(
            ["git", "show", f":{path}"], capture_output=True, cwd=ROOT, check=False
        )
        if result.returncode != 0:
            return None
        raw = result.stdout
    else:
        file = ROOT / path
        if not file.is_file():
            return None
        raw = file.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan(staged: bool = False) -> list[Finding]:
    """Every reason the given content must not be committed."""
    secrets = secrets_from_env()
    findings: list[Finding] = []

    for path in tracked_files(staged):
        if path.startswith(RECORDINGS) and not path.startswith(ALLOWED_RECORDINGS):
            findings.append(
                Finding(path, "wire data from a real tenant; only refusals may be tracked")
            )
            continue
        if path == ".env" or path.startswith(".env."):
            findings.append(Finding(path, "holds the practice's credentials; it is gitignored"))
            continue
        if Path(path).suffix in SKIP_SUFFIXES or path in SELF_REFERENTIAL:
            continue

        text = content_of(path, staged)
        if text is None:
            continue

        for value, name in secrets.items():
            if value in text:
                findings.append(Finding(path, f"contains the value of {name} from .env"))
        for description, pattern in SHAPES:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(path, f"looks like {description}: {match.group()[:12]}...")
                )

    return findings


def main() -> int:
    """Scan, and refuse with the offending paths named."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="scan what is about to be committed"
    )
    arguments = parser.parse_args()

    findings = scan(staged=arguments.staged)
    if not findings:
        where = "staged changes" if arguments.staged else "tracked files"
        print(f"No patient data, staff identities or credentials found in {where}.")
        return 0

    print(f"Refusing: {len(findings)} problem(s)\n", file=sys.stderr)
    for finding in findings:
        print(finding, file=sys.stderr)
    print(
        "\nNothing here is a formatting nit. Remove the value, or if it is genuinely safe, "
        "narrow the check in scripts/scan_for_leaks.py deliberately.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
