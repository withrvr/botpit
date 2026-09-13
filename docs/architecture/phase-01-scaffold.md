# Phase 1 — Scaffold, event schema, environments

Status: done, merged to `master` (PR #1), 2026-09-12.

This is a historical snapshot: it describes the system as it stood when
Phase 1 was declared done, on the pre-template codebase layout (`src/botpit/`).
Phase 2b (below) replaced most of this foundation wholesale when the project
adopted the `full-stack-fastapi-template` as its base — see
`docs/architecture/phase-02b-template-adoption.md` for what specifically
survived that pivot and what didn't. Nothing here is updated to match later
changes; for the system as it stands today, read
`docs/architecture/latest_architecture.md`.

## What was built

- **Config** (`src/botpit/config.py`): `pydantic-settings`, `BOTPIT_ENV`
  selects `.env.<env>` (`dev`/`staging`/`production`). `.env.dev`,
  `.env.staging.example`, `.env.production.example` committed.
- **Wide-event logging** (`botpit.logging`): a `wide_event` context manager
  building one comprehensive JSON event per unit of work, a contextvar for
  correlation-id inheritance across nested events, JSON and console
  formatters, and a tail sampler (always keep errors/slow, sample the rest).
  This module's design (not its exact location) survived the Phase 2b
  pivot as `app.telemetry`.
- **Events** (`botpit.events`): the envelope carrying `visibility` (public /
  `seat:N` / privileged), typed payloads, and `filter_for_subscriber` — the
  privacy-filtering boundary that is still, unchanged in design, the
  mechanism `docs/events.md` describes today. An `EventBus` protocol with
  two implementations: `InMemoryEventBus` (single process) and
  `RedisStreamsEventBus` (WATCH/MULTI for atomic sequence assignment, XREAD
  fan-out, catch-up from the last seen id). Contract-tested against both
  fakeredis and a real Redis instance so the two implementations couldn't
  quietly drift apart.
- **DB** (`botpit.db`): SQLAlchemy 2.0 **async** base/session, portable
  mixins (`TimestampMixin`, `UUIDPrimaryKeyMixin`), an Alembic environment
  supporting **both** SQLite (dev/tests) and Postgres (later) via one
  models module, a `runtime_settings` table plus its migration, a
  programmatic `upgrade_to_head`, and an autogenerate-diff guard test
  (fail the suite if models and migrations disagree). This entire layer —
  async engine, dual-dialect SQLite/Postgres support — was dropped at
  Phase 2b in favor of the template's sync SQLModel/Postgres-only design;
  see `docs/persistence.md` for what persistence looks like today.
- **LLM health** (`botpit.llm.health`): a provider-agnostic probe
  (`ollama_native`, `openai_compat`) and a startup gate that can refuse to
  boot the app on an unhealthy LLM backend.
- **App factory** (`botpit.app`): FastAPI app factory, a lifespan running
  logging setup, migrations, event-bus startup, and the LLM health gate in
  sequence; `/health`, `/health/llm`; a request-scoped wide-event
  middleware.
- **Infra**: multi-stage `Dockerfile` (uv, non-root user); Compose stack
  (`app`, `postgres:17`, `redis:8`, plus `llm` and `proxy` profiles); a dev
  override running the app container with host networking so it could
  reach Ollama running on the Windows host; `Caddyfile`; a `Makefile`; a CI
  workflow (lint, unit tests, integration tests against real services,
  Docker build). All of this — Caddy, the Makefile, this specific CI
  workflow — was replaced by the template's own equivalents at Phase 2b.

## Key implementation decisions made during this phase

- **Ollama reachability from the dev container**: Ollama on Windows binds
  `127.0.0.1` only, so `host.docker.internal` cannot reach it from inside a
  container in every networking configuration tried. The dev Compose
  override runs the app container with `network_mode: host` instead. This
  specific problem and its resolution is also recorded in `DECISIONS.md`
  §0.5 for the current (post-template) setup, where the same underlying
  constraint still applies via WSL2 mirrored networking rather than a
  container network mode.
- **`DECISIONS.md` was missing from the repo** when the build started. A
  reconstructed version was committed (see that file's own header) rather
  than blocking on it; the owner supplied amendments the same day.
- **GitHub branch protection on `master` was unavailable** (private repo,
  free plan). Protection is by discipline — PR-only merges — not tooling.

## Module layout introduced

```
src/botpit/
├── config.py
├── logging/          # wide events, formatters, sampler
├── events/            # envelope, visibility, filter, bus protocol + 2 impls
├── db/                # base, models, session, migrate, alembic env
├── llm/
│   └── health.py
└── app.py             # factory, lifespan, /health, /health/llm
```

## Tests and verification

Unit suite green; integration suite green against Compose Postgres + Redis;
the app was verified healthy both running natively and in Compose, in both
cases against the real Windows-hosted Ollama (4/4 configured models
present). No aggregate test count is recorded for this phase specifically
in `PROGRESS.md` — the first project-wide count appears at Phase 2 (458
tests) after the game modules landed.
