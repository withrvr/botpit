"""``/v1/chat/completions`` adapter: llama-server, vLLM, Ollama's OpenAI shim.

``num_ctx``/``keep_alive``/``think`` are ignored here: bake them into the
server config (Modelfile, llama-server flags). See docs/local-model-setup.md §2.4.
"""

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


class OpenAICompatAdapter:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        timeout_s: float,
        *,
        owns_client: bool = False,
    ) -> None:
        base = base_url.rstrip("/")
        self.base_url = base if base.endswith("/v1") else f"{base}/v1"
        self.client = client
        self.timeout_s = timeout_s
        # The registry shares one httpx client per base_url and owns its lifetime.
        self.owns_client = owns_client

    def body(self, req: CompletionRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "temperature": req.temperature,
            "stream": False,
        }
        if req.seed is not None:
            body["seed"] = req.seed
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        if req.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "move",
                    "schema": req.json_schema,
                    "strict": True,
                },
            }
        return body

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        return await http_complete(
            self.client,
            f"{self.base_url}/chat/completions",
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
    choice = data["choices"][0]
    message = choice["message"]
    text, inline = strip_think_tags(str(message.get("content") or ""))
    usage = data.get("usage") or {}
    return CompletionResult(
        text=text,
        model=str(data.get("model") or ""),
        latency_ms=latency_ms,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        reasoning=message.get("reasoning")
        or message.get("reasoning_content")
        or inline,
        finish_reason=choice.get("finish_reason"),
    )
