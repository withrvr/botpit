# Staging deployment

**No staging server exists yet.** Per the owner's explicit direction
(`DECISIONS.md` §1, amended 2026-09-12): no staging or production server is
bought or hosted until local development, setup, and testing are fully
complete. This file describes the deployment plan for when that time
comes — Phase 15 in `PROGRESS.md`'s phase table — not a live environment.
Everything below is mechanics that are ready to use, not a description of
anything currently running anywhere.

## What "ready" means today

The Docker Compose shape that would run staging already runs locally and is
exercised by every contributor: `compose.yml` (shared services — Traefik,
Postgres, Redis, the backend, Adminer) plus `compose.deploy.yml` (deploy-only
overlay — HTTPS, automatic certificates) is the same pair of files staging
would use, minus `compose.override.yml`'s dev conveniences (bind-mounted
source, published database ports, `fastapi dev --reload`). See
`docs/development/setup.md` for the dev-side services and ports.

## Host requirements and candidates (research, pre-dating any actual purchase)

Requirements a host must meet, from the pre-Phase-1 hosting research that
shaped the Docker/compose layout: long-lived websocket connections (rules
out serverless/edge-only hosts); Postgres, Redis, the app, and a CPU LLM
server co-located (8-16 GB RAM minimum for a 4B model plus everything
else); two environments (staging + production) on a side-project budget;
Docker Compose deployable, since dev already is.

Candidates compared (self-managed VPS vs. PaaS): a small VPS running Docker
Compose (Hetzner-class pricing, e.g. an 8 vCPU/16 GB box in the €15-20/month
range) came out ahead of Fly.io/Railway/Render for this shape specifically
because the LLM has to run on a box under our control anyway (self-hosted
`llama-server`, see below) — paying PaaS prices for the app tier while
self-managing the LLM box on the side would combine the worst of both. A
dedicated GPU box was rejected as too expensive to leave running for a
side project; an on-demand GPU pod (RunPod/Vast.ai, roughly $0.30-0.40/hour)
repointed via `LLM_BASE_URL` for an actual play session is the documented
alternative when faster inference is wanted. Re-verify actual prices at
Phase 15 — this comparison is from 2026-09-12 research and will be stale by
the time a host is actually bought.

## Deploy mechanics (for when a host exists)

1. **Provision a host.** A single VPS with Docker Engine installed is
   sufficient for staging — this project's whole point is to stay
   deployable to one small box (real accounts, real Postgres/Redis, one AI
   backend), not to require orchestration infrastructure.
2. **Copy the code.**
   ```bash
   rsync -av --exclude=".git/" --filter=":- .gitignore" ./ <user>@<staging-host>:/path/to/app/
   ```
3. **Set the environment.** A staging `.env` (never committed) needs at
   least everything `backend/app/core/config.py`'s `Settings` requires —
   see `docs/development/setup.md`'s environment variable table — plus the
   deploy-only variables `compose.yml` reads: `PROJECT_NAME`, `SECRET_KEY`,
   `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`, `POSTGRES_PASSWORD`,
   `DOMAIN` (for Traefik's routing rule), and optionally `SMTP_*`/`SENTRY_DSN`.
   Generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   — never reuse the `changethis` dev defaults; `Settings` refuses them
   outside `FASTAPI_ENV=development` anyway.
4. **Point the LLM backend at a self-hosted model server**, not the dev
   Windows-Ollama setup — see "LLM serving for staging" below and
   `docs/local-model-setup.md`.
5. **Build and migrate.**
   ```bash
   docker compose -f compose.yml -f compose.deploy.yml build
   docker compose -f compose.yml -f compose.deploy.yml run --rm backend bash scripts/prestart.sh
   ```
   `prestart.sh` runs `alembic upgrade head` then seeds the first superuser
   (`app/initial_data.py`) — see `docs/persistence.md` for the migration
   chain this applies.
6. **Bring the stack up.**
   ```bash
   docker compose -f compose.yml -f compose.deploy.yml up -d
   ```
   Traefik terminates TLS for `${DOMAIN}` and routes to the `backend`
   service; the backend image builds and serves the frontend once Phase 10
   exists.
7. **Smoke-test checklist** before calling a deploy healthy:
   - `GET /api/v1/health/` returns `200` with `status: "ok"`, `database: "ok"`,
     and an `event_bus` value ending `:ok`.
   - `GET /api/v1/health/llm` (superuser token required) reports every
     configured model present, or a deliberately degraded state if the LLM
     tier isn't up yet (rooms simply refuse to seat AI players until it is).
   - A test account can sign up, log in, create a room, and — with a second
     account — join it by code.
   - Redis-backed rate limiting and the join queue behave (attempt to
     exceed `RATE_LIMIT_ROOM_CREATE`, confirm a `429` with `Retry-After`).
   - Alembic's own migration state matches head: `alembic current` inside
     the backend container equals the latest revision in
     `backend/app/alembic/versions/`.

## LLM serving for staging

Once a staging host exists, it should run a self-hosted model server
co-located with the app rather than reach back into the developer's Windows
machine. `docs/local-model-setup.md` §3 lays out the reasoning in full;
summarized: **llama.cpp's `llama-server`** (official images
`ghcr.io/ggml-org/llama.cpp:server` / `:server-cuda`) serving the same GGUF
files used in dev is the default plan — it runs on CPU (what a cheap VPS
has), needs no re-quantizing, and exposes an OpenAI-compatible endpoint the
existing `OpenAICompatAdapter` already speaks with zero code changes. Point
`LLM_PROVIDER=openai_compat` and `LLM_BASE_URL` at it. An on-demand GPU pod
(RunPod/Vast.ai) is a documented option for faster inference during an
actual play session, repointed via the same `LLM_BASE_URL` — the codebase
does not care which is behind it.

