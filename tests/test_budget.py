"""The counter in scripts/code_size.py measures the right things."""

from __future__ import annotations

from scripts.code_size import CATEGORIES, branches, count, python_lines


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
    assert "deploy/dental-practice-admin.xml" in counted


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


def test_branches_are_counted_by_kind() -> None:
    """The branch count is a trend read across commits, so its classifier must not drift."""
    source = (
        "def f(xs, a, b, c):\n"
        "    if a and b or c:\n"  # if, plus `and` and `or`: 3
        "        pass\n"
        "    elif b:\n"  # 1
        "        pass\n"
        "    for x in xs:\n"  # 1
        "        try:\n"
        "            pass\n"
        "        except ValueError:\n"  # 1
        "            pass\n"
        "    return [x for x in xs if x] if a else None\n"  # filter and conditional: 2
    )
    assert branches(source, ".py") == 8
    template = "{% if a %}{% elif b %}{% else %}{% endif %}{% for x in y %}{% endfor %}"
    assert branches(template, ".html") == 3
    assert branches("<service/>", ".xml") == 0
