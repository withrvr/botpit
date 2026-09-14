"""Provider-agnostic LLM client: request/result types, errors, retries, metrics.

Adapters (``app.llm.ollama``, ``app.llm.openai_compat``) only build a request
body and parse a response; everything else (HTTP, error mapping, timing, the
``llm.calls`` wide-event entry) lives in ``http_complete`` here.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from app.telemetry import current_event


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Message(Frozen):
    role: Literal["system", "user", "assistant"]
    content: str


class CompletionRequest(Frozen):
    model: str
    messages: tuple[Message, ...]
    json_schema: dict[str, Any] | None = None
    temperature: float = 0.7
    seed: int | None = None
    max_tokens: int | None = None
    think: bool | None = None
    num_ctx: int | None = None
    keep_alive: str | None = None


class CompletionResult(Frozen):
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    # Model "thinking". Privileged-only downstream (DECISIONS.md §9).
    reasoning: str | None = None
    finish_reason: str | None = None


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.retriable = retriable


class LLMTimeoutError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=True)


class LLMBadResponseError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=False)


class LLMClient(Protocol):
    base_url: str

    async def complete(self, req: CompletionRequest) -> CompletionResult: ...

    async def aclose(self) -> None: ...


_THINK = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)


def strip_think_tags(text: str) -> tuple[str, str | None]:
    """Split inline ``<think>...</think>`` (qwen3 on OpenAI-compatible servers)."""
    blocks = [b.strip() for b in _THINK.findall(text)]
    if not blocks:
        return text, None
    return _THINK.sub("", text).strip(), "\n".join(blocks)


async def with_retries[T](
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Retry retriable ``LLMError``s with exponential backoff (0.2s, 0.4s, ...)."""
    for attempt in range(attempts):
        try:
            return await fn()
        except LLMError as exc:
            if not exc.retriable or attempt == attempts - 1:
                raise
        await sleep(base_delay * 2**attempt)
    msg = "unreachable: attempts must be >= 1"
    raise AssertionError(msg)


def record_llm_call(
    model: str,
    latency_ms: int,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: BaseException | None = None,
) -> None:
    """Append one entry to the current wide event's ``llm.calls`` list, if any."""
    ev = current_event()
    if ev is None:
        return
    ev.add(
        "llm.calls",
        {
            "model": model,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "ok": error is None,
            "error": None if error is None else type(error).__name__,
        },
    )


type Parser = Callable[[dict[str, Any], int], CompletionResult]


async def http_complete(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    *,
    model: str,
    timeout_s: float,
    parse: Parser,
) -> CompletionResult:
    """POST ``body``, map transport/HTTP/JSON failures to ``LLMError``, record metrics."""
    started = time.perf_counter()
    try:
        result = parse(await _post_json(client, url, body, timeout_s), _ms(started))
    except LLMError as exc:
        record_llm_call(model, _ms(started), error=exc)
        raise
    record_llm_call(
        model,
        result.latency_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
    return result


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


async def _post_json(
    client: httpx.AsyncClient, url: str, body: dict[str, Any], timeout_s: float
) -> dict[str, Any]:
    try:
        resp = await client.post(url, json=body, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        msg = f"timeout after {timeout_s}s: {url}"
        raise LLMTimeoutError(msg) from exc
    except httpx.HTTPError as exc:
        msg = f"{type(exc).__name__}: {exc}"
        raise LLMError(msg, retriable=True) from exc
    status = resp.status_code
    if not 200 <= status < 300:
        msg = f"HTTP {status} from {url}: {resp.text[:200]}"
        raise LLMError(msg, retriable=status >= 500 or status == 429)
    try:
        data = resp.json()
    except ValueError as exc:
        msg = f"non-JSON response from {url}: {resp.text[:200]}"
        raise LLMBadResponseError(msg) from exc
    if not isinstance(data, dict):
        msg = f"unexpected JSON shape from {url}: {type(data).__name__}"
        raise LLMBadResponseError(msg)
    return data


def parsed[T](
    fn: Callable[[dict[str, Any], int], T],
) -> Callable[[dict[str, Any], int], T]:
    """Turn missing/mistyped keys in a response parser into ``LLMBadResponseError``."""

    def wrapper(data: dict[str, Any], latency_ms: int) -> T:
        try:
            return fn(data, latency_ms)
        except (KeyError, TypeError, IndexError, AttributeError) as exc:
            msg = f"malformed completion response: {type(exc).__name__}: {exc}"
            raise LLMBadResponseError(msg) from exc

    return wrapper
