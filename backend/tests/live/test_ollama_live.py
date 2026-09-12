"""Real Ollama round-trip per configured model. Run: uv run pytest -m ollama tests/live"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.config import settings
from app.llm import CompletionRequest, Message, ModelRegistry

log = logging.getLogger("tests.live")
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.mark.ollama
@pytest.mark.parametrize("model", settings.LLM_MODELS)
async def test_pong(model: str) -> None:
    reg = ModelRegistry.from_settings(settings)
    try:
        result = await reg.client_for(model).complete(
            CompletionRequest(
                model=model,
                messages=(
                    Message(role="system", content="Reply with JSON only."),
                    Message(role="user", content='Reply with {"answer": "pong"}.'),
                ),
                json_schema=SCHEMA,
                temperature=0.0,
                max_tokens=256,
            )
        )
    finally:
        await reg.aclose()
    log.warning(
        "%s: %d ms, tokens %s/%s, answer=%r reasoning=%s",
        model,
        result.latency_ms,
        result.prompt_tokens,
        result.completion_tokens,
        result.text,
        bool(result.reasoning),
    )
    assert isinstance(json.loads(result.text)["answer"], str)
    assert result.latency_ms > 0
