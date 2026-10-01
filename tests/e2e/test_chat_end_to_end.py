"""Chat over real HTTP, against the running application.

Deliberately not through the browser embed. The ChatKit web component loads from OpenAI's CDN
and verifies the serving domain, so a spec that drives it is neither hermetic nor offline — it
would fail on a practice network without internet and tell us nothing about our own code. The
component is exercised by an opt-in `-m ui` spec instead; this tier proves everything behind it.

Both external systems are simulated in their own processes: Principle by `tests.fake.server`, the
model by `tests.fake_ai.server`, the latter reached through `OPENAI_BASE_URL`. The application
itself has no idea either is a simulation, which is the property that makes this worth running.

What is real here that the in-process tests cannot reach: Uvicorn, the SSE response and its
buffering headers, the session middleware, the auth dependency, and a second process holding the
SQLite file the task writes to.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.fake_ai import MARKER
from tests.servers import DIARY_DATE, EXPECTED_BOOKED

pytestmark = pytest.mark.e2e


def test_the_chat_page_is_served_and_links_the_component(spine: dict[str, str]) -> None:
    """The page must exist and point the component at our own endpoint.

    A component with no `api.url` silently talks to OpenAI's hosted backend instead of ours,
    which would answer without ever reading the practice's diary.
    """
    page = httpx.get(f"{spine['APP_URL']}/chat", timeout=30)
    assert page.status_code == 200
    assert 'data-automation-id="chatkit"' in page.text
    assert "'/chatkit'" in page.text or '"/chatkit"' in page.text


def test_the_chatkit_endpoint_streams_with_buffering_disabled(spine: dict[str, str]) -> None:
    """A streamed reply must not arrive as one silent pause and then a wall of text.

    `X-Accel-Buffering: no` and an identity encoding are what stop a reverse proxy holding the
    whole SSE response until the run completes. Without them chat appears to hang.

    The whole turn is read, because the simulated model answers immediately: the tool runs against
    the fake Principle and the reply is grounded in what it returned. A real model here would make
    this spec slow, costly and non-deterministic, and would still not test our code any harder.
    """
    request = {
        "op": "threads.create",
        "type": "threads.create",
        "params": {
            "input": {
                "content": [{"type": "input_text", "text": f"How many on {DIARY_DATE}?"}],
                "attachments": [],
                "quoted_text": None,
                "inference_options": {},
            }
        },
    }
    with httpx.Client(timeout=60) as client, client.stream(
        "POST",
        f"{spine['APP_URL']}/chatkit",
        content=json.dumps(request),
        headers={"Content-Type": "application/json"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in response.headers.get("cache-control", "")
        body = "".join(response.iter_text())

    # ChatKit frames carry `data:` with the event type inside the payload, and no `event:` name.
    assert body.startswith("data:"), f"not an SSE stream: {body[:200]}"
    assert '"type":"thread.created"' in body.replace(" ", "")
    assert MARKER in body, "the simulated assistant's reply never reached the client"
    assert f"of {EXPECTED_BOOKED} booked" in body, (
        "the reply is not grounded in the fake Principle's diary, so the tool did not run"
    )


def test_a_thread_is_created_and_survives_in_the_shared_database(
    spine: dict[str, str],
) -> None:
    """The conversation must be written by the web process and readable afterwards.

    Two processes over one SQLite file is the arrangement most likely to work in tests and fail in
    service; this is the chat half of that check.
    """
    request = {
        "op": "threads.list",
        "type": "threads.list",
        "params": {"limit": 10, "order": "desc"},
    }
    response = httpx.post(
        f"{spine['APP_URL']}/chatkit",
        content=json.dumps(request),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    assert response.status_code == 200, response.text[:400]
    listed = response.json()
    assert "data" in listed, listed
