# botpit — Decisions

> **Status: reconstructed.** The original `DECISIONS.md` was not present in the
> repo when the build started (2026-09-12). This file reconstructs every decision
> the build prompt references by section number, keeps that numbering so the
> prompt's cross-references (§0.5, §2, §4, §5/§6, §7, §9, §10, §11, §13) still
> resolve, and folds in the prompt's §1 changes plus the owner's amendments
> from 2026-09-12. When the original turns up, merge it here; anything below
> that contradicts it loses, except where the build prompt explicitly
> supersedes the original.

Entries are RESOLVED unless marked OPEN. Dates absolute.

## 0. Scope, RESOLVED 2026-09-12

botpit is a deployable multiplayer card-game platform where human players and
LLM-driven agents sit at the same table. v1 ships Classic UNO, UNO Flip!,
UNO Show 'em No Mercy, and one structurally different card game. Every table
is a mix of human and AI seats in any ratio, including all-AI (spectator
mode) and all-human.

## 0.5 Local model serving, RESOLVED 2026-09-12 (amended)

- Project code lives in WSL2. Ollama runs natively on Windows and is reached
  from WSL at a config-driven base URL (`LLM_BASE_URL`, default
  `http://localhost:11434` via mirrored networking; `host.docker.internal`
  from inside compose). Never hardcoded.
- A startup health check probes the LLM backend and confirms every model in
  `LLM_MODELS` is present before any game can start
  (`LLM_HEALTH_REQUIRED_AT_STARTUP`).
- **Owner amendment 2026-09-12:** no new models get pulled. The dev model set
  is exactly what is installed on the Windows host: `gemma3:4b`,
  `llama3.1:8b`, `qwen3:4b`, `phi4-mini:latest` (all Q4_K_M).
- Ollama is the dev choice only. The codebase depends on "an
  OpenAI-compatible or Ollama-compatible completion endpoint at a base URL",
  not on Ollama. See `docs/local-model-setup.md`.

## 1. Environments, RESOLVED 2026-09-12 (amended)

- Three environments, one codebase: `dev`, `staging`, `production`, selected
  by `BOTPIT_ENV`, configured by `.env.<env>` via pydantic-settings. No
  `if env == ...` in business logic.
- **Owner amendment 2026-09-12:** no staging or production server is bought
  or hosted until local development, setup and testing are fully complete.
  Everything is built local-first but server-shaped: Docker Compose with
  Postgres and Redis runs locally, the Redis event bus and the Postgres
  schema are exercised locally, and the deploy workflows are written and
  dry-run locally. Hosting research (`docs/deployment.md`) stands as the
  plan for when that day comes; nothing in the code assumes a particular host.

## 2. Configuration and capacity, RESOLVED 2026-09-12

- `MAX_CONCURRENT_GAMES` starts as a config default and becomes a
  database-backed runtime setting adjustable from the admin dashboard.
  Additional caps: global connection cap, per-room seat cap (enforced at the
  API), per-room viewer cap. Overflow goes to a fair FIFO join queue.
- The original "no login system" note is **reversed**: human accounts are
  real (email/username, salted password hash, JWT sessions). Humans have no
  model field; model selection exists only for AI seats.

## 3. Deterministic referee, RESOLVED 2026-09-12

The referee owns turn order, move validation, state application and win
detection. It is deterministic given a seed and a move sequence. It never
calls a model. A seat is an abstraction: a human websocket message and an
agent's parsed completion arrive as the same validated move shape.

## 4. Game module contract, RESOLVED 2026-09-12

A game is a module satisfying one contract (`docs/engine.md`): initial state
from seed and seat count, legal moves for a seat, apply move, is-terminal,
ranking, `player_view(state, seat)` redaction, plus a rules markdown file
handed to agents. New games are added by dropping in a module and a rules
file without touching the referee, websocket layer or frontend. The fourth
v1 game is the proof.

## 5. Agent turn structure, RESOLVED 2026-09-12

An AI turn is two model calls: call one produces private reasoning plus a
move (structured JSON); call two produces optional table chat, after the
move has been validated and applied. Reasoning is never shown to other
players; it is shown live to privileged spectators/admins.

## 6. Agent memory, RESOLVED 2026-09-12

Agents are stateless between turns. Everything an agent knows is in the
prompt built from `player_view` plus recent public chat. No hidden
per-agent memory store.

## 7. Personas, RESOLVED 2026-09-12

A persona bundles skill traits (how well it plays: risk appetite, card
counting hints in the prompt) and social traits (how it chats). Traits are
prompt-level, not engine-level: the referee treats every seat identically.

## 8. Chat, RESOLVED 2026-09-12

Chat is downstream-only: it never affects game state. Human and AI chat
flow through the same public channel. Rate-limited per account.

## 9. Privacy gate, RESOLVED 2026-09-12

One function, `player_view(state, seat)`, is the only path from full game
state to anything a seat sees: the agent prompt builder and the API/websocket
payload sent to a human's browser both call it. It is a security boundary
and is tested as one (a human must not be able to see another hand via any
API or websocket payload).

## 10. Views, RESOLVED 2026-09-12

Two view modes: spectator (omniscient) and player (own seat). The
spectator/admin client can click any seat and see its hand, current
`player_view`, live reasoning (AI only), model and persona, and chat, in
real time. Ordering: reasoning streams before the move lands. The websocket
layer carries full per-seat detail to privileged subscribers and
seat-filtered events to player subscribers from one event source.

## 11. Leaderboard and roles, RESOLVED 2026-09-12

Leaderboard per human account and per model, with aggregate admin views.
RBAC: roles are bundles of permissions (many-to-many), checked centrally via
a `require_permission` dependency. Default roles: `admin`, `player`,
`spectator`, room-scoped `moderator`. Room visibility: private, unlisted,
public.

## 12. Persistence, RESOLVED 2026-09-12

The event log is the source of truth; finished games replay from it.
SQLAlchemy 2.0 async + Alembic, one models module for SQLite (dev) and
Postgres (later). Redis is never the source of truth for game state.

## 13. Model-swap-aware scheduling, RESOLVED 2026-09-12 (flagged)

Loading a model costs seconds; on a 6 GB card at most two 4B-class models
fit at once and an 8B model sits alone. The turn scheduler treats model
identity as a resource: group consecutive AI turns by model where turn
order allows, prefer tables that share a model, and surface swap counts in
metrics. Research (`docs/local-model-setup.md` §2.3) confirms keep-alive
tuning does not remove the swap cost, it only delays unloads.

## 14. Observability, RESOLVED 2026-09-12

Wide events (one context-rich JSON event per request, per agent turn, per
websocket session, per game), stdlib logging with a JSON formatter,
correlation ids, tail sampling (always keep errors and slow, sample the
rest). Prometheus `/metrics`, `/health`, `/health/llm`. Everything
inspectable locally. Reference: https://loggingsucks.com/.

## 15. Stack, RESOLVED 2026-09-12

Python 3.12, uv, ruff, mypy strict, pytest + pytest-asyncio, FastAPI with
native websockets, SQLAlchemy 2.0 async, Alembic, httpx, redis-py (Redis
Streams event bus), pydantic-settings. Auth, RBAC, rate-limiting, feature
flag and frontend libraries are chosen by research in their phases and
recorded here when decided.

## Open decisions

- OPEN: fourth v1 game (structurally different from UNO). Decide in Phase 2.
- OPEN: auth library, RBAC approach (Casbin vs custom), rate limiter,
  feature-flag storage, frontend stack. Decide in Phases 6, 7, 10.
