"""Warning relevance and exact counts from synthetic appointment evidence."""
import json
from collections.abc import Iterator
from typing import Any, Literal

import httpx2 as httpx
import pytest
from agents import Agent, RunConfig, Runner
from agents.tool_context import ToolContext
from pydantic import BaseModel

from dental_practice_admin.chat import INSTRUCTIONS, model_for
from dental_practice_admin.config import Settings
from dental_practice_admin.principle import PrincipleClient, PrincipleError, api_tools
from tests.fake import FakeStore, seed, transport

WINDOW = {"from": "2026-09-27T11:00:00Z", "to": "2026-09-28T11:00:00Z"}


@pytest.fixture
def appointments() -> Iterator[FakeStore]:
    store = seed(appointments_per_day=23, days=1)
    store.db.execute("UPDATE appointments SET status = 'scheduled'")
    for index in range(18, 23):
        store.db.execute("UPDATE appointments SET patient_id = ? WHERE id = ?",
                         (f"fake-patient-{index - 18:03d}", f"fake-appointment-{index:03d}"))
    store.db.commit()
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize("page_size", [20, 100])
async def test_complete_appointment_walk_supports_both_counts(
    appointments: FakeStore, fake_settings: Settings, page_size: int,
) -> None:
    async with PrincipleClient(fake_settings, transport=transport(appointments)) as api:
        rows = [row async for row in api.rows(
            "listAppointmentsByDateRange", query=WINDOW, page_size=page_size)]
    assert len(rows) == 23
    assert len({row["patientId"] for row in rows}) == 18


async def test_failed_later_page_cannot_return_a_completed_walk(
    appointments: FakeStore, fake_settings: Settings,
) -> None:
    underlying = transport(appointments)

    async def respond(request: httpx.Request) -> httpx.Response:
        if "offsetId" in request.url.params:
            return httpx.Response(503, json={"message": "fake outage"})
        return await underlying.handle_async_request(request)

    async with PrincipleClient(fake_settings, transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(PrincipleError):
            _ = [row async for row in api.rows(
                "listAppointmentsByDateRange", query=WINDOW, page_size=20)]


async def page_evidence(settings: Settings, store: FakeStore, size: int) -> list[dict[str, Any]]:
    tools = {t.name: t for t in await api_tools(settings, transport(store))}
    tool = tools["listAppointmentsByDateRange"]
    context = ToolContext(context=None, tool_name=tool.name,
                          tool_call_id="fake-call", tool_arguments="{}")
    arguments = {**WINDOW, "limit": size}
    pages = []
    while True:
        page = json.loads(await tool.on_invoke_tool(context, json.dumps(arguments)))
        pages.append(page)
        cursor = page["result"]["meta"].get("nextOffsetId")
        if not cursor:
            return pages
        arguments["offsetId"] = cursor


async def test_generated_pages_preserve_evidence_without_inventing_coverage(
    appointments: FakeStore, fake_settings: Settings,
) -> None:
    pages = await page_evidence(fake_settings, appointments, 20)
    assert [len(p["result"]["data"]) for p in pages] == [20, 3]
    assert all(set(p) == {"result"} for p in pages)
    assert pages[0]["result"]["meta"]["nextOffsetId"]


class Answer(BaseModel):
    """Claim-level conclusions, independent of the assistant's choice of prose."""

    appointments: int | None
    patients: int | None
    population: int | None
    warning_claims: list[Literal["appointments", "patients", "population", "practitioner_names"]]
    explanation: str


@pytest.mark.llm
@pytest.mark.parametrize("scenario", [
    "complete", "paged", "page_failure", "limited_search", "irrelevant_search",
    "missing_names_count", "missing_names_breakdown",
])
async def test_real_model_warns_only_about_affected_claims(
    appointments: FakeStore, fake_settings: Settings, scenario: str,
) -> None:
    pages = await page_evidence(fake_settings, appointments, 20 if scenario != "complete" else 100)
    question = "How many booked appointments and distinct patients are in the requested range?"
    evidence: object = pages
    expected_counts: tuple[int | None, int | None] = (23, 18)
    expected_warnings: set[str] = set()
    if scenario == "page_failure":
        evidence = {"pages": pages[:1], "next_page": {"error": "HTTP 503; retrieval failed"}}
        expected_counts = (None, None)
        expected_warnings = {"appointments", "patients"}
    elif scenario in {"limited_search", "irrelevant_search"}:
        search = {"operation": "searchPatients", "result": {"data": [
            {"id": f"fake-patient-{index:03d}"} for index in range(18)]}}
        evidence = {"appointments": pages, "patient_search": search}
        if scenario == "limited_search":
            question = "How many patients are registered in the whole practice?"
            evidence = search
            expected_counts = (None, None)
            expected_warnings = {"population"}
    elif scenario.startswith("missing_names"):
        appointments.db.execute("DELETE FROM practitioners")
        appointments.db.commit()
        async with PrincipleClient(fake_settings, transport=transport(appointments)) as api:
            names = [row async for row in api.rows("listPractitioners")]
        evidence = {"appointments": pages, "practitioners": names}
        question = "How many booked appointments are there?"
        expected_counts = (23, None)
        if scenario == "missing_names_breakdown":
            question += " Break them down by practitioner name."
            expected_warnings = {"practitioner_names"}
    settings = Settings()
    assert settings.openai_api_key.get_secret_value()
    agent = Agent(name="Synthetic warning acceptance", instructions=INSTRUCTIONS,
                  model=model_for(settings), output_type=Answer)
    result = await Runner.run(agent,
        question + "\nThe following are captured tool results from a synthetic practice. "
        "Assess this evidence; no further retrieval is available in this acceptance scenario. "
        "Return complete totals only in the numeric fields; use null for unrequested or "
        "unestablished totals. warning_claims identifies requested claims with a remaining "
        "problem, not general API limitations.\n" + json.dumps(evidence),
        max_turns=None, run_config=RunConfig(tracing_disabled=True))
    answer = result.final_output_as(Answer)
    assert (answer.appointments, answer.patients) == expected_counts
    assert answer.population is None
    assert set(answer.warning_claims) == expected_warnings
