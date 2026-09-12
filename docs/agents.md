# Agents

<!-- Phase 5 fills in the client adapters, prompts and turn loop. -->

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
