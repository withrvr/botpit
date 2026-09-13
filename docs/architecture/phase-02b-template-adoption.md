# Phase 2b — Adopt the full-stack-fastapi-template as the base

Status: done (PR #3), 2026-09-12.

This is the mid-build pivot: the owner decided, partway through the build,
that botpit would sit on top of
[fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)
rather than continue on the from-scratch Phase 1 foundation (`DECISIONS.md`
§15, amended same day). This phase is a real, consequential part of the
project's history — most of Phase 1's infrastructure choices were
superseded here, not incrementally evolved — which is why it gets its own
snapshot rather than being folded into Phase 1 or Phase 3's writeups.

## What was built

- **The entire template tree vendored whole**: `backend/`, `frontend/`,
  `packages/`, `scripts/`, `hooks/`, `.github/`, `.claude/`, `.agents/`,
  `.vscode/`, `img/`, the compose files, and the template's own root docs.
  Python moved to 3.14 via uv; the frontend toolchain to Bun 1.4.
- **botpit's own modules ported into `backend/app/`**: `games`, `events`,
  and `logging` (renamed to `telemetry` — it was shadowing the stdlib
  `logging` module once scripts ran from inside `app/`, a real bug the
  rename fixed, not just a cosmetic choice), plus `llm`, `middleware.py`,
  and `api/routes/health.py`.
- **Settings folded into `app/core/config.py`**, botpit's fields added in
  upper-case to match the template's existing `Settings` style;
  `FASTAPI_ENV` extended to accept `staging`/`production` in addition to
  the template's own values, with a new `env_name` property.
- **`app/main.py`**: a lifespan running logging setup, event-bus startup,
  and the LLM health gate (the same three concerns Phase 1's lifespan ran,
  reassembled on the template's app factory shape); wide-event middleware;
  a frontend static-file mount guarded by directory existence (so a bare
  backend checkout, or a test run before Phase 10 builds a frontend, does
  not fail trying to serve a directory that doesn't exist).
- **Compose**: a `redis` service added to the template's stack; botpit's
  environment variables threaded onto the `backend` service; the dev
  override publishes the database and Redis ports (avoiding native-service
  collisions) and disables the LLM startup gate inside Compose specifically
  — Windows Ollama is not reachable from a Docker bridge network, only from
  the host directly.

## What was dropped

Everything Phase 1 built that the template already had a working
equivalent for, rather than running two versions of the same concern side
by side:

- The async SQLAlchemy 2.0 persistence layer and its dual-dialect (SQLite +
  Postgres) Alembic setup — replaced by the template's sync SQLModel +
  psycopg + Postgres-only design. See
  `docs/architecture/phase-01-scaffold.md` for what this replaced and
  `docs/persistence.md` for the current design.
- Caddy (replaced by the template's Traefik) and the hand-written
  `Makefile` and CI workflow (replaced by the template's own, adapted later
  as needed).
- The `.env.<env>` multi-file environment setup, replaced by the template's
  single `.env` per box plus `FASTAPI_ENV`.

## Key implementation decisions made during this phase

- **Kept deliberately, not rewritten**: wide-event logging (`app.telemetry`),
  the event schema and pluggable bus (`app.events`), the LLM health probe
  (`app.llm`), the request middleware, and Redis as a Compose service —
  these were judged to have no equivalent in the template worth switching
  to, so they moved essentially unchanged.
- **The rename that fixed a real bug**: `botpit.logging` → `app.telemetry`.
  A module named `logging` living inside a package that gets run as a
  script from within itself shadows the stdlib `logging` module for every
  import below it — this surfaced during the port and was fixed by
  renaming, not by restructuring imports around the collision.
- **Template CI/deploy workflows kept verbatim for now**, flagged as an open
  item to adapt during Phase 14 rather than adapting them speculatively
  here before there was a concrete need.

## Module layout after this phase

```
backend/
├── app/
│   ├── core/config.py     # Settings, now the merge point for template + botpit fields
│   ├── main.py            # app factory, lifespan, frontend mount
│   ├── models.py          # template's User/Item + botpit's growing schema
│   ├── games/              # ported from src/botpit/games
│   ├── events/              # ported from src/botpit/events
│   ├── telemetry/           # ported + renamed from src/botpit/logging
│   ├── llm/                 # ported from src/botpit/llm
│   ├── middleware.py
│   └── api/routes/health.py
├── frontend/                 # template's React/Vite scaffold (unused until Phase 10)
├── scripts/, hooks/, .github/, .claude/, .agents/, .vscode/, img/
compose.yml, compose.override.yml, compose.deploy.yml
```

## Tests and what was verified

Ruff, mypy strict, and ty all clean. **509 tests green** (the template's own
test suite plus botpit's ported tests plus a new Redis integration check)
against Compose-provided Postgres and Redis. Verified beyond the test
suite: `fastapi run` healthy natively against the real Windows-hosted
Ollama (4/4 configured models present); the frontend builds and is served
at `/`; the backend Docker image builds; the full Compose stack comes up
successfully behind Traefik.

## Left open at the end of this phase

Template-provided GitHub workflows for releases and deploys were kept as-is
and flagged for adaptation in Phase 14, rather than being rewritten
speculatively during this pivot. `docs/deployment.md` (superseded by
`docs/deployment/staging.md` and `docs/deployment/production.md` in Phase
9.5's documentation reorganization) deferred to the template's own
deployment docs for mechanics at the time this phase was written.
