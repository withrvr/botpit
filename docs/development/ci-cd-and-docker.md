# CI/CD and Docker

Why the Docker build, Compose setup, CI workflows, server startup, and test
execution look the way they do. Written at the end of Phase 9.7
(infrastructure-only optimization pass, no application/test-content
changes) as the answer to "why does our infrastructure look like this,"
for whoever reads it next.

## Docker image build

**Already current, left alone:** the multi-stage build (a discarded
`oven/bun:1` stage produces the frontend's static assets; the final stage
is Python-only), the two-phase `uv sync` pattern with BuildKit cache mounts
and bind-mounted `uv.lock`/`pyproject.toml` (deps-only sync before the app
code is copied, so a code-only change doesn't invalidate the dependency
layer; a second full sync after), and dependency-before-code layer
ordering. This is astral-sh's own documented Docker pattern for `uv`,
already implemented correctly.

**Changed — base image, measured:** `python:3.14` → `python:3.14-slim`.
Measured on this machine: **2.13GB → 1.14GB (46% smaller)**. `docker
history` attributed the difference to `python:3.14`'s apt/Debian-base
layers (compilers, dev headers) that this image never needs at
runtime — every Python dependency here is a prebuilt wheel
(`psycopg[binary]` bundles its own `libpq`).

This swap broke two things, both fixed:

- `python:3.14-slim` doesn't ship `curl`, and `compose.yml`'s backend
  healthcheck used `curl -f`. Re-adding `curl` via apt would partially
  undo the size win, so the healthcheck now runs `python3 -c
  "import urllib.request; urllib.request.urlopen(...)"` instead —
  `urlopen` raises (non-zero exit) on anything but a 2xx response, the
  same semantics as `curl -f`.
- The worker-count fix below (a persistent `ENV`) leaked into the
  dev-only `fastapi dev` command; fixed by not using a persistent `ENV`
  at all. See "Server startup."

**Changed — non-root user:** added a `groupadd`/`useradd` (uid/gid 1000,
no home dir) + `chown -R app:app /app` + `USER app`, placed after every
root-owned build step (`uv sync`, `COPY`) but before `CMD`. Verified
`prestart.sh` (Alembic migrations + initial-data seed) runs correctly as
this user against the slim base.

**Changed — `.dockerignore` hardening** (root, `backend/`): added
`.env*`, `.pytest_cache`, `.ruff_cache`, `*.log`, and
`packages/react-email/.react-email/` (a build-cache directory discovered
in Phase 9.6, already `.gitignore`d there but not `.dockerignore`d).
`frontend/.dockerignore` was already minimal and correct — no Python
caches apply there, left alone.

## Docker Compose for local dev and deploy

