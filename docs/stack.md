# Technology stack

What botpit is built on, why each piece was picked, and what it is used for.
This is the one page to read to understand the whole technology surface
without hunting through `DECISIONS.md` and the per-subsystem docs. It does
not repeat those documents' detail — it summarizes and links to them.

## Base and language

- **[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)**
  is the vendored base (DECISIONS.md §15, owner decision 2026-09-12, mid-build
  pivot documented in `docs/architecture/phase-02b-template-adoption.md`).
  botpit is built on top of it, not from scratch: backend, frontend
  scaffolding, compose files, CI workflow shape, and dev tooling all started
  from the template tree, then had botpit's own modules ported into
  `backend/app/`.
- **Python 3.14**, managed by **uv** (a single workspace at the repo root).
  Chosen with the template; uv's speed and lockfile discipline replaced the
  original Phase 1 plan of plain `pip`/`venv`.
- **Bun** is the frontend package manager/runtime once Phase 10 builds the
  frontend (React 19, TanStack Router/Query, Tailwind 4, shadcn/ui, Vite —
  see `DECISIONS.md` §15; this supersedes the SvelteKit pick that was
  researched pre-template and is recorded below for history).

## Backend framework and data

- **FastAPI** — the web framework. One `api_router` (`backend/app/api/main.py`)
  mounted under `API_V1_STR`; every botpit subsystem (rooms, games, authz,
  admin) is its own router.
- **SQLModel** with a **sync** engine and **psycopg** (`postgresql+psycopg`)
  — the template's persistence layer, adopted in place of the original Phase 1
  plan of async SQLAlchemy 2.0 across SQLite and Postgres. DB work inside
  `async def` route handlers goes through `run_in_threadpool`; the engine
  itself (`backend/app/core/db.py`) is a plain sync `create_engine`.
- **Postgres 18** (`postgres:18` in `compose.yml`, the same image for local
  dev and any deploy-shaped setup — one pin, not a dev/deploy split) is the
  only database. The SQLite dual-dialect layer from Phase 1 was dropped at
  the Phase 2b pivot — one dialect, one set of column types, no `if dialect
  == ...` branches anywhere in the codebase. PostgreSQL 19 entered beta in
  2026; not adopted here until it ships stable (`docs/development/dependency-audit.md`).
- **Alembic** — schema migrations, run via `scripts/prestart.sh` before the
  server starts (not in the app lifespan, unlike the pre-template design).
  See `docs/persistence.md`.
- **Redis 8** (`redis:8-alpine` in `compose.yml`) — two independent uses,
  never a source of truth for game state (DECISIONS.md §12): the pluggable
  event bus's `RedisStreamsEventBus` (`docs/events.md`) when
  `EVENT_BUS=redis`, and the sliding-window rate limiter plus join-queue
  primitives (`docs/capacity-and-queueing.md`).
- **Pydantic v2** — every request/response model (via SQLModel, which wraps
  it), the event envelope and payload schema (`backend/app/events/schema.py`),
  and `pydantic-settings` for `Settings` (`backend/app/core/config.py`).

## Auth and authorization

- **PyJWT** + **pwdlib[argon2]** — hand-rolled auth (the template's own
  choice), not a framework like `fastapi-users`. `docs/research/`'s
  reasoning (now folded into this page): `fastapi-users` was in maintenance
  mode when researched, forces its own `User` model shape, and roles/rooms
  need a custom user model regardless — so a five-table hand-built schema
  costs about the same and stays simple. `passlib` was rejected as
  unmaintained since 2020 in favor of `pwdlib`'s Argon2id default.
- **Hand-built RBAC** (`backend/app/authz/`) — roles, permissions, and two
  assignment tables (global `user_role`, room-scoped `room_role`), checked
  through one `require_permission` FastAPI dependency and one
  `load_permissions` function for everything else. `pycasbin` was the
  researched runner-up; rejected because it adds a second source of truth
  (its own policy table) and an in-process cache to invalidate across
  workers, for a policy language this project's four roles never need. Full
  detail: `docs/roles-and-permissions.md`.

## Real-time transport and capacity

- **Redis Streams** for the event bus (multi-worker fan-out with catch-up by
  stream id) with an **in-memory** (`deque`) implementation for
  single-process dev/tests — one `EventBus` protocol, `EVENT_BUS` picks the
  implementation. Chosen over Pub/Sub because Pub/Sub is fire-and-forget: a
  reconnecting client loses whatever was sent while it was away.
