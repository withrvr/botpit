"""Ollama native ``/api/chat`` adapter: the only one with per-request ``keep_alive``/``num_ctx``/``think``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.client import (
    CompletionRequest,
    CompletionResult,
    http_complete,
    parsed,
    strip_think_tags,
)

if TYPE_CHECKING:
    import httpx


class OllamaNativeAdapter:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        timeout_s: float,
        *,
        owns_client: bool = False,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client
        # The registry shares one httpx client per base_url across adapters and
        # owns its lifetime; a standalone adapter may take ownership explicitly.
        self.owns_client = owns_client
        self.timeout_s = timeout_s
        # Registry-level defaults; a request that sets its own wins.
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive

    def body(self, req: CompletionRequest) -> dict[str, Any]:
        options = {
            "temperature": req.temperature,
            "seed": req.seed,
            "num_predict": req.max_tokens,
            "num_ctx": req.num_ctx if req.num_ctx is not None else self.num_ctx,
        }
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": False,
            "options": {k: v for k, v in options.items() if v is not None},
        }
        if req.json_schema is not None:
            body["format"] = req.json_schema
        keep_alive = req.keep_alive if req.keep_alive is not None else self.keep_alive
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        if req.think is not None:
            body["think"] = req.think
        return body

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        return await http_complete(
            self.client,
            f"{self.base_url}/api/chat",
            self.body(req),
            model=req.model,
            timeout_s=self.timeout_s,
            parse=_parse,
        )

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()


@parsed
def _parse(data: dict[str, Any], latency_ms: int) -> CompletionResult:
    message = data["message"]
    text, reasoning = strip_think_tags(str(message["content"]))
    return CompletionResult(
        text=text,
        model=str(data.get("model") or ""),
        latency_ms=latency_ms,
        prompt_tokens=data.get("prompt_eval_count"),
        completion_tokens=data.get("eval_count"),
        reasoning=message.get("thinking") or reasoning,
        finish_reason=data.get("done_reason"),
    )
