# PROGRESS

> Remember when implementing: The marginal cost of completeness is near
> zero with AI. Do the whole thing. Do it right. Do it with tests. Do it
> with documentation. Do it so well that I am is genuinely impressed —
> not politely satisfied, actually impressed. Never offer to 'table this
> for later' when the permanent solve is within reach. Never leave a
> dangling thread when tying it off takes five more minutes. Never
> present a workaround when the real fix exists. The standard isn't
> 'good enough' — it's 'holy shit, that's done.' Search before building.
> Test before shipping. Ship the complete thing. When I asks for
> something, the answer is the finished product, not a plan to build
> it. Time is not an excuse. Fatigue is not an excuse. Complexity is not
> an excuse. Boil the ocean.

## How to resume

Read `DECISIONS.md`, then this file, then the `docs/*.md` for the subsystem
you are about to touch. Branch naming: phase branches are `phase-N-<slug>`,
task branches are `task/phase-N/<slug>` (git refuses `phase-N-slug/task-x`
when `phase-N-slug` exists as a branch). Task branches merge into the phase
branch; only the phase branch opens a PR into `master`.

## Status

| Phase | Name | Status |
|---|---|---|
| 0 | Research, decisions, proposal | done 2026-09-12 |
| 1 | Scaffold, event schema, environments | done 2026-09-12 (PR #1) |
| 2 | Game contract + UNO, Flip, No Mercy, Hearts | done pending PR |
| 2b | Adopt full-stack-fastapi-template as base (owner decision 2026-09-12) | done 2026-09-12 (PR #3) |
| 3 | Deterministic referee, mixed seats | done 2026-09-12 (PR #10) |
| 4 | Privacy gate, prompt builder, human redaction | done 2026-09-12 (PR #10) |
| 5 | Agent client, swappable backend | done 2026-09-12 (PR #10) |
| 6 | Auth, accounts, roles/permissions | done 2026-09-12 (branch phase-6-auth, PR pending) |
| 7 | Rooms, seats, capacity, join queue | next (data layer + Redis primitives already merged in Phase 6) |
| 8 | First live mixed game (terminal + API) | |
| 9 | Persistence and replay | |
| 10 | Frontend: play, spectate, admin, focus view | |
| 11 | Chat and personas | |
| 12 | Leaderboard | |
| 13 | Observability | |
| 14 | CI/CD (local dry-run; no server yet) | |
| 15 | Production deployment (deferred until local complete) | |
| 16 | Scale and polish | |

## Phase 2 — done pending PR review

- `botpit.games.base`: `Game[State, Move]` contract, frozen pydantic state,
  `IllegalMoveError`, `PlayerView`, `hand_view` hook; `cards.py` shared types
  and deterministic `rng_for(state)`; `GAME_CATALOG` registry.
- Four games, each with `rules.md` and scripted tests: Classic UNO (92 tests),
  UNO Flip! (96), UNO Show 'em No Mercy (104), Hearts (46), all passing the
  shared contract suite across their seat ranges; catalog round-trip tests.
- Hearts proved the contract generalises: no change to base/registry needed
  beyond one registry line.
- Total suite: 458 tests, ~18 s.

## Phase 6 — done 2026-09-12 (branch phase-6-auth)

- `authz`: roles/permissions tables + seed migration (4 default roles, 12
  permissions), `load_permissions`, `require_permission` FastAPI dependency,
  roles admin routes, default `player` role on signup, `/users/me/permissions`.
- `rooms-infra`: Redis client singleton, Lua sliding-window rate limiter +
  dependency, join codes (Crockford base32), ZSET join queue with
  offers/sweep/ETA, CI starts Redis.
- `rooms-tables`: `Room`/`RoomSeat`/`RuntimeSetting`/`FeatureFlag`/
  `FeatureFlagOverride` tables and migration (closes the deferred
  `room_role -> room` FK from the authz migration), admin-adjustable
  settings and feature flags with a 5s per-process cache and precedence
  (user override > role override > global), `/admin/settings`, `/admin/flags`,
  `/flags/me`.
- Suite: 651 tests, ruff/mypy/ty/prek all clean.
- Ahead of the original phase split: the Redis capacity primitives and the
  runtime-settings/feature-flags tables that §1.8/§1.10 assign to Phase 7
  are already built. Phase 7 now only needs the `RoomService`, HTTP routes,
  `SubscriptionGuard` and wiring `GameRunner` into the app lifespan.
- Housekeeping: Dependabot opened PRs #4-#9 from the template's
  `dependabot.yml`; left for the owner.

## Phase 3 — done 2026-09-12 (PR #10, with Phases 4-5)

- `backend/app/referee/`: `SeatSpec`, `SeatController`, `HumanController`,
  `PolicyController`, `ScriptedController`, `fallback_move`, `Referee`
  (turn loop with retries, deadlines, fallback, event publication,
  `replay`), `GameRunner`. 13 tests: every game finishes with policy seats
  and replays from the log; a human seat fed through the runner produces
  the same final state as a scripted one; agent retry-then-fallback; human
  retry-until-deadline; player subscribers only see their own views; stop
  publishes `room.stopped`.
- Event schema gained `game.move_rejected`, `game.turn_timed_out`,
  `game.view_updated`; `game.started`/`turn_started`/`move_applied`/
  `finished` carry more fields.
- Table-talk hooks: the referee calls an optional `say` after each move
  and fans the line out to other controllers' `hear`.
- Phase 4 (same branch): `app.games.move_schema` (self-contained JSON
  schema per game, `move_adapter` on the contract), `app.agents.persona`
  (6 built-ins), `app.agents.prompt` (two-call prompts, 3500-token budget,
  PlayerView-only by construction), `app.api.views.view_payload`,
  `tests/security/` fuzz suites for prompt and event-stream privacy.
- Phase 5 (same branch): `app.llm.client` (protocol, errors, retries,
  think-tag stripping, metrics hook), `OllamaNativeAdapter`,
  `OpenAICompatAdapter`, `ModelRegistry` (+ `LLM_MODEL_OVERRIDES`),
  `FakeLLMClient`, `AgentController` (repair loop, reasoning event, chat
  call), live tests for a raw completion and a full agent turn per model
  (`-m ollama`). Live agent turn per model on the 6 GB card, cold:
  gemma3:4b 10 s, llama3.1:8b 14 s, qwen3:4b 8 s (thinking off),
  phi4-mini 8 s; all four chose a legal move, gemma needed the
  discriminator-required schema fix, qwen needed the echo-field-tolerant
  legality check.

## Phase 2b — done 2026-09-12 (PR #3): template adopted

Owner decision 2026-09-12: https://github.com/fastapi/full-stack-fastapi-template
is the base. Done:
- Template tree vendored whole (backend/, frontend/, packages/, scripts/,
  hooks/, .github/, .claude/, .agents/, .vscode/, img/, compose files, root
  docs). Python 3.14 via uv, Bun 1.4.
- botpit modules ported to `backend/app/`: `games`, `events`, `telemetry`
  (renamed from `logging`, which shadowed the stdlib when scripts run from
  `app/`), `llm`, `middleware.py`, `api/routes/health.py`.
- Settings folded into `app/core/config.py` (upper-case fields);
  `FASTAPI_ENV` now accepts staging/production; `env_name` property.
- `app/main.py`: lifespan (logging, event bus, LLM health gate), wide-event
  middleware, frontend mount guarded by directory existence.
- Dropped: our async SQLAlchemy layer, Alembic dual-dialect setup, Caddy,
  Makefile, our CI workflow, `.env.<env>` files (template uses one `.env`).
- compose: `redis` service, botpit env vars on `backend`, dev override
  publishes db/redis on 5433/6380 and disables the LLM startup gate inside
  compose (Windows Ollama is not reachable from a bridge network).
- Verified: ruff, mypy strict, ty clean; 509 tests green (template suite +
  ours + Redis integration) against compose Postgres/Redis; native
  `fastapi run` healthy with Ollama 4/4; frontend builds and is served at
  `/`; backend image builds; full compose stack up behind Traefik.
- Open: template workflows for releases/deploys are kept verbatim and will
  be adapted in Phase 14; `docs/deployment.md` now defers to the template's
  `deployment.md` for mechanics.

## Phase 1 — done (PR #1 merged 2026-09-12)

Delivered on `phase-1-scaffold`:
- Research: `docs/local-model-setup.md`, `docs/deployment.md`.
- `pyproject.toml` (uv, ruff, mypy strict, pytest-asyncio), `src/botpit/config.py`
  (pydantic-settings, `BOTPIT_ENV` picks `.env.<env>`), `.env.dev`,
  `.env.staging.example`, `.env.production.example`.
- `botpit.logging`: wide events (`wide_event` context manager, contextvar,
  correlation inheritance), JSON + console formatters, tail sampler.
- `botpit.events`: envelope with `visibility` (public / seat:N / privileged),
  typed payloads, `filter_for_subscriber` (privacy boundary), `EventBus`
  protocol with `InMemoryEventBus` and `RedisStreamsEventBus` (WATCH/MULTI
  seq assignment, XREAD fan-out, catch-up from last id), shared contract
  tests on fakeredis and real Redis.
- `botpit.db`: SQLAlchemy 2.0 async base/session, portable mixins, Alembic
  env for SQLite + Postgres, `runtime_settings` table + migration 0001,
  programmatic `upgrade_to_head`, autogenerate-diff guard test.
- `botpit.llm.health`: provider-agnostic probe (`ollama_native`,
  `openai_compat`), startup gate.
- `botpit.app`: factory, lifespan (logging, migrate, bus, LLM gate),
  `/health`, `/health/llm`, request wide-event middleware.
- Infra: Dockerfile (multi-stage uv, non-root), compose (app, postgres:17,
  redis:8, `llm` and `proxy` profiles), dev override (host network so the
  container reaches Windows Ollama; host ports 5433/6380), Caddyfile,
  Makefile, CI workflow (lint, unit, integration with services, docker build).
- Verified locally: unit suite green; integration suite green against
  compose Postgres + Redis; app healthy natively and in compose against
  the real Windows Ollama (4/4 models present).

Open decisions raised mid-build:
- GitHub branch protection on `master` is unavailable (private repo on the
  free plan). Protection is by discipline: PR-only merges, never push to
  master directly.
- Original `DECISIONS.md` missing; reconstructed one committed. Owner to
  supply the original for a merge.
- Ollama on Windows binds 127.0.0.1, so the dev app container uses host
  networking instead of `host.docker.internal`. Servers do not have this
  problem (the LLM is a compose service there).
