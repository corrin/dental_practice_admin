"""A bounded real-model check using only synthetic text, independent of patient records."""

import pytest
from openai import AsyncOpenAI
from openai.types.responses import FunctionToolParam

from dental_practice_admin.config import load_settings
from dental_practice_admin.principle import api_tools

pytestmark = pytest.mark.llm


async def test_real_model_accepts_the_configured_model_and_wire_format() -> None:
    settings = load_settings()
    key = settings.openai_api_key.get_secret_value()
    assert key and "fake" not in key.lower(), "Live verification needs a real OPENAI_API_KEY"
    assert settings.openai_base_url.rstrip("/") == "https://api.openai.com/v1", (
        "Live verification requires the real OpenAI endpoint")
    generated = await api_tools(settings, None)
    selected = next(tool for tool in generated if not tool.params_json_schema["properties"])
    definitions = [
        FunctionToolParam(
            type="function",
            name=tool.name,
            description=tool.description,
            parameters=tool.params_json_schema,
            strict=False,
        )
        for tool in generated
    ]
    async with AsyncOpenAI(
        api_key=key, base_url="https://api.openai.com/v1", timeout=30, max_retries=0
    ) as client:
        response = await client.responses.create(
            model=settings.agent_model,
            input="Synthetic schema check. Request the selected tool; do not answer from memory.",
            tools=definitions,
            tool_choice={"type": "function", "name": selected.name},
            max_output_tokens=512,
            store=False,
        )
    calls = [item for item in response.output if item.type == "function_call"]
    assert len(calls) == 1
    assert calls[0].name == selected.name
