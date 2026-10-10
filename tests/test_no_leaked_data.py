"""Nothing tracked in this repository may contain patient data, staff identities or credentials.

The pre-commit hook is convenience; this is the gate. A hook lives in .git, so it is absent on a
fresh clone, absent in CI, and skipped by --no-verify. A test is none of those things.

Patient privacy is the highest-consequence failure mode this project has, and the defences before
these were passive: a .gitignore and remembering to look.
"""

from __future__ import annotations

from scripts.scan_for_leaks import SHAPES, scan


def test_nothing_tracked_leaks_data() -> None:
    """Fails with the offending paths named, so the fix is obvious.

    The scanner reads the real values from .env, which is gitignored, so it recognises the
    practice's actual key and addresses without any of them being written down here.
    """
    findings = scan()
    assert not findings, "tracked files contain data that must not be committed:\n" + "\n".join(
        str(finding) for finding in findings
    )


def test_the_scanner_catches_a_planted_credential() -> None:
    """A scanner that never fires is indistinguishable from a broken one.

    Proves the shape patterns match something, so a regex quietly broken by an edit -- the
    lookbehind that stopped "task-scheduler-start-page" reading as an OpenAI key, for instance --
    is caught rather than silently passing everything.
    """
    planted = {
        "an OpenAI key": "sk-svcacct-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        "a Google client secret": "GOCSPX-AbCdEfGhIjKlMnOpQrSt",
        "a private key block": "-----BEGIN RSA PRIVATE KEY-----",
        "an NHI number": "ZAC5768",
    }
    for description, pattern in SHAPES:
        assert pattern.search(planted[description]), f"{description} no longer matches"


def test_ordinary_text_is_not_flagged() -> None:
    """The false positives that would get this bypassed.

    Every one of these was found firing wrongly: a documentation URL containing "task-scheduler",
    and the fake credentials that legitimately appear in the fake Principle.
    """
    innocent = (
        "https://learn.microsoft.com/windows/win32/taskschd/task-scheduler-start-page",
        "fake-principle-key",
        "fake-practice-0001",
        "Dr Ada Whitwell",
    )
    for description, pattern in SHAPES:
        for text in innocent:
            assert not pattern.search(text), f"{description} wrongly matched {text!r}"
