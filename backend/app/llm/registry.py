"""Model name -> (adapter, base_url, options); one ``httpx.AsyncClient`` per base_url."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import field_validator

from app.llm.client import CompletionRequest, CompletionResult, Frozen, LLMClient
from app.llm.ollama import OllamaNativeAdapter
from app.llm.openai_compat import OpenAICompatAdapter
from app.llm.scheduler import ModelAffinityScheduler
from app.metrics import note_llm_call

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


class _ScheduledClient:
    """Wraps a real ``LLMClient`` with model-affinity admission control
    (Phase 16, DECISIONS.md §13, ``app.llm.scheduler``) -- transparent to
    every caller. ``AgentController`` and every existing test that
    constructs a controller directly with a raw client (``FakeLLMClient``
    included) never need to know this exists; only clients handed out by
    ``ModelRegistry.client_for`` go through it.

    Also where ``note_llm_call`` (the swap counter, Phase 13) actually
    fires -- at real dispatch time, after the scheduler admits the call,
    not when a caller merely *wants* to call. Firing it any earlier would
    count a "swap" for every model-alternating request the scheduler
    exists to reorder around, silently making the metric blind to whether
    the scheduler is actually working.

    ``inner`` (the wrapped adapter) is a public attribute, not private --
    tests that need to reach past the wrapper (``isinstance(x.inner,
    OllamaNativeAdapter)``, checking which shared ``httpx.AsyncClient`` an
    adapter holds) are legitimate, so ``__getattr__`` also transparently
    forwards any attribute this class doesn't define itself (``.client``,
    ``.num_ctx``, ``.keep_alive``, ...) straight to ``inner``.
    """

    def __init__(self, inner: LLMClient, scheduler: ModelAffinityScheduler) -> None:
        self.inner = inner
        self._scheduler = scheduler
        self.base_url = inner.base_url

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        await self._scheduler.acquire(req.model)
        note_llm_call(self.base_url, req.model)
        try:
            return await self.inner.complete(req)
        finally:
            await self._scheduler.release(req.model)

    async def aclose(self) -> None:
        await self.inner.aclose()


class ModelRegistry:
    def __init__(
        self,
        configs: Iterable[ModelConfig],
        default_model: str,
        *,
        max_loaded_models: int = 2,
        scheduler_max_wait_s: float = 5.0,
    ) -> None:
        self._configs = {c.model: c for c in configs}
        self.default_model = default_model
        self._max_loaded_models = max_loaded_models
        self._scheduler_max_wait_s = scheduler_max_wait_s
        self._http: dict[str, httpx.AsyncClient] = {}
        self._schedulers: dict[str, ModelAffinityScheduler] = {}
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
        return cls(
            configs,
            settings.LLM_DEFAULT_MODEL,
            max_loaded_models=settings.LLM_MAX_LOADED_MODELS,
            scheduler_max_wait_s=settings.LLM_SCHEDULER_MAX_WAIT_SECONDS,
        )

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
        # Synchronous on purpose: no await between check and insert, so concurrent
        # tasks cannot interleave here (asyncio runs one task at a time).
        if model not in self._clients:
            cfg = self.config_for(model)
            http = self._http.setdefault(cfg.base_url, httpx.AsyncClient())
            scheduler = self._schedulers.setdefault(
                cfg.base_url,
                ModelAffinityScheduler(
                    self._max_loaded_models, max_wait_s=self._scheduler_max_wait_s
                ),
            )
            inner: LLMClient = (
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
            self._clients[model] = _ScheduledClient(inner, scheduler)
        return self._clients[model]

    async def aclose(self) -> None:
        for http in self._http.values():
            await http.aclose()
        self._http.clear()
        self._clients.clear()
