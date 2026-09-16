from __future__ import annotations

import pytest

from app.core.config import Settings
from app.llm import ModelRegistry, OllamaNativeAdapter, OpenAICompatAdapter


def _registry(settings: Settings, **update: object) -> ModelRegistry:
    return ModelRegistry.from_settings(
        settings.model_copy(
            update={
                "LLM_MODELS": ["a:1b", "b:1b", "c:1b"],
                "LLM_BASE_URL": "http://ollama:11434/",
                "LLM_DEFAULT_MODEL": "b:1b",
                **update,
            }
        )
    )


def test_from_settings_defaults(settings: Settings) -> None:
    reg = _registry(settings)
    assert reg.models == ["a:1b", "b:1b", "c:1b"]
    assert reg.default_model == "b:1b"
    cfg = reg.config_for("a:1b")
    assert cfg.provider == "ollama_native"
    assert cfg.base_url == "http://ollama:11434"
    assert cfg.num_ctx == settings.LLM_NUM_CTX
    assert cfg.keep_alive == settings.LLM_KEEP_ALIVE
    assert cfg.timeout_s == settings.LLM_TIMEOUT_SECONDS


async def test_one_http_client_per_base_url(settings: Settings) -> None:
    reg = _registry(
        settings,
        LLM_MODEL_OVERRIDES={
            "c:1b": {
                "provider": "openai_compat",
                "base_url": "http://llm-c:8080/v1",
                "num_ctx": 1,
            }
        },
    )
    a, b, c = (reg.client_for(m) for m in ("a:1b", "b:1b", "c:1b"))
    assert isinstance(a.inner, OllamaNativeAdapter)
    assert isinstance(b.inner, OllamaNativeAdapter)
    assert isinstance(c.inner, OpenAICompatAdapter)
    assert a.client is b.client
    assert a.client is not c.client
    assert a.num_ctx == settings.LLM_NUM_CTX
    assert a.keep_alive == settings.LLM_KEEP_ALIVE
    assert c.base_url == "http://llm-c:8080/v1"
    assert reg.config_for("c:1b").num_ctx == 1
    assert reg.client_for("a:1b") is a

    assert a._scheduler is b._scheduler  # same base_url, shared affinity scheduler
    assert a._scheduler is not c._scheduler

    await reg.aclose()
    assert a.client.is_closed
    assert c.client.is_closed


def test_unknown_model(settings: Settings) -> None:
    reg = _registry(settings)
    with pytest.raises(KeyError, match=r"unknown model 'zzz'; known: a:1b, b:1b, c:1b"):
        reg.config_for("zzz")
    with pytest.raises(KeyError):
        reg.client_for("zzz")


def test_bad_override_key_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="extra"):
        _registry(settings, LLM_MODEL_OVERRIDES={"a:1b": {"nope": 1}})


async def test_adapter_aclose_does_not_close_shared_client(settings: Settings) -> None:
    reg = ModelRegistry.from_settings(settings)
    a = reg.client_for(settings.LLM_MODELS[0])
    b = reg.client_for(settings.LLM_MODELS[1])
    await a.aclose()
    assert not b.client.is_closed  # shared per base_url, registry-owned
    await reg.aclose()
    assert b.client.is_closed
