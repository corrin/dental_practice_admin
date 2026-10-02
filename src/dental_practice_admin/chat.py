"""Staff chat: a ChatKit server driving the OpenAI Agents SDK over our own operations.

The agent uses the shared API, Firestore and script integrations. Browser work uses the pinned
Playwright MCP server, with an inner browser agent supplying the last fallback.

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

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib.metadata import distribution

from agents import Agent, RunConfig, Runner, function_tool
from agents.models.interface import Model
from agents.models.openai_responses import OpenAIResponsesModel
from chatkit.agents import AgentContext, ThreadItemConverter, stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import ThreadItem, ThreadMetadata, ThreadStreamEvent, UserMessageItem
from httpx2 import AsyncBaseTransport
from openai import AsyncOpenAI

from dental_practice_admin.auth import StaffUser
from dental_practice_admin.automation import tools as automation_tools
from dental_practice_admin.chat_store import SqliteChatStore
from dental_practice_admin.config import ConfigurationError, Settings
from dental_practice_admin.principle import PrincipleClient, api_tools
from dental_practice_admin.tasks import PRACTICE_TZ, DiaryReport, daily_diary

# How much history the agent is given. Bounded because a year of chat is neither affordable nor
# useful; the whole conversation stays in the store either way.
HISTORY_ITEMS = 100

INSTRUCTIONS = """
You help staff at a dental practice with administrative questions about their Principle Dental
records. You can read and update records through the configured practice integrations.

Be brief and concrete. Staff are busy and mid-task.

Warn only about evidenced problems affecting requested claims; resolve gaps before answering.
Explain what remains missing and which claim it affects, rather than repeating a partial label.
Absent labels do not prove completeness; irrelevant missing fields do not invalidate a count.
Use diary tools for daily reports. For range totals, use run_script with services.api.rows to
finish pagination before calculating; failed remaining pages prevent complete totals.
Count unique patients from patient IDs on complete appointments, without a directory search.
Generated API results can be single pages or limited searches: neither page size nor meta.total
is a population count. searchPatients cannot establish the whole practice's patient population.
Individual records are not inherently partial. Staging is neutral context, not a quality warning.

Prefer the official API, proven Playwright scripts, verified Firestore reads, then AI browsing.
Before changing a record, state what will change and check it against the request. Afterwards,
read saved state back. After an uncertain write inspect state before retrying or changing route.
Never write directly to Firestore. Report partial or uncertain outcomes explicitly.
Use run_script to develop a reusable task. Python scripts define async run(services, inputs).
services.api.call(operation, arguments) and services.api.rows support API reads/writes and paging;
services.firestore.read supplies scoped reads; services.browser runs a Playwright function.
Return {"summary": str, "detail": object, "coverage": "complete" or "partial"} from Python.
Keep credentials out of script source. Drafts are trusted code, not a sandbox. Promotion requires
a reviewed PR and a release; run_task executes released tasks and never changes their schedule.
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


async def build_tools(deps: ChatDeps) -> list[object]:
    """Practice-scoped operations and trusted script execution for staff chat.

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

    return [diary_for_date, diary_for_tomorrow,
            *await api_tools(deps.settings, deps.transport),
            *automation_tools(deps.settings, deps.model)]


async def _diary(deps: ChatDeps, day: date) -> DiaryReport:
    """The same operation the scheduled command runs, against the same client."""
    async with PrincipleClient(deps.settings, transport=deps.transport) as client:
        return await daily_diary(client, day)


def _describe(report: DiaryReport) -> str:
    """The report as text for the model, with coverage stated rather than implied."""
    return json.dumps({"summary": report.summary(), **report.as_detail()})


def model_for(settings: Settings) -> Model:
    """The model to run, built from configuration.

    An explicit client when a key is configured, so `.env` is authoritative; the SDK's own
    environment lookup would otherwise be a second place to configure the same thing. Pointing
    `openai_base_url` at the fake AI is how a simulated model is selected -- the application never
    learns that it is simulated.
    """
    if not settings.openai_api_key.get_secret_value():
        raise ConfigurationError("Chat needs OPENAI_API_KEY")
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url or None,
    )
    return OpenAIResponsesModel(settings.agent_model, client)


async def build_agent(deps: ChatDeps) -> Agent[AgentContext[StaffUser]]:
    """The agent for one turn. `deps.model` is the injection point tests use."""
    knowledge = distribution("dental-practice-admin").locate_file("dental_practice_admin/knowledge")
    return Agent[AgentContext[StaffUser]](
        name="Massey Smiles Admin assistant",
        instructions=INSTRUCTIONS + "\n" + "\n".join(
            (knowledge / name).read_text(encoding="utf-8")
            for name in ("README.md", "website.md", "firestore.md")),
        model=deps.model,
        tools=await build_tools(deps),  # type: ignore[arg-type]
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
            await build_agent(self.deps),
            input=agent_input,
            context=agent_context,
            max_turns=None,
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
