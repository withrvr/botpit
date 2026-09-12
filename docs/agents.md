# Agents

<!-- Phase 5: prompts and the turn loop still to come. -->

## Health probe

`app.llm.probe_health(settings)` answers one question for the startup gate
and the admin "LLM" panel: is the backend reachable, and are the models in
`LLM_MODELS` installed there? It never raises on network errors; it returns an
`LLMHealth` with `reachable=False` and `error` set instead. The probe uses a
timeout of `min(LLM_TIMEOUT_SECONDS, 5)` so a dead backend fails fast.

| `LLM_PROVIDER`   | Probed with                                   | Model names from   | Version from                |
| ---------------- | --------------------------------------------- | ------------------ | --------------------------- |
| `ollama_native`  | `GET {LLM_BASE_URL}/api/tags`                 | `models[].name`    | `GET /api/version` (optional) |
| `openai_compat`  | `GET {LLM_BASE_URL}/v1/models` (base may already end in `/v1`) | `data[].id` | none |

Model matching: a configured `qwen3:4b` must be installed as exactly `qwen3:4b`;
a configured name without a tag (`qwen3`) also matches `qwen3:latest`.

`LLMHealth.ok` is `reachable and not models_missing`. `summary()` is the
one-line form the log and the admin panel show, e.g.
`ollama_native @ http://localhost:11434: reachable, 4/4 models present`.

Startup calls `require_healthy_or_raise(settings)`. With
`LLM_HEALTH_REQUIRED_AT_STARTUP=true` (the dev default) a not-ok probe raises
`LLMHealthError` and the process does not start; with `false` it logs a
warning and continues, so staging can boot before its GPU pod is up.

## LLM client

`app.llm` exposes one protocol and two adapters; nothing above the adapter
knows which backend is in use (DECISIONS.md §0.5, `docs/local-model-setup.md` §4).

```python
class LLMClient(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResult: ...
    async def aclose(self) -> None: ...
```

`CompletionRequest`: `model`, `messages` (tuple of `Message(role, content)`),
`json_schema`, `temperature=0.7`, `seed`, `max_tokens`, `think`, `num_ctx`,
`keep_alive`. `CompletionResult`: `text`, `model`, `latency_ms`,
`prompt_tokens`, `completion_tokens`, `reasoning` (model thinking, privileged
views only), `finish_reason`. Both are frozen pydantic models.

| Adapter                | Endpoint                          | Supports                                                                                           |
| ---------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------- |
| `OllamaNativeAdapter`  | `POST {base}/api/chat`            | `format` (schema), `options.{temperature,seed,num_predict,num_ctx}`, `keep_alive`, `think`; reads `message.thinking` |
| `OpenAICompatAdapter`  | `POST {base}/v1/chat/completions` (base may already end in `/v1`) | `temperature`, `seed`, `max_tokens`, `response_format={"type":"json_schema",...,"strict":true}`; reads `message.reasoning` / `reasoning_content`, else splits inline `<think>` tags |

`num_ctx`, `keep_alive` and `think` are ignored by the OpenAI-compatible
adapter: bake them into the server (Modelfile, llama-server flags). Unset
request fields are omitted from the wire body. The Ollama adapter takes
registry-level `num_ctx`/`keep_alive` defaults; a request that sets its own
wins.

**Registry.** `ModelRegistry.from_settings(settings)` builds one
`ModelConfig(model, provider, base_url, num_ctx, keep_alive, timeout_s)` per
entry of `LLM_MODELS` from `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_NUM_CTX`,
`LLM_KEEP_ALIVE`, `LLM_TIMEOUT_SECONDS`. `LLM_MODEL_OVERRIDES` (JSON in env)
patches any of those per model, so a server tier can point one model at its
own engine:

```
LLM_MODEL_OVERRIDES={"qwen3:4b": {"provider": "openai_compat", "base_url": "http://llm-qwen:8080/v1"}}
```

`client_for(model)` returns the adapter, sharing one `httpx.AsyncClient` per
base URL (created lazily); `config_for(model)`, `models`, `default_model`
(`LLM_DEFAULT_MODEL`). Unknown model -> `KeyError` listing the known names.
`aclose()` closes every HTTP client.

**Errors and retries.** `LLMError(retriable=...)` is the base; `LLMTimeoutError`
(retriable) for `httpx.TimeoutException`, `LLMBadResponseError` (not
retriable) for non-JSON or malformed bodies. Connection failures and HTTP 5xx
/ 429 are retriable `LLMError`s, other 4xx are not.
`with_retries(fn, attempts=3, base_delay=0.2, sleep=asyncio.sleep)` retries
retriable errors with exponential backoff (0.2s, 0.4s, ...) and re-raises
everything else at once.

**Metrics.** Every call (success or failure) appends
`{model, latency_ms, prompt_tokens, completion_tokens, ok, error}` to the
current wide event's `llm.calls` list via `record_llm_call`; no-op outside a
wide event.

**Fake.** `FakeLLMClient(["text", LLMTimeoutError("slow"), ...])` or
`FakeLLMClient(lambda req: "...")` implements the protocol in memory, records
every request in `.requests`, optionally sleeps `latency_s`, and splits
`<think>` tags like the real adapters. Used by unit tests and the
AgentController tests.

**Live check.** `uv run pytest -m ollama tests/live` makes one real
`/api/chat` call per configured model with a tiny JSON schema (deselected by
default).
