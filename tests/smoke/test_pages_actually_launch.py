"""Does it launch? A real browser against the real server, on every page.

This tier exists because the suite was once green while the chat page did not start. The
end-to-end tests asserted the SSE backend and the store, all of which worked; nothing asserted
that a browser could load the page and use it. "The tests pass" and "it launches" were different
claims and only one of them was being checked.

The rule here is blunt on purpose: **any console error or failed request on any page fails the
test.** A front-end mistake does not have to be anticipated to be caught, which is the only way
this class of bug gets found by a machine rather than by opening a browser and remembering to look.

Not hermetic: the ChatKit component is a cross-origin iframe served from OpenAI's CDN. That is a
reason to mark the tier, not a reason to skip it — the component is part of the product, and a test
that cannot see it cannot tell us whether the product works. It needs internet; it needs no
credentials and costs nothing.

Run: `uv run pytest -m smoke`, or via scripts/release_gate.ps1.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import (
    ConsoleMessage,
    FrameLocator,
    Locator,
    Page,
    Request,
    expect,
)

from tests.fake_ai import MARKER
from tests.servers import DIARY_DATE, EXPECTED_BOOKED

pytestmark = pytest.mark.smoke

# Console and network noise from OpenAI's own bundle talking to OpenAI's own services: its
# telemetry call to chatgpt.com is blocked by CORS and we can neither fix it nor be harmed by it.
# Deliberately narrow, and deliberately NOT applied to uncaught exceptions -- the bug that made
# this tier necessary arrived as an uncaught exception thrown from inside that same bundle, so
# ignoring third-party exceptions would have ignored the defect.
THIRD_PARTY_ORIGINS = (
    "chatgpt.com",
    "cdn.platform.openai.com",
)

IGNORED_CONSOLE = (
    "Direct usage of named widget classes is deprecated",
    "favicon.ico",
    # Localhost is exempt from ChatKit domain verification; production is not, and
    # ADMIN_CHATKIT_DOMAIN_KEY must hold a key registered for the practice's hostname.
    "Domain verification skipped",
)

# How long the CDN component is given to register and render. Generous because it is someone
# else's network, and a flaky failure here would get the tier disabled.
COMPONENT_TIMEOUT_MS = 30_000


class PageProblems:
    """Every console error and failed request a page produced."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.third_party: list[str] = []

    def console(self, message: ConsoleMessage) -> None:
        """Record an error, unless OpenAI's own document raised it.

        Attribution is by the *source* of the message, not its text: the browser's echo of a
        blocked cross-origin fetch is the bare string "Failed to fetch", which names no URL. Asking
        where the message came from is the only way to tell our bug from theirs.
        """
        if message.type != "error":
            return
        if any(ignore in message.text for ignore in IGNORED_CONSOLE):
            return
        source = (message.location or {}).get("url", "") or ""
        if any(origin in f"{source} {message.text}" for origin in THIRD_PARTY_ORIGINS):
            self.third_party.append(f"{source[:90]} {message.text[:120]}")
            return
        self.problems.append(f"console error: {message.text[:300]} (from {source[:120]})")

    def page_error(self, error: Exception) -> None:
        self.problems.append(f"uncaught exception: {str(error)[:300]}")

    def request_failed(self, request: Request) -> None:
        if any(ignore in request.url for ignore in IGNORED_CONSOLE):
            return
        if any(origin in request.url for origin in THIRD_PARTY_ORIGINS):
            self.third_party.append(f"{request.url[:120]} ({request.failure})")
            return
        self.problems.append(f"request failed: {request.url[:150]} ({request.failure})")

    def assert_clean(self, where: str) -> None:
        listed = "\n".join(f"  - {problem}" for problem in self.problems)
        assert not self.problems, f"{where}: {len(self.problems)} problem(s)\n{listed}"


@pytest.fixture
def watched(page: Page) -> Iterator[PageProblems]:
    """A page whose console and network failures are recorded for assertion."""
    problems = PageProblems()
    page.on("console", problems.console)
    page.on("pageerror", problems.page_error)
    page.on("requestfailed", problems.request_failed)
    yield problems


def test_the_runs_page_loads_clean(
    page: Page, watched: PageProblems, spine: dict[str, str]
) -> None:
    """The page staff land on must render with nothing broken in the console."""
    page.goto(spine["APP_URL"], wait_until="load")
    expect(page.get_by_test_id("task-list")).to_be_visible()
    expect(page.get_by_test_id("fake-banner")).to_be_visible()
    expect(page.get_by_test_id("staff-identity")).to_be_visible()
    watched.assert_clean("the runs page")


def chat_ui(page: Page) -> FrameLocator:
    """The ChatKit interface.

    It is a **cross-origin iframe served from OpenAI's CDN**, not markup in our page and not a
    shadow root. Nothing in the parent frame can see inside it, which is why a `querySelectorAll`
    on the element found no composer and reported a working page as broken.

    Worth knowing beyond testing: the conversation is rendered by OpenAI's own document, so the
    text of a staff question is handled inside their frame as well as by their model.
    """
    return page.frame_locator("openai-chatkit iframe")


def composer(page: Page) -> Locator:
    """The box a staff member types into."""
    return chat_ui(page).get_by_role("textbox").first


def test_the_chat_page_loads_clean_and_the_component_starts(
    page: Page, watched: PageProblems, spine: dict[str, str]
) -> None:
    """The chat page must render a usable composer, not an empty box.

    This is the test whose absence let a broken chat page ship green: the component registered and
    still failed to initialise, so "the element exists" proves nothing. The assertion is that
    something a person can type into is actually on screen.
    """
    page.goto(f"{spine['APP_URL']}/chat", wait_until="load")
    expect(page.get_by_test_id("chatkit")).to_be_visible()
    expect(composer(page)).to_be_visible(timeout=COMPONENT_TIMEOUT_MS)
    watched.assert_clean("the chat page")


def test_a_staff_member_can_ask_a_question_and_get_an_answer(
    page: Page, watched: PageProblems, spine: dict[str, str]
) -> None:
    """The whole product, driven the way a person drives it.

    Browser -> ChatKit component -> our endpoint -> agent -> tool -> fake Principle, and the answer
    back again. Everything in that chain is real except the two external systems, and the numbers
    asserted are the fake Principle's, so a reply that contains them cannot have been invented
    anywhere along the way.
    """
    page.goto(f"{spine['APP_URL']}/chat", wait_until="load")
    expect(composer(page)).to_be_visible(timeout=COMPONENT_TIMEOUT_MS)

    composer(page).fill(f"How many appointments on {DIARY_DATE}?")
    composer(page).press("Enter")

    answer = chat_ui(page).get_by_text(MARKER, exact=False).first
    expect(answer).to_be_visible(timeout=COMPONENT_TIMEOUT_MS)
    assert f"of {EXPECTED_BOOKED} booked" in answer.inner_text(), (
        "the reply does not carry the fake Principle's numbers, so the tool did not run"
    )
    watched.assert_clean("asking a question")


def test_every_link_on_the_runs_page_resolves(
    page: Page, watched: PageProblems, spine: dict[str, str]
) -> None:
    """A link to a page that 404s is a broken application, however green the suite is."""
    page.goto(spine["APP_URL"], wait_until="load")
    hrefs = {
        href
        for href in page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
        if href and href.startswith("/")
    }
    assert hrefs, "the page offers no navigation at all"
    for href in sorted(hrefs):
        response = page.goto(f"{spine['APP_URL']}{href}", wait_until="load")
        assert response is not None, href
        assert response.status < 400, f"{href} returned {response.status}"
    watched.assert_clean("navigating every link")
