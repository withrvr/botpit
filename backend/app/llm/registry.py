"""Model name -> (adapter, base_url, options); one ``httpx.AsyncClient`` per base_url."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import field_validator

from app.llm.client import Frozen, LLMClient
from app.llm.ollama import OllamaNativeAdapter
from app.llm.openai_compat import OpenAICompatAdapter

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.core.config import Settings


class ModelConfig(Frozen):
    model: str
    provider: Literal["ollama_native", "openai_compat"]
    base_url: str
    num_ctx: int
    keep_alive: str
    timeout_s: float

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


class ModelRegistry:
    def __init__(self, configs: Iterable[ModelConfig], default_model: str) -> None:
        self._configs = {c.model: c for c in configs}
        self.default_model = default_model
        self._http: dict[str, httpx.AsyncClient] = {}
        self._clients: dict[str, LLMClient] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelRegistry:
        configs = []
        for name in settings.LLM_MODELS:
            fields: dict[str, Any] = {
                "model": name,
                "provider": settings.LLM_PROVIDER,
                "base_url": settings.LLM_BASE_URL,
                "num_ctx": settings.LLM_NUM_CTX,
                "keep_alive": settings.LLM_KEEP_ALIVE,
                "timeout_s": settings.LLM_TIMEOUT_SECONDS,
                **settings.LLM_MODEL_OVERRIDES.get(name, {}),
            }
            configs.append(ModelConfig(**fields))
        return cls(configs, settings.LLM_DEFAULT_MODEL)

    @property
    def models(self) -> list[str]:
        return list(self._configs)

    def config_for(self, model: str) -> ModelConfig:
        try:
            return self._configs[model]
        except KeyError:
            msg = f"unknown model {model!r}; known: {', '.join(self.models)}"
            raise KeyError(msg) from None

    def client_for(self, model: str) -> LLMClient:
        if model not in self._clients:
            cfg = self.config_for(model)
            http = self._http.setdefault(cfg.base_url, httpx.AsyncClient())
            self._clients[model] = (
                OllamaNativeAdapter(
                    cfg.base_url,
                    http,
                    cfg.timeout_s,
                    num_ctx=cfg.num_ctx,
                    keep_alive=cfg.keep_alive,
                )
                if cfg.provider == "ollama_native"
                else OpenAICompatAdapter(cfg.base_url, http, cfg.timeout_s)
            )
        return self._clients[model]

    async def aclose(self) -> None:
        for http in self._http.values():
            await http.aclose()
        self._http.clear()
        self._clients.clear()
