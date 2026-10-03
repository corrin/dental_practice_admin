"""A fake OpenAI Responses API: the same treatment the fake Principle gets.

The model is the fourth external system, and testing against the real one every time is slow,
costs money, needs a key, and is non-deterministic — so it gets a simulation with its own
scripted behaviour, reached through the real transport.

One implementation, two ways in, exactly like `tests/fake/`:

    transport()                      an httpx transport for in-process tests
    uvicorn tests.fake_ai.server:app  a real HTTP server, so the application under test can be a
                                      separate process pointed at it by OPENAI_BASE_URL

**It decides, it does not replay.** Given the conversation so far it chooses what a model
plausibly would: asked a question, call the practitioner tool; handed a tool result, summarise it.
That makes a full chat turn — tool call, tool execution against the fake Principle, and an answer
grounded in the result — run end to end with no network and no cost.

What it deliberately does not do is prove anything about OpenAI. It answers in the Responses
wire format because that format was read off the SDK's own types, not because anyone verified it
against the vendor. Compatibility with the real API is what `-m llm` is for.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import urlparse

import httpx2

FAKE_AI_KEY = "fake-openai-key"

# Anything the assistant says carries this, so a reply produced by the simulation can never be
# mistaken for one a model wrote.
MARKER = "[simulated assistant]"

class FakeAiUnhandledRequestError(NotImplementedError):
    """A call the fake AI has no answer for: add it, rather than guessing."""


def decide(body: dict[str, Any]) -> list[dict[str, Any]]:
    """What the simulated model does next, from the conversation so far.

    Two rules, in order:

      1. A tool result is present -> answer, quoting the result so the answer is grounded in what
         the tool actually returned rather than in anything invented here.
      2. Otherwise -> list the practice's practitioners.
    """
    tools = {tool.get("name") for tool in body.get("tools") or []}

    outputs = _tool_outputs(body.get("input"))
    if "prepare_script" in tools and not any("/tasks/prepare/" in output for output in outputs):
        from tests.test_saved_scripts import DEFINITION, SOURCE, TESTS
        return [_function_call("prepare_script", {"source": SOURCE, "language": "python",
            "definition_json": DEFINITION.model_dump_json(), "tests": TESTS,
            "previous_draft": ""})]
    if outputs:
        return [_message(f"{MARKER} {' '.join(outputs)}")]

    if "listPractitioners" in tools:
        return [_function_call("listPractitioners", {})]
    if tools:
        raise FakeAiUnhandledRequestError(
            f"the fake AI has no behaviour for tools {sorted(tools)}; teach it in"
            " tests/fake_ai/server.py"
        )
    return [_message(f"{MARKER} nothing to look up.")]


def _tool_outputs(sent_input: Any) -> list[str]:
    """Every function_call_output in the conversation, oldest first."""
    if not isinstance(sent_input, list):
        return []
    return [
        str(item.get("output"))
        for item in sent_input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]


def _message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_" + uuid.uuid4().hex,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "fc_fake",
        "call_id": f"call_{name}",
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(arguments),
    }


def _response(output: list[dict[str, Any]], model: str) -> dict[str, Any]:
    return {
        "id": "resp_fake",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": "completed",
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def events(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The Responses stream for one turn, in the order the SDK expects to read it."""
    model = str(body.get("model") or "fake")
    output = decide(body)
    stream: list[dict[str, Any]] = [
        {
            "type": "response.created",
            "response": _response([], model),
            "sequence_number": 0,
        }
    ]
    sequence = 0
    for index, item in enumerate(output):
        sequence += 1
        stream.append(
            {
                "type": "response.output_item.added",
                "item": item,
                "output_index": index,
                "sequence_number": sequence,
            }
        )
        sequence += 1
        stream.append(
            {
                "type": "response.output_item.done",
                "item": item,
                "output_index": index,
                "sequence_number": sequence,
            }
        )
    stream.append(
        {
            "type": "response.completed",
            "response": _response(output, model),
            "sequence_number": sequence + 1,
        }
    )
    return stream


class FakeAi:
    """ASGI application answering the Responses endpoint."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            raise FakeAiUnhandledRequestError(f"http only, not {scope['type']!r}")

        chunks = bytearray()
        while True:
            message = await receive()
            chunks.extend(bytes(message.get("body") or b""))
            if not message.get("more_body"):
                break

        path = urlparse(str(scope.get("path", ""))).path
        if path == "/health" and scope["method"] == "GET":
            await self._json(send, {"status": "ok"})
            return
        if not path.endswith("/responses") or scope["method"] != "POST":
            raise FakeAiUnhandledRequestError(
                f"the fake AI serves POST /v1/responses, not {scope['method']} {path}"
            )

        body = json.loads(chunks or b"{}")
        self.requests.append(body)

        if body.get("stream"):
            await self._sse(send, events(body))
            return
        await self._json(send, _response(decide(body), str(body.get("model") or "fake")))

    async def _json(
        self,
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
        payload: dict[str, Any],
    ) -> None:
        data = json.dumps(payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(data)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": data})

    async def _sse(
        self,
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
        stream: list[dict[str, Any]],
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                ],
            }
        )
        for event in stream:
            # The SDK reads the `event:` name as well as the payload; omitting it makes every
            # event look like the same type.
            frame = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
            await send({"type": "http.response.body", "body": frame, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


app = FakeAi()


def transport(instance: FakeAi | None = None) -> httpx2.ASGITransport:
    """The fake AI where the socket would be, for the real OpenAI client to use.

    httpx2, not httpx: openai 3.x is built on httpx2, while `PrincipleClient` uses httpx. Each
    fake is installed under the client that will actually talk to it.
    """
    return httpx2.ASGITransport(app=instance if instance is not None else FakeAi())