## Rollback / migrations

Alembic migrations are forward-and-back (`docs/persistence.md`'s "hand-check
the file" rule requires every autogenerated migration's downgrade to
actually undo the upgrade). A bad deploy rolls back by restoring the
previous image tag and, only if the new migration already ran and is
incompatible, running `alembic downgrade -1` before restarting the old
image. No automated rollback tooling exists yet — genuinely deferred to
Phase 15, when a real host makes "roll back a live deploy" a real
operation rather than a hypothetical one.

## Phase 14 status: dry-run verified, no host to actually deploy to yet

Steps 5-6 above (build, migrate, up) are exactly what
`.github/workflows/deploy-docker-compose.yml` already automates --
`workflow_dispatch` + `runs-on: self-hosted` (correct, not a bug: there is
no self-hosted runner registered anywhere, because there is no host to run
one on yet; dispatching this workflow today would just queue forever with
nothing to pick it up). Phase 14 dry-ran the mechanics it encodes, for
real, rather than trusting the YAML was correct on paper:

- `docker compose -f compose.yml -f compose.deploy.yml config` -- the
  overlay parses and interpolates cleanly with a placeholder `DOMAIN`.
- `docker compose -f compose.yml -f compose.deploy.yml build` -- builds
  clean.
- The "prepare database" step, run directly (not through compose, to
  isolate it from Traefik/networking concerns already covered by
  `config`) against a fresh throwaway Postgres with `FASTAPI_ENV=production`
  and real (non-`"changethis"`) secrets, exactly matching what a first
  real deploy would do: full migration chain applies cleanly from an empty
  database, initial superuser seeds correctly. `Settings._check_default_secret`
  correctly *refused* to start with the dev-placeholder `"changethis"`
  values on the first attempt -- confirms that guard actually works, not
  a bug to route around.
- **Not dry-runnable locally, and not a gap**: Traefik's real HTTPS/ACME
  cert issuance genuinely needs a real domain with DNS pointed at a real
  reachable host -- there is no way to exercise that part without one, by
  construction. Everything else in the deploy mechanics is now verified;
  this one piece waits for Phase 15 along with the host itself.

Also found and fixed during Phase 14, unrelated to this compose-based
workflow: `.github/workflows/deploy.yml` (the *other*, FastAPI-Cloud-based
deploy path) was `push: branches: [master]`-triggered and had failed on
every single push to master since this repo's first one -- invisible to
every PR's `gh pr checks` because a `push`-triggered workflow never
appears there. Root cause was a real bug (`bun run --filter frontend
build`, but the actual workspace package is `botpit-frontend` since the
Phase 9.5 rebrand) layered under a design gap (no `FASTAPI_CLOUD_TOKEN`
exists, so even fixed it would still fail at the final step, correctly,
since there's no FastAPI Cloud account yet). Fixed the bug and changed
the trigger to `workflow_dispatch`, matching this file's own already-correct
precedent, so it stops failing loudly for a deploy nobody is asking it to
attempt yet.
