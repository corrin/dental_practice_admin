"""Staff chat: a ChatKit server driving the OpenAI Agents SDK over our own operations.

The agent runs in this process and calls ordinary Python functions. There is no MCP hop: these
are our functions, called by our agent, for our staff.

**The model is the seam.** `build_agent` takes a `model`, and tests pass a scripted one while
everything else stays real -- the real `Runner`, the real ChatKit event conversion, the real
store, the real tools, the real Principle client against the fake Principle. Mocking higher up
would prove only that the mock was called; mocking lower down is not a supported surface.

Two rules the tools obey, both tested rather than trusted:

  * **Practice scope comes from configuration, never from the model.** A tool signature that
    accepted a practice id would let a prompt widen what it can read, and the model's arguments
    are the least trustworthy input in the system.
  * **Chat and the command line call the same function.** `daily_diary` is the operation; this
    module is a thin wrapper over it, so what the tests prove about it holds for both.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from agents import Agent, RunConfig, Runner, function_tool
from agents.models.interface import Model
from chatkit.agents import AgentContext, ThreadItemConverter, stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import ThreadItem, ThreadMetadata, ThreadStreamEvent, UserMessageItem
from httpx import AsyncBaseTransport

from principle_admin.auth import StaffUser
from principle_admin.chat_store import SqliteChatStore
from principle_admin.config import Settings
from principle_admin.principle import PrincipleClient
from principle_admin.tasks import PRACTICE_TZ, daily_diary

# How much history the agent is given. Bounded because a year of chat is neither affordable nor
# useful; the whole conversation stays in the store either way.
HISTORY_ITEMS = 100

# A runaway tool loop costs money and time. Enough turns for a question, a lookup and an answer.
MAX_TURNS = 8

INSTRUCTIONS = """
You help staff at a dental practice with administrative questions about their Principle Dental
records. You have read-only access.

Be brief and concrete. Staff are busy and mid-task.

When a report covers only part of what was asked, say so plainly and say what is missing. Never
present a partial answer as a complete one. If a tool reports partial coverage, repeat that
caveat in your reply.

You cannot change anything in Principle. If asked to, say so and describe what the person would
do in Principle itself.
""".strip()


@dataclass
class ChatDeps:
    """What a chat turn needs. Assembled per request, never global.

    `settings` carries the practice scope, so no tool needs to be told which practice it is
    working on -- and none of them accepts being told.
    """

    settings: Settings
    model: Model | str

    # The same seam `PrincipleClient` takes: production leaves it None and opens a socket, tests
    # pass the fake. Without it a chat turn is the one path in the application that cannot be
    # exercised offline.
    transport: AsyncBaseTransport | None = None


def build_tools(deps: ChatDeps) -> list[object]:
    """The tool set exposed to the agent: small, purposeful, read-only.

    Browser sessions, credentials and arbitrary execution are implementation details, not tools.
    Each function here closes over `deps`, which is how practice scope reaches it without
    passing through the model.
    """

    @function_tool
    async def diary_for_date(on_date: str) -> str:
        """Report the practice's appointments for one day.

        Args:
            on_date: The day to report, as YYYY-MM-DD in the practice's local time.

        """
        try:
            day = date.fromisoformat(on_date)
        except ValueError:
            return f"{on_date!r} is not a date in YYYY-MM-DD form."
        return _describe(await _diary(deps, day))

    @function_tool
    async def diary_for_tomorrow() -> str:
        """Report the practice's appointments for tomorrow."""
        tomorrow = datetime.now(tz=PRACTICE_TZ).date() + timedelta(days=1)
        return _describe(await _diary(deps, tomorrow))

    return [diary_for_date, diary_for_tomorrow]


async def _diary(deps: ChatDeps, day: date) -> object:
    """The same operation the scheduled command runs, against the same client."""
    async with PrincipleClient(deps.settings, transport=deps.transport) as client:
        return await daily_diary(client, day)


def _describe(report: object) -> str:
    """The report as text for the model, with coverage stated rather than implied."""
    summary = getattr(report, "summary", None)
    lines = [summary() if callable(summary) else str(report)]
    for day in getattr(report, "by_practitioner", []):
        lines.append(
            f"- {day.name}: {day.attending} attending of {day.appointments} booked"
            f" ({(day.first_from or '?')[11:16]}-{(day.last_to or '?')[11:16]})"
        )
    note = getattr(report, "coverage_note", None)
    if note:
        lines.append(
            f"INCOMPLETE: {note}. Say this in your reply; do not present it as a full day."
        )
    return "\n".join(lines)


def build_agent(deps: ChatDeps) -> Agent[AgentContext[StaffUser]]:
    """The agent for one turn. `deps.model` is the injection point tests use."""
    return Agent[AgentContext[StaffUser]](
        name="Principle admin assistant",
        instructions=INSTRUCTIONS,
        model=deps.model,
        tools=build_tools(deps),  # type: ignore[arg-type]
    )


class StaffChatServer(ChatKitServer[StaffUser]):
    """Turns a staff message into an agent run, and the run into ChatKit events."""

    def __init__(self, store: SqliteChatStore, deps: ChatDeps) -> None:
        super().__init__(store)
        self.chat_store = store
        self.deps = deps

    async def respond(
        self,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: StaffUser,
    ) -> AsyncIterator[ThreadStreamEvent]:
        """Stream one assistant turn.

        The conversation is reloaded from the store rather than accumulated in memory: a staff
        member may have two tabs open, and the store is the only shared truth.
        """
        page = await self.chat_store.load_thread_items(
            thread.id, after=None, limit=HISTORY_ITEMS, order="desc", context=context
        )
        history: list[ThreadItem] = list(reversed(page.data))

        agent_context = AgentContext[StaffUser](
            thread=thread, store=self.chat_store, request_context=context
        )
        agent_input = await ThreadItemConverter().to_agent_input(history)
        result = Runner.run_streamed(
            build_agent(self.deps),
            input=agent_input,
            context=agent_context,
            max_turns=MAX_TURNS,
            # Traces would send conversation content to OpenAI's dashboard. Patient-adjacent
            # material does not leave this practice beyond the inference call itself.
            run_config=RunConfig(tracing_disabled=True),
        )
        try:
            async for event in stream_agent_response(agent_context, result):
                yield event
        finally:
            # An abandoned stream -- a closed tab, a dropped VPN -- leaves the run consuming
            # tokens until it finishes on its own.
            result.cancel()
