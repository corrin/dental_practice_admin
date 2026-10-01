"""A bounded real-model check using only synthetic text, independent of patient records."""

import pytest
from openai import AsyncOpenAI

from dental_practice_admin.config import Settings

pytestmark = pytest.mark.llm


async def test_real_model_accepts_the_configured_model_and_wire_format() -> None:
    settings = Settings()
    key = settings.openai_api_key.get_secret_value()
    assert key and "fake" not in key.lower(), "Live verification needs a real OPENAI_API_KEY"
    assert settings.openai_base_url.rstrip("/") in {
        "",
        "https://api.openai.com/v1",
    }, "Live verification requires the real OpenAI endpoint"
    async with AsyncOpenAI(
        api_key=key, base_url="https://api.openai.com/v1", timeout=30, max_retries=0
    ) as client:
        response = await client.responses.create(
            model=settings.agent_model,
            input="This is a synthetic connectivity check. Reply with the single word READY.",
            max_output_tokens=128,
            store=False,
        )
    assert response.output_text.strip(), "Real model returned no usable text"
