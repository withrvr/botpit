# Agents

<!-- Phase 5 fills in the client adapters and turn loop. -->

## Prompt structure

An AI turn is two model calls (DECISIONS.md §5), both built by
`app.agents.prompt` from a `PlayerView` only. The builders never see
`GameState`; `tests/unit/agents/test_prompt.py` checks the signatures and
greps the module, and `tests/security/test_prompt_privacy.py` fuzzes the
rendered prompts for other seats' cards.

`build_move_prompt(game, view, persona, recent_chat, last_error) -> PromptBundle`

| part | content |
|---|---|
| `system` | the game's `rules.md` verbatim, a `## Persona` block (name, risk appetite, card-counting hint, voice), and the `## Output` contract: one JSON object with `reasoning` (private, 1-3 sentences) and `move` (an entry of `legal_moves`) |
| `user` | the `PlayerView` as compact JSON (own hand, table, hand_counts, legal_moves, phase, current_seat, scores), the last 10 chat lines as `seat k: text`, and, on a retry, `Your previous move was rejected: <reason>. Choose a different move from legal_moves.` |
| `json_schema` | `{"type":"object","properties":{"reasoning":{"type":"string"},"move":<move schema>},"required":["reasoning","move"],"additionalProperties":false}` |

The move schema comes from `app.games.move_schema.move_json_schema(game)`:
the game's `move_adapter` JSON Schema with `$defs` inlined, titles stripped
and every object closed with `additionalProperties: false`, so Ollama's
`format` and OpenAI's `response_format.json_schema` both accept it as-is.
Cached per slug.

`build_chat_prompt(game, view, persona, move_description, recent_chat)` is
call two, after the move landed: optional table talk in the persona's voice,
never revealing the hand, `{"say": null}` to stay silent (frequency guided by
`chattiness`). Schema
`{"type":"object","properties":{"say":{"type":["string","null"],"maxLength":200}},"required":["say"],"additionalProperties":false}`.
Its user message carries only public state (phase, hand_counts, scores, table).

Token budget: `PROMPT_TOKEN_BUDGET = 3500` estimated as `len(text) // 4`.
Over budget, `build_move_prompt` drops chat lines oldest-first, then the
rules' `## How to think` section. A test renders every game at max seats
with full chat and asserts it fits.

## Personas

`app.agents.persona.Persona`: `name`, `risk` (`cautious | balanced |
reckless`), `counts_cards`, `voice` (one sentence), `chattiness` (0-1).
Prompt-level only (DECISIONS.md §7): the referee never reads them.
Built-ins in `PERSONAS` / `get_persona(name)`: `steady`, `gambler`,
`quiet-shark`, `chatterbox`, `grump`, `coach`.

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