- **Lua sliding-window rate limiting** on redis-py, one atomic script shared
  by HTTP routes and (eventually) websocket handlers. `slowapi` doesn't
  support websockets at all; `fastapi-limiter` was a viable runner-up but adds
  a transitive dependency (`pyrate-limiter`) for what is about 45 lines of
  Lua + Python here.
- **Redis sorted sets** for the FIFO join queue (`docs/capacity-and-queueing.md`):
  `ZRANK` gives O(log N) position feedback, `ZADD NX` makes joining
  idempotent, and the queue is explicitly ephemeral (DECISIONS.md §12 — a
  queue is not game state, so losing it on restart is fine).

## LLM serving

- **Ollama**, running natively on Windows and reached from WSL2, is the dev
  choice (DECISIONS.md §0.5, `docs/local-model-setup.md`). The codebase does
  not depend on Ollama specifically — it depends on "an OpenAI-compatible or
  Ollama-compatible completion endpoint at a base URL"
  (`backend/app/llm`'s `LLMClient` protocol, two adapters:
  `OllamaNativeAdapter` and `OpenAICompatAdapter`).
- **llama.cpp `llama-server`** is the intended server-tier engine once a
  staging/production host exists (`docs/deployment/staging.md`,
  `docs/deployment/production.md`) — same GGUF model files as dev, runs on
  CPU, official Docker images. `vLLM` was researched and rejected for now
  (needs an NVIDIA GPU, can't load GGUF, its throughput advantage is
  irrelevant at this project's scale); TGI was eliminated outright
  (archived by Hugging Face in March 2026).

## Observability

- **Wide events** (`backend/app/telemetry`, née `botpit.logging` — renamed
  in the Phase 2b port because `logging` shadowed the stdlib module when
  scripts ran from inside `app/`) — one comprehensive JSON event per unit of
  work (HTTP request, agent turn, game), not scattered log lines. Reference:
  https://loggingsucks.com/. Detail: `docs/admin-and-observability.md`.
- **Prometheus** (`prometheus-client` + `prometheus-fastapi-instrumentator`)
  is the planned metrics exporter for Phase 13 — picked over an OpenTelemetry
  Prometheus exporter because the latter renames metrics, adds SDK ceremony
  for no collector in this project's plan, and its own docs say it isn't
  designed for multi-process use.

## Frontend (Phase 10)

- **React 19.3, TanStack Router 1.170/Query 5.102, TypeScript 6.0
  (see below), Tailwind 4.3, shadcn/ui, Vite 8.3, Bun 1.4**, built into
  `backend/app/frontend` and served by FastAPI, with a generated OpenAPI
  client (`@hey-api/openapi-ts` 0.99) — the template's own stack, adopted
  at the Phase 2b pivot, kept current in Phase 9.6
  (`docs/development/dependency-audit.md`). `@tanstack/react-table` 9 (a
  real v8→v9 migration, not just a version bump) is still only used by the
  template's own `Admin` scaffolding — the Items demo it also backed was
  removed in Phase 10, not botpit's domain. Phase 10 built the real
  lobby/create-room/join-by-code/live-game/spectator-focus-view screens on
  top of a websocket push transport (`WS /rooms/{id}/ws`), a hand-written
  card-and-visual-identity layer, and a reducer-based live-state hook
  (`src/lib/roomLiveState.ts`) — see `PROGRESS.md`'s Phase 10 section for
  the full write-up.
- **TypeScript stays on 6.0**, not the newer major 7.0, because
  `@hey-api/openapi-ts` (the OpenAPI-to-TypeScript client generator this
  project's build depends on) crashes under TypeScript 7 — a confirmed,
  currently-open upstream bug (`hey-api/hey-api#4235`), not a choice made
  here. Revisit once that's fixed upstream; full detail in
  `docs/development/dependency-audit.md`.
  This supersedes an earlier researched pick of **SvelteKit 2 + Svelte 5**
  (runes-based reactive state was attractive for a websocket-fed table UI,
  and produced the smallest bundles in 2026 framework comparisons) — recorded
  here for history since the reasoning no longer applies once the template
  became the base.

## What's deliberately not here

No hosted LLM API adapter (only local/self-hosted serving is implemented or
planned); no feature-flag SaaS (a two-table Postgres schema plus a 5-second
in-process cache does the whole job, per `docs/rooms.md`); no OpenTelemetry
collector or tracing backend (stdlib logging + wide events + tail sampling
is the whole observability plan for this project's scale, DECISIONS.md §14).
