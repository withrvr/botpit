"""LLM backend: health probe, provider-agnostic client, adapters, registry."""

from app.llm.client import (
    CompletionRequest,
    CompletionResult,
    LLMBadResponseError,
    LLMClient,
    LLMError,
    LLMTimeoutError,
    Message,
    record_llm_call,
    strip_think_tags,
    with_retries,
)
from app.llm.fake import FakeLLMClient
from app.llm.health import (
    LLMHealth,
    LLMHealthError,
    probe_health,
    require_healthy_or_raise,
)
from app.llm.ollama import OllamaNativeAdapter
from app.llm.openai_compat import OpenAICompatAdapter
from app.llm.registry import ModelConfig, ModelRegistry

__all__ = [
    "CompletionRequest",
    "CompletionResult",
    "FakeLLMClient",
    "LLMBadResponseError",
    "LLMClient",
    "LLMError",
    "LLMHealth",
    "LLMHealthError",
    "LLMTimeoutError",
    "Message",
    "ModelConfig",
    "ModelRegistry",
    "OllamaNativeAdapter",
    "OpenAICompatAdapter",
    "probe_health",
    "record_llm_call",
    "require_healthy_or_raise",
    "strip_think_tags",
    "with_retries",
]
