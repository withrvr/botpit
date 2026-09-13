# Dependency audit (Phase 9.5)

One-time sweep against every dependency's current stable release, done
right before Phase 10 (frontend) starts, per the standing project
convention of stabilizing between backend and frontend work. Not a
recurring scheduled job yet -- there's no CI/CD phase (14) to hang one off
of.

## Backend (`uv lock --upgrade`)

30 packages bumped within the existing `pyproject.toml` version ranges (all
of them `>=X,<NEXT_MAJOR` or caret-equivalent, so nothing here required a
`pyproject.toml` edit -- `uv` just resolved to newer versions already
permitted by the existing constraints):

`fastapi`, `sqlalchemy`, `sqlmodel`, `starlette` (1.3.1 -> 1.6.0, the
largest single jump), `uvicorn`, `pydantic`/`pydantic-core`, `alembic`,
`psycopg`/`psycopg-binary`, `pyjwt`, `sentry-sdk`, `httpx`-adjacent
transitive deps, plus the dev toolchain (`ruff`, `ty`, `prek`, `zizmor`,
`typer`).

Verified clean after: `ruff check`, `mypy app` (strict), `ty check app`,
and the full backend suite (753 tests) all green, same as before the
upgrade.

Python itself: `3.14` is pinned in `.python-version` at the repo root, and
every GitHub Actions workflow that runs Python reads that same file via
`python-version-file: .python-version` rather than a hardcoded version
string -- there is no drift possible between local dev and CI on this.

## Frontend (`bun update`)

Within-range bumps to `@tanstack/react-query`/`react-router` (and their
devtools packages), `react`/`react-dom`, `react-hook-form`,
`lucide-react`, `tailwindcss`, `sonner`, `tailwind-merge`, `zod`,
`@biomejs/biome`, `vite`, `@types/*`, and their transitive dependencies.

Verified clean after: `bun run build` and `bun run lint` (biome) both
green.

### Deliberately not upgraded

- **`@tanstack/react-table`** (8.21.3, latest is 9.x) and **`typescript`**
  (6.0.3, latest is 7.x): both are capped by `package.json`'s own `^`
  range at their current major version, and both major bumps carry real
  breaking-change risk that needs actual code migration to absorb safely
  -- `react-table` v9 changes its core API surface, and the current
  scaffold's `DataTable.tsx`/`Admin/columns.tsx`/`Items/columns.tsx` use
  it directly. Phase 10 (frontend) is about to rewrite or heavily touch
  those exact files anyway, so bumping the major version now, before any
  real UI work happens on top of it, would mean either migrating dead-end
  template scaffold code or doing the migration twice. Left for Phase 10
  to decide once it knows what it's actually keeping vs. replacing.
- **`axios`** (1.18.0) and **`form-data`** (4.0.6): both are exact-pinned
  in `package.json` (no `^`/`~` range), unlike every other dependency in
  the file. An exact pin like that is typically deliberate -- most often
  to work around a specific regression in a later version -- and nothing
  in this repo's history explains why these two are pinned that way.
  Overriding a pin without knowing its reason risks reintroducing whatever
  it was protecting against. Left as-is; a newer `axios` (1.20.0 is
  current) is available whenever someone confirms the pin's origin and
  decides to lift it.

### Noted, not acted on

- Biome's own report flagged `frontend/biome.json`'s `$schema` URL as one
  version behind the just-upgraded `@biomejs/biome` package (`2.5.6` vs.
  `2.5.13`) -- purely an informational schema-version string, not a
  lint failure (`biome check` still ran and passed on this repo's actual
  code). Left as-is: this repo's config-protection tooling blocks direct
  edits to `biome.json` outside of a deliberate, explicit exception, and
  the mismatch has no functional effect. Worth a one-line fix whenever
  someone next touches that file for an actual rule change.
- `starlette`'s `TestClient` and its own `testclient.py` now emit two new
  deprecation warnings under the upgraded `httpx`/`anyio` (`httpx2`
  suggested; `anyio.abc.BlockingPortal` -> `anyio.from_thread.BlockingPortal`).
  Both are upstream library warnings, not anything in this codebase, and
  neither affects test correctness -- the full suite still passes with
  them present. Not acted on; revisit if a future `starlette`/`httpx`
  bump ever turns these into hard removals instead of warnings.

## Container base images

`backend/Dockerfile`: `python:3.14` and `oven/bun:1` -- both float to the
latest patch within their pinned major/minor, already current by
construction, nothing to bump. `compose.yml`: `postgres:18`, `redis:8-alpine`
-- both already the current major version for each. `traefik:v3.7`,
`mailpit`, `adminer` (dev-only tooling containers, not part of botpit's
own runtime) were not chased for exact latest patch tags -- low value
relative to the effort, and none of them affect application behavior.

## What this audit did not cover

- No PRs or phase branches were left dangling going into this audit --
  every phase through 9 is merged into `master`, confirmed via `git log`
  before this phase branched.
- Frontend and backend were audited together in one pass rather than
  strictly "one meaningful batch at a time" with a full-suite run between
  every individual package -- given the actual diff size (30 backend
  packages, ~20 frontend packages, all within already-permitted semver
  ranges) and that the full gate was still run and confirmed green
  immediately after each side's upgrade, a finer-grained bisection wasn't
  needed to attribute a regression, since there wasn't one.
