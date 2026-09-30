"""One chat turn, with everything real except the two external systems.

The real `Runner`, the real ChatKit event conversion, the real store, the real tool functions, the
real Principle client and the real OpenAI model class. Both external systems are simulated —
Principle by `tests/fake/`, the model by `tests/fake_ai/` — and each is reached through the real
transport, so only the socket is replaced.

What this proves that a mocked tool call could not: the tool the model names actually runs, it
reaches `daily_diary`, that reaches the Principle client, the client reaches the fake, and the
numbers that come back are the ones the fake holds. Each of those is a real seam.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from agents.models.openai_responses import OpenAIResponsesModel
from chatkit.server import StreamingResult
from openai import AsyncOpenAI
from pydantic import SecretStr

from principle_admin.auth import StaffUser
from principle_admin.chat import ChatDeps, StaffChatServer, build_tools
from principle_admin.chat_store import SqliteChatStore
from principle_admin.config import Environment, Settings
from principle_admin.tasks import daily_diary
from tests.fake import FAKE_API_KEY, FAKE_PRACTICE_ID, FakeStore, seed
from tests.fake import transport as fake_transport
from tests.fake_ai import FAKE_AI_KEY, MARKER, FakeAi, transport

STAFF = StaffUser(email="nurse@practice.nz", name="Nurse")

SEEDED_DAY = "2026-09-28"
SLOTS_PER_DAY = 8
CANCELLED_PER_DAY = 2


def fake_ai_model(instance: FakeAi) -> OpenAIResponsesModel:
    """The real OpenAI model class, talking to the fake AI over the real transport.

    The same arrangement as the Principle fake: production code is untouched and only the socket
    is replaced. The base URL is unreachable on purpose, so a request that escaped the transport
    fails loudly instead of reaching OpenAI.
    """
    client = AsyncOpenAI(
        api_key=FAKE_AI_KEY,
        base_url="http://fake-ai.invalid/v1",
        http_client=httpx2.AsyncClient(transport=transport(instance)),
    )
    return OpenAIResponsesModel("gpt-5", client)


@pytest.fixture
def chat_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment=Environment.FAKE,
        api_key=SecretStr(FAKE_API_KEY),
        practice_id=FAKE_PRACTICE_ID,
        data_root=tmp_path,
    )


@pytest.fixture
def chat_store(chat_settings: Settings) -> Iterator[SqliteChatStore]:
    store = SqliteChatStore(chat_settings.database_path)
    yield store
    store.close()


async def _turn(
    store: SqliteChatStore,
    settings: Settings,
    ai: FakeAi,
    fake_store: FakeStore | None = None,
    message: str = f"How many appointments on {SEEDED_DAY}?",
) -> str:
    """One turn through `process`, the entry point the HTTP endpoint uses.

    Driving `respond` directly would skip what ChatKit does around it -- creating the thread and
    persisting both the user message and the assistant reply -- and would quietly prove less than
    the test claims.
    """
    server = StaffChatServer(
        store,
        ChatDeps(
            settings=settings,
            model=fake_ai_model(ai),
            transport=fake_transport(fake_store if fake_store is not None else seed()),
        ),
    )
    request = json.dumps(
        {
            "op": "threads.create",
            "type": "threads.create",
            "params": {
                "input": {
                    "content": [{"type": "input_text", "text": message}],
                    "attachments": [],
                    "quoted_text": None,
                    "inference_options": {},
                }
            },
        }
    )
    result = await server.process(request, STAFF)
    if isinstance(result, StreamingResult):
        async for _event in result:
            pass
    threads = await store.load_threads(limit=1, after=None, order="desc", context=STAFF)
    assert threads.data, "no thread was created"
    return threads.data[0].id


async def test_the_model_s_tool_call_reaches_the_fake_principle(
    chat_store: SqliteChatStore, chat_settings: Settings
) -> None:
    """The whole chain runs: tool -> daily_diary -> client -> fake, and the numbers come back.

    A test that mocked the tool would prove the model was offered a function and nothing else.
    The second turn's input carries the tool's real output, so the seeded diary's numbers
    appearing there means every layer in between actually executed.
    """
    ai = FakeAi()
    await _turn(chat_store, chat_settings, ai)

    assert len(ai.requests) == 2, "expected a tool turn and then an answer turn"
    tool_output = json.dumps(ai.requests[1]["input"])
    assert f"of {SLOTS_PER_DAY} booked" in tool_output
    assert f"{SLOTS_PER_DAY - CANCELLED_PER_DAY} attending" in tool_output
    assert "Dr " in tool_output, "the practitioner breakdown should reach the model"


async def test_the_assistant_reply_is_persisted(
    chat_store: SqliteChatStore, chat_settings: Settings
) -> None:
    """A turn that is not stored is a conversation that forgets itself on reload.

    ChatKit writes items through the store during the run; if that path were broken the chat
    would appear to work and lose its history on the next page load.
    """
    thread_id = await _turn(chat_store, chat_settings, FakeAi())

    page = await chat_store.load_thread_items(
        thread_id, after=None, limit=10, order="asc", context=STAFF
    )
    assert page.data, "the assistant's reply was not written to the store"
    assert any(MARKER in str(item) for item in page.data), "no assistant reply was stored"
    assert any(f"of {SLOTS_PER_DAY} booked" in str(item) for item in page.data)


async def test_a_partial_report_reaches_the_model_labelled_as_partial(
    chat_store: SqliteChatStore, chat_settings: Settings
) -> None:
    """Coverage must survive the trip into the model's context.

    The instructions tell the model to repeat the caveat, but it can only do that if the tool
    output says so. Dropping the note here would let a confident, wrong answer be generated from
    a report that knew better.
    """
    # An appointment whose practitioner is not in the practice list: what a mid-day staffing
    # change looks like through the API.
    fake = seed()
    fake.db.execute("DELETE FROM practitioners")
    fake.db.commit()

    ai = FakeAi()
    try:
        await _turn(chat_store, chat_settings, ai, fake_store=fake)
    finally:
        fake.close()

    assert "INCOMPLETE" in json.dumps(ai.requests[1]["input"])


async def test_no_tool_accepts_a_practice_id(chat_settings: Settings) -> None:
    """Practice scope must come from configuration, never from the model.

    A tool that took a practice id would let a prompt widen what chat can read, and the model's
    arguments are the least trustworthy input in the system. This asserts the signature, because
    the absence of a parameter is the mechanism -- not a validation rule someone could relax.
    """
    tools = build_tools(ChatDeps(settings=chat_settings, model="unused"))
    for tool in tools:
        schema = getattr(tool, "params_json_schema", {})
        properties = set(schema.get("properties", {}))
        forbidden = {"practiceId", "practice_id", "practice"}
        assert not (properties & forbidden), f"{tool} accepts {properties & forbidden}"


async def test_the_tool_set_stays_small_and_read_only(chat_settings: Settings) -> None:
    """Browser sessions, credentials and arbitrary execution are not tools.

    A tool set that grows by accident is how a chat interface acquires abilities nobody decided
    to give it.
    """
    tools = build_tools(ChatDeps(settings=chat_settings, model="x"))
    names = {getattr(tool, "name", "") for tool in tools}
    assert names == {"diary_for_date", "diary_for_tomorrow"}


def test_chat_and_the_command_line_call_the_same_operation() -> None:
    """ARCHITECTURE.md's requirement, asserted rather than assumed.

    Two implementations of "the day's diary" would drift, and the one staff see in chat would
    stop matching the one the scheduled report files.
    """
    import principle_admin.chat as chat_module
    import principle_admin.tasks as tasks_module

    source = inspect.getsource(chat_module._diary)
    assert "daily_diary" in source
    assert tasks_module.daily_diary is daily_diary
    # The chat module must call the operation, not hold a reimplementation of it.
    assert inspect.getmodule(chat_module.daily_diary) is tasks_module  # type: ignore[attr-defined]
