# Phase 5 — Agent client, swappable backend

Status: done (PR #10, same branch as Phases 3 and 4), 2026-09-12.

Historical snapshot of `backend/app/llm/` and `AgentController` as they
stood when this phase completed. Not updated for later changes — see
`docs/architecture/latest_architecture.md` for the current state, and
`docs/agents.md` for the living documentation of the LLM client and
controller.

## What was built

- **`app.llm.client`**: one `LLMClient` protocol
  (`async complete(request) -> result`, `async aclose()`), a shared error
  hierarchy (`LLMError(retriable=...)`, `LLMTimeoutError`,
  `LLMBadResponseError`), think-tag stripping for models that inline
  `<think>...</think>` in their output, and a metrics hook
  (`record_llm_call`) that appends to the current wide event's `llm.calls`
  list.
- **Two adapters**: `OllamaNativeAdapter` (`POST {base}/api/chat`, supports
  `format`/`options.{temperature,seed,num_predict,num_ctx}`/`keep_alive`/
  `think`) and `OpenAICompatAdapter` (`POST {base}/v1/chat/completions`,
  supports `response_format={"type":"json_schema",...,"strict":true}`).
  Nothing above the adapter boundary knows which one is in use — the
  interface is deliberately "an OpenAI-compatible or Ollama-compatible
  completion endpoint at a base URL" per `DECISIONS.md` §0.5, not
  "Ollama specifically."
- **`ModelRegistry.from_settings(settings)`**: builds one `ModelConfig` per
  entry in `LLM_MODELS`, with `LLM_MODEL_OVERRIDES` (JSON in the
  environment) able to patch any field per model — this is what lets a
  future server tier point one specific model at its own dedicated engine
  without a code change.
- **`FakeLLMClient`**: an in-memory implementation of the same protocol for
  tests — takes a list of canned responses or a callable, records every
  request, and splits `<think>` tags like the real adapters so tests
  exercise the same parsing path production traffic does.
- **`AgentController`**: the LLM-backed `SeatController` (Phase 3's
  interface). Call one (`decide`) builds the move prompt, calls the
  client, publishes the model's reasoning as `agent.reasoning`
  (privileged-visibility) *before* returning the move — so a privileged
  focus view can show the reasoning landing before the move itself does,
  per `DECISIONS.md` §10's specified ordering. A malformed reply triggers
  a bounded repair loop (feeding the parse error back as `last_error`) up
  to `max_repairs` times before raising `ControllerError`, which lets the
  referee fall back per Phase 3's turn loop. Call two (`say`), after the
  move lands, optionally produces table chat; failures here are swallowed
  outright, since chat must never affect the game.

## Key implementation decisions made during this phase

- **Two calls, not one.** `DECISIONS.md` §5 registers the two-call
  structure; this phase is where the split became concrete: call one must
  complete (reasoning + a legal move) before the referee applies anything,
  and call two only happens *after* the move is already committed — so a
  chat failure genuinely cannot roll back or block a move.
- **Repair loop lives in the controller, not the client.** `LLMError`s
  (timeouts, 5xx, connection failures) are retried by
  `with_retries` inside the client layer; a syntactically-valid-JSON-but-
  illegal-move reply is a different failure mode entirely and is repaired
  by feeding the specific parse/validation error back into the next
  prompt attempt — conflating the two would have made the retry policy
  much harder to reason about.
- **Registry-level defaults, request-level overrides.** `num_ctx` and
  `keep_alive` come from the registry by default but a specific request
  can override either — needed because a repair-loop retry sometimes wants
  a shorter context than the original attempt, without changing every
  other call to that model.
- **The live-model calibration that shaped defaults.** Measured against
  the actual dev hardware (6 GB card, four Q4_K_M models): a full agent
  turn per model, cold, ranged 8-14 seconds (gemma3:4b 10s, llama3.1:8b
  14s, qwen3:4b 8s with thinking off, phi4-mini 8s). All four models chose
  a legal move; gemma needed the discriminator-required schema fix and
  qwen needed the echo-field-tolerant legality check — both real,
  model-specific quirks discovered and fixed during this phase, not
  theoretical concerns.

## Module layout introduced

```
backend/app/llm/
├── client.py       # LLMClient protocol, CompletionRequest/Result, errors, with_retries
├── ollama.py         # OllamaNativeAdapter
├── openai_compat.py  # OpenAICompatAdapter
├── registry.py        # ModelRegistry, ModelConfig, LLM_MODEL_OVERRIDES
├── fake.py             # FakeLLMClient
└── health.py            # ported from Phase 1, probe_health / require_healthy_or_raise

backend/app/agents/
├── persona.py       # Persona, PERSONAS, get_persona (Phase 4)
├── prompt.py          # build_move_prompt, build_chat_prompt (Phase 4)
└── controller.py       # AgentController (this phase)
```

## Tests and what was verified

Live tests (`tests/live/test_agent_turn_live.py`, marker `-m ollama`,
deselected by default) played one real turn per configured model against
the actual Windows-hosted Ollama, confirming a legal move from all four
models and surfacing the two model-specific quirks noted above. Unit tests
covered the client protocol via `FakeLLMClient` (including injected
timeouts and retry behavior) and the controller's repair loop and
reasoning-before-move publish ordering. These tests are counted within the
combined Phase 3/4/5 total reported in `PROGRESS.md` rather than broken out
separately per phase.
