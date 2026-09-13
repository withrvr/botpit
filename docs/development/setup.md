# Development setup

A new-contributor guide to running botpit locally. For the technology
choices behind any of this, see `docs/stack.md`; for the reasoning behind
the WSL/Ollama split specifically, see `DECISIONS.md` §0.5.

## Prerequisites

- **Python 3.14**, managed by **uv** (`backend/pyproject.toml` pins
  `requires-python = ">=3.14,<4.0"`). Install uv per
  [its own docs](https://docs.astral.sh/uv/); it manages the interpreter too,
  so a separate Python install is not required.
- **Bun** — the frontend package manager/runtime, needed once Phase 10's
  frontend exists (`frontend/`).
- **Docker** (Docker Engine, with Compose v2) — runs Postgres and Redis
  locally; also builds and runs the full stack behind Traefik if you want
  that instead of running the backend natively.
- An **Ollama** install on the machine that will serve local models. See
  "LLM backend" below — this is optional if you only want to run the test
  suite or work on non-agent code paths.

## Install dependencies

```bash
cd backend && uv sync    # Python deps, backend/.venv
bun install               # frontend deps, once frontend/ exists (Phase 10)
```

## Environment variables

Settings are defined in `backend/app/core/config.py` (`class Settings`,
loaded via `pydantic-settings` from one `../.env` file at the repo root —
the template's single-`.env`-per-box model, not the `.env.<env>` files an
earlier pre-template design used). The repo's tracked `.env` holds local
development defaults. Fields that actually exist today:

| Variable | Default | Purpose |
|---|---|---|
| `FASTAPI_ENV` | unset (falls back to `"production"` via `env_name`) | `development` / `staging` / `production` |
| `PROJECT_NAME` | required | shown in API docs and emails |
| `SECRET_KEY` | required | JWT signing; must not be `"changethis"` outside `development` |
| `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD` | required | seeded by `app/initial_data.py` |
| `FRONTEND_HOST` | `http://localhost:5173` | CORS allow-origin |
| `DATABASE_URL` | required | Postgres DSN; `postgres://`/`postgresql://` are rewritten to `postgresql+psycopg://` automatically |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TLS`, `SMTP_SSL`, `SMTP_PORT`, `EMAILS_FROM_EMAIL`, `EMAILS_FROM_NAME` | — | outbound email; `emails_enabled` is true only when `SMTP_HOST` and `EMAILS_FROM_EMAIL` are both set |
| `SENTRY_DSN` | unset | error reporting, skipped entirely in `development` |
| `REDIS_URL` | `redis://localhost:6379/0` | event bus (when `EVENT_BUS=redis`), rate limiter, join queue |
| `EVENT_BUS` | `memory` | `memory` (single-process dev) or `redis` (multi-worker/production shape) |
| `EVENT_STREAM_MAXLEN` | `10000` | per-room retained event count |
| `LLM_PROVIDER` | `ollama_native` | `ollama_native` or `openai_compat` |
| `LLM_BASE_URL` | `http://localhost:11434` | see "LLM backend" below |
| `LLM_MODELS` | the four models installed on the reference dev machine | comma-separated in the env file |
| `LLM_DEFAULT_MODEL` | `qwen3:4b` | used when a room's agent seat doesn't specify one |
| `LLM_TIMEOUT_SECONDS`, `LLM_KEEP_ALIVE`, `LLM_NUM_CTX`, `LLM_MODEL_OVERRIDES` | see `docs/agents.md` | per-call/registry tuning |
| `LLM_HEALTH_REQUIRED_AT_STARTUP` | `false` | `true` makes a bad LLM probe refuse to start the whole API |
| `MAX_CONCURRENT_GAMES`, `MAX_CONNECTIONS`, `MAX_VIEWERS_PER_ROOM` | `4` / `200` / `20` | capacity defaults, admin-overridable at runtime (`docs/capacity-and-queueing.md`) |
| `RATE_LIMIT_ROOM_CREATE`, `RATE_LIMIT_ROOM_JOIN`, `RATE_LIMIT_ROOM_MOVE`, `RATE_LIMIT_CHAT` | `"5/60"`, `"20/60"`, `"30/10"`, `"10/10"` | `"count/seconds"` strings |
| `LOG_LEVEL`, `LOG_FORMAT`, `LOG_SAMPLE_RATE`, `LOG_SLOW_REQUEST_MS`, `LOG_FILE`, `METRICS_ENABLED` | see `docs/admin-and-observability.md` | wide-event logging |
| `EMAIL_TEST_USER` | `test@example.com` | test fixture only |

Only the variables actually read by `Settings` are listed above — don't
invent others. A value of `"changethis"` for `SECRET_KEY`,
`FIRST_SUPERUSER_PASSWORD`, or the database password is only accepted (with
a warning) when `FASTAPI_ENV=development`; anything else raises at startup.

## Postgres and Redis via Docker Compose

`compose.yml` (shared) plus `compose.override.yml` (local dev, loaded
automatically by Compose alongside `compose.yml`) define the supporting
services:

| Service | Image | Dev host port |
|---|---|---|
| `db` | `postgres:18` | `${POSTGRES_PORT:-5433}` → container `5432` |
| `redis` | `redis:8-alpine` (`--appendonly yes`) | `${REDIS_PORT:-6380}` → container `6379` |
| `mailpit` | `axllent/mailpit` | `8025` (UI), `1025` (SMTP) |
| `proxy` | `traefik:v3.7` | `80`, `8090` (dashboard) |
| `adminer` | `adminer` | `8080` |

The non-default ports (5433/6380) exist so a natively-installed Postgres or
Redis on the dev box doesn't collide; override with `POSTGRES_PORT` /
`REDIS_PORT` if needed.

```bash
docker compose up -d db redis mailpit   # supporting services only
cd backend && uv run bash scripts/prestart.sh   # alembic upgrade head + seed superuser
uv run fastapi dev                              # backend, natively, hot-reload
```

Running the backend inside Compose too (`docker compose watch`) works, but
see the LLM backend note below for why AI seats need it to run natively
during dev.

## LLM backend: the WSL/Ollama split

Per `DECISIONS.md` §0.5: project code runs in WSL2; Ollama runs **natively
on Windows**, not inside WSL and not inside a container. WSL2's mirrored
networking mode reaches it at `http://localhost:11434` — that's the
`LLM_BASE_URL` default. This is *not* `host.docker.internal`: Ollama on
Windows binds `127.0.0.1` only, and `host.docker.internal` from inside a
container (or from WSL depending on networking mode) cannot always reach a
loopback-only bind. The dev override in `compose.override.yml` documents
this directly — running the backend **inside** Compose with AI seats
enabled doesn't reliably reach Windows Ollama, so for any work touching
agent seats, run the backend natively (`fastapi dev` from `backend/`) against
the Compose-provided Postgres/Redis, not inside the Compose stack itself.
Compose's `backend` service still works fine for everything that doesn't
need to reach the LLM (human-only rooms, most of the test suite).

A startup health probe (`app.llm.probe_health`) confirms every model in
`LLM_MODELS` is actually installed and reachable; see `docs/local-model-setup.md`
for the full write-up (concurrency/memory tuning knobs, the CPU-inference
numbers behind the model choices, and the adapter interface). With
`LLM_HEALTH_REQUIRED_AT_STARTUP=false` (the default) a degraded LLM backend
just logs a warning — the API and human-only tables keep serving, and rooms
refuse to seat an AI player until `/api/v1/health/llm` reports healthy.

## Running the whole stack in one command

`scripts/dev.sh` (added by a parallel task in this same phase, not covered
here) is the one-command way to bring the entire stack — Postgres, Redis,
the backend, and eventually the frontend — up together in tmux for local
development.

## Tests and linting

```bash
cd backend
uv run pytest                     # full suite (needs Postgres + Redis reachable)
uv run pytest -m ollama tests/live  # live LLM calls, deselected by default
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict .
uv run prek run --all-files       # lint/format hooks, same as pre-commit
```