**Already current, left alone:** `compose.yml`'s named volumes
(`app-db-data`, `app-redis-data`) for Postgres/Redis persistence across
restarts; healthchecks on `db` (`pg_isready`) and `redis` (`redis-cli
ping`); the backend's `depends_on` using the modern `condition:
service_healthy` form (with `restart: true` on the `db` dependency
specifically). `compose.override.yml`'s Compose Watch (`develop: watch:`)
for live dev sync, dev-only host-port offsets (`POSTGRES_PORT:-5433`,
`REDIS_PORT:-6380`) to avoid colliding with a natively-installed
Postgres/Redis, and the `fastapi dev` reload command. All of this already
matches current (2026) Compose guidance; nothing here needed changing.

**Changed:** `compose.deploy.yml` gave `restart: always` to `proxy`,
`db`, `adminer`, and `backend`, but `redis` had no override block at all
— meaning a deployed Redis container wouldn't auto-restart after a
crash. Added the same `restart: always` to `redis`.

**Changed — GHA-backed Docker layer cache:** `compose.yml`'s `backend`
build and `compose.override.yml`'s `playwright` build each get
`cache_from`/`cache_to: type=gha` (scoped per service). Combined with
`COMPOSE_BAKE=1` on the `docker compose build` steps in
`playwright.yml` and `test-docker-compose.yml`, this routes the build
through `docker buildx bake`, which understands the GHA cache backend
(`docker compose build` alone does not). Requires Docker Engine ≥28
(this machine: 29.8.0; GitHub's `ubuntu-latest` runners ship current
Docker/buildx). This was the single highest-value, not-previously-covered
gap: Playwright's CI job runs 4 parallel shards, each independently
running `docker compose build` for the same backend/playwright images
with zero cache sharing between them — a 4x-redundant build on every
push. Verified locally that the compose files with these fields parse
and build correctly (`COMPOSE_BAKE=1 docker compose build backend` /
`playwright`, both succeed). The actual cross-run/cross-shard cache-hit
benefit can only be confirmed on GitHub's own runners — local builds have
no `ACTIONS_CACHE_URL`/`ACTIONS_RUNTIME_TOKEN`, so there's nothing to
measure here honestly; check the next CI run's build step timing (shards
2-4 should be much faster than shard 1) to confirm.

## CI/CD (GitHub Actions)

**Already current, left alone — uv dependency caching:** `astral-sh/setup-uv`'s
`enable-cache` defaults to `auto`, which only *disables* caching for
`release`/tag-push/`pull_request_target`/`workflow_run` events (to avoid
cache poisoning from untrusted contexts) — not for the plain
`pull_request`/`push` events every workflow here actually uses. Verified
empirically via real CI run logs (`gh run view --log`), not just the
docs: a lockfile-hash change produces a cache miss, and a subsequent run
with the same hash produces a clean hit (`Cache hit for:
setup-uv-2-x86_64-unknown-linux-gnu-ubuntu-24.04-3.14.7-11de0f...`, 66MB
restored in 1.4s at 87.8 MB/s). Adding an explicit `enable-cache: true`
would have been a no-op at best and a slight security regression at
worst — correctly left alone. (A same-push, same-cache-key race between
two workflows saving under an identical key — "another job may be
creating this cache" — is expected, benign behavior after a lockfile
change invalidates the shared key, not a bug.)

**Already current, left alone — action versions:** every action is
SHA-pinned with a version comment; Phase 9.6 already brought these
current and Dependabot watches the `github-actions` ecosystem
continuously.

**Changed — concurrency groups:** `deploy-docker-compose.yml` already
had one (`group: deploy-docker-compose, cancel-in-progress: false`),
proving the pattern was already known in this repo, but `test-backend.yml`,
`playwright.yml`, `pre-commit.yml`, and `test-docker-compose.yml` had
none — a new push didn't cancel a stale in-flight run of the same
workflow on the same branch/PR. Added `concurrency: {group: ...,
cancel-in-progress: true}` to all four, keyed on
`${{ github.head_ref || github.ref }}` so pushes to the same PR/branch
cancel each other but unrelated branches don't collide.

**Changed — path-filter gating:** `playwright.yml` already had a
`dorny/paths-filter`-based `changes` job (filtering `backend/**`,
`frontend/**`, `.env`, `compose*.yml`, the workflow file itself) so
docs-only or unrelated changes skip the expensive Playwright matrix.
`test-backend.yml` and `test-docker-compose.yml` had no such gate and ran
in full on every push/PR regardless of relevance. Added the same
`changes` job pattern to both (scoped to `backend/**` for
`test-backend.yml`; `backend/**` + `frontend/**` for
`test-docker-compose.yml`, since it also serves the built frontend).
No `alls-green`-style wrapper job was needed for these two, unlike
`playwright.yml`'s `alls-green-playwright`: this repo has **no branch
protection configured** (confirmed via `gh api
repos/withrvr/botpit/branches/master/protection` → 403, a private
repo on the free tier can't enable it), so no required status check
depends on a specific job reporting non-skipped. If branch protection is
ever turned on, revisit whether `test-backend`/`test-docker-compose`
need the same wrapper `playwright.yml` already uses.

**Changed — `bun-version` consistency:** bumped the pinned
`bun-version: 1.3.12` → `1.4.0` in `playwright.yml` (both occurrences),
`pre-commit.yml`, and `deploy.yml`, matching the actual local dev Bun
version (`bun --version` → 1.4.0) confirmed throughout Phase 9.6. No
other change to `deploy.yml` — its deploy logic and the leftover
template-org `if: github.repository != 'fastapi/full-stack-fastapi-template'`
check are out of scope for this phase (see below).

**Found, deliberately not fixed — template-org leftovers:**
`.github/workflows/zizmor.yml`'s `advanced-security: ${{
github.repository_owner == 'fastapi' }}` (found in Phase 9.6) and
`deploy.yml`'s `if: github.repository != 'fastapi/full-stack-fastapi-template'`
(found in this phase) are both harmless no-ops for `withrvr/botpit`
(never equal to `fastapi/...`), but genuine template cruft. Both are
in scope for Phase 14's eventual template-workflow adaptation, not this
infra-optimization phase, per the Phase 9.5/9.6 precedent of leaving
`.github/workflows/{prepare-release,issue-manager,bump-pre-commit-hooks,
deploy-docker-compose,deploy,zizmor}.yml`'s actual logic untouched while
still applying narrow, safe, version-consistency fixes (like the
bun-version bump) to those same files.

## Server startup

**Already current, left alone:** `backend/app/main.py`'s lifespan
already blocks startup on a real LLM health check
(`require_healthy_or_raise`) before serving, and on shutdown sequentially
closes the `GameRunner` (cancelling every running game and awaiting its
completion), the LLM `ModelRegistry`, the event bus, and Redis — thorough
readiness and graceful shutdown, no changes needed.
`backend/app/api/routes/health.py`'s `GET /health/` performs a real
Postgres (`SELECT 1`) and event-bus (`bus.ping()`) check, returning 503
on failure, and `compose.yml`'s backend healthcheck already targets
exactly this endpoint — only the healthcheck's own *command* needed
fixing for the slim base (see "Docker image build"), not this
already-correct readiness logic.

**Changed — configurable worker count:** the `CMD` was
`["fastapi", "run", "--workers", "4"]`, a hardcoded worker count. Changed
to `["sh", "-c", "exec fastapi run --workers ${WEB_CONCURRENCY:-4}"]` —
overridable per deploy target via the `WEB_CONCURRENCY` env var (the
standard name from the tiangolo/uvicorn-gunicorn-docker image and
Heroku/Gunicorn ecosystem), still defaulting to 4 when unset. Deliberately
**not** a persistent `ENV WEB_CONCURRENCY=4` instruction: that would leak
into `compose.override.yml`'s separate `fastapi dev --host 0.0.0.0` dev
command, which takes no `--workers` flag at all and would then print a
spurious "workers flag is ignored when reloading is enabled" warning. The
default lives only in the shell parameter expansion inside the `CMD`
string, so it only applies when that exact command runs. `exec` replaces
the shell process so `fastapi run` receives `SIGTERM` directly from
Docker for a clean graceful shutdown — the standard Docker signal-handling
pattern.

**Deliberately deferred — Gunicorn vs. bare Uvicorn workers:** current
(2026) FastAPI/Uvicorn guidance still documents Gunicorn-fronting-Uvicorn
as the "default" production setup (raw Uvicorn "doesn't handle process
signals or restarts gracefully on its own"), while acknowledging
multi-replica bare Uvicorn (`fastapi run --workers N` per pod) as "a
legitimate and increasingly common pattern in 2026" for
Kubernetes-style orchestration specifically. This project has no real
production deploy target yet (Phase 15, explicitly deferred per
`DECISIONS.md`/`PROGRESS.md`), so which pattern actually fits is
genuinely undetermined — switching process-management architecture now
would be guessing. Fixed only the concrete, actionable gap (hardcoded →
configurable worker count) and left the Gunicorn-vs-bare-Uvicorn choice
as a deliberate, reasoned deferral to whenever Phase 15 picks a real
deploy target.

## Test environment execution

**Already current, left alone — CI dependency/Docker caching:** covered
above (uv cache via `setup-uv`'s `auto` default; Docker layer cache now
added via GHA backend).

**Checked, not changed — wait-for-DB retry logic:** `backend/scripts/prestart.sh`
has no `tenacity`-style retry loop (unlike the original template's
`backend_pre_start.py`), but this is already adequately handled at the
orchestration level: `compose.yml`'s `depends_on: {condition:
service_healthy}` and CI's own `docker compose up -d --wait db mailpit
redis` both already block until Postgres/Redis report healthy before
`prestart.sh` (or the app) ever runs. Adding an application-level retry
loop on top would be redundant, not a gap.

**Checked, not changed — `pytest-xdist` parallelization:**
`backend/tests/conftest.py`'s `db` fixture is `scope="session"` — one
shared `Session` against the real Postgres database for the entire test
run, cleaned up via explicit per-model `DELETE` statements only at final
teardown, not per-test transactional rollback. Running `pytest-xdist`
workers would mean multiple OS processes concurrently reading/writing the
same live tables through that one shared-fixture pattern, with no
per-worker database or schema namespacing anywhere in the fixture setup
— a real collision risk (one worker's test could observe or clobber
another's in-progress state), not a hypothetical one. Making this safe
would need a real fixture rework (per-worker database/schema, keyed off
`PYTEST_XDIST_WORKER`) that this infra-optimization phase's scope
boundary (Section 0: no test-content changes) doesn't cover, and the
suite is already fast as-is: 753 tests in ~53s. Verdict: checked,
marginal benefit given the real isolation risk — not applied.

## Summary of measured changes

| Change | Before | After |
|---|---|---|
| Backend image size | 2.13GB | 1.14GB (-46%) |
| uv dependency cache (CI) | already working (`auto` default) | unchanged, verified |
| Docker layer cache (CI) | none (4x redundant build per Playwright run) | `type=gha`, mechanism verified locally; cross-run hit rate to confirm on next CI push |
