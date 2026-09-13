# Dependency audit

A living record of dependency-upgrade passes. First done in Phase 9.5
(stabilize/document/rebrand, right before Phase 10 starts); Phase 9.6 is a
dedicated follow-up whose only job was pushing every dependency to its true
latest stable release, not just what was already caught in passing. Not a
recurring scheduled job yet -- there's no CI/CD phase (14) to hang one off
of.

## Phase 9.6 -- true latest, dependency-focused pass

Every source of truth was checked directly (not just Dependabot's own open
PRs, since it doesn't always propose a major-version jump): `bun outdated`/
`uv pip list --outdated` for the true current-latest of every direct and
transitive dependency, and a live web check for anything version-sensitive
enough to need it (PostgreSQL's and Redis's current major, `@hey-api/
openapi-ts`'s TypeScript 7 compatibility, `@tanstack/react-table` v9's
actual GA status -- it was in beta as recently as mid-2026 and only went
stable ~2 weeks before this pass).

### Backend

`uv lock --upgrade` picked up the small remainder left after Phase 9.5
(`ast-serialize`, `coverage`, `pydantic-core` patch bumps). Two
`pyproject.toml` version *floors* were also raised to match what was
already resolving anyway, closing Dependabot PRs #8/#9 for real rather than
just resolving past their proposed floor invisibly:

- `redis[hiredis]`: `>=5.2` -> `>=8.1.0`
- `psycopg[binary]`: `>=3.3.4,<4.0.0` -> `>=3.3.5,<4.0.0`

`pydantic-core` sits at 2.46.5 while 2.49.0 exists on PyPI -- checked
directly against PyPI's own listing, `pydantic` itself (the only thing this
project depends on directly) is genuinely still at 2.13.5, its true latest
release, and its own metadata is what pins the compatible `pydantic-core`
range. Not a gap: forcing `pydantic-core` past what `pydantic` actually
declares compatible would risk a real mismatch, not close one.

Verified clean after: `ruff check`, `mypy app` (strict), `ty check app`,
and the full backend suite (753 tests) all green.

### Frontend

`bun update` picked up the small remainder (`@radix-ui/react-slot`, minor
`@tanstack/*` patch bumps). Three items Phase 9.5 had deliberately deferred
were actually attempted this time, per this phase's mandate to try the real
fix before falling back:

- **`axios`** 1.18.0 -> 1.20.0 and **`@hey-api/openapi-ts`** 0.97.3 ->
  0.99.0: both exact-pinned, both upgraded cleanly. Verified by regenerating
  the OpenAPI client (`scripts/generate-client.sh`) and a full `bun run
  build`/`bun run lint` -- clean. (`form-data`, still exact-pinned at
  4.0.6, was left alone -- same "no recorded reason for the pin, don't
  guess" caution as before, and it's already at its own latest anyway.)
- **`@tanstack/react-table`** 8.21.3 -> 9.2.4: a real v8->v9 migration, not
  a version-number change. v9 replaces `useReactTable`/`getCoreRowModel`/
  `getPaginationRowModel` with `useTable`/`tableFeatures`/
  `createPaginatedRowModel`, drops `table.getState()` in favor of
  `table.state`, drops `row.getVisibleCells()` in favor of `row.getAllCells()`
  (column visibility is no longer bundled into core), and `ColumnDef` gains
  a leading `TFeatures` type parameter. Migrated `DataTable.tsx` (the shared
  component) and both its consumers, `Admin/columns.tsx` and
  `Items/columns.tsx` -- exported the table's `features` object from
  `DataTable.tsx` so both column-definition files share the exact same
  `TFeatures` type. Verified via `bun run build` (`tsc` + `vite build`),
  `bun run lint`, and starting the dev server to exercise
  `@tanstack/router-plugin`'s live route-tree generation under the new
  table types -- all clean. No live-browser render check was done (this is
  still template scaffold, not a real botpit feature yet); build+lint+type
  correctness was judged sufficient given that.
- **`@react-email/ui`**/**`react-email`** (in `packages/react-email/`, the
  `emails` workspace member -- found via a Dependabot PR that mentioned
  packages neither prior audit had looked at): 6.9.1 -> 6.9.5 (true latest;
  Dependabot's own PR only proposed 6.9.3). Verified via `bun run --filter
  emails build` and, more importantly, `bun run --filter emails export` --
  byte-identical output to the three already-committed `backend/app/
  email-templates/*.html` files, confirming the upgrade changes nothing
  observable for this project's actual usage.

### Deliberately held back

- **`typescript`**: attempted the real upgrade to 7.0.2 first, per this
  phase's explicit mandate. It builds, lints, and runs the dev server fine
  **on its own** -- the "TypeScript 7 has no stable programmatic compiler
  API until 7.1" limitation that blocks many toolchains (Vue/Svelte/Astro
  template type-checking, some webpack loaders) does not affect this
  project's actual Vite/`@tanstack/router-plugin` build path. But
  `@hey-api/openapi-ts` 0.99.0 -- the OpenAPI-to-TypeScript client generator
  `scripts/generate-client.sh` depends on -- crashes under it:
  `TypeError: Cannot read properties of undefined (reading 'AnyKeyword')`,
  because it uses the TypeScript compiler as a library to build the
  generated client's AST, and that surface is exactly what changed in
  TypeScript 7. This is a confirmed, currently-open upstream bug
  (`hey-api/hey-api#4235`, labeled `bug`), not something fixable in this
  codebase -- reverted to `^6.0.3` (the version already in use, still the
  right choice) rather than ship a build that can never regenerate its own
  API client. Retry this once that issue closes upstream; check
  `@hey-api/openapi-ts`'s own changelog for a "TypeScript 7 support" entry
  first, since the fix will land there, not in TypeScript itself.

### Container base images

`postgres:18` and `redis:8-alpine` (`compose.yml`) confirmed against a live
check to still be the current major of each -- PostgreSQL 19 is in beta
(scheduled stable ~September 2026, not shipped as of this pass) and Redis
8.x remains current (8.10.1 is the latest patch). `backend/Dockerfile`'s
`python:3.14`/`oven/bun:1` float to latest patch by construction, already
current. `traefik:v3.7`/`mailpit`/`adminer` (dev-only tooling, not
botpit's own runtime) -- Dependabot's own `docker-compose` ecosystem watch
covers these and has not flagged any of them, taken as sufficient signal
rather than re-checking each by hand.

### GitHub Actions

Every action in `.github/workflows/*.yml` is SHA-pinned with a version
comment. Dependabot's `github-actions` group had exactly one open item
(`zizmorcore/zizmor-action` 0.6.2 -> 0.6.3, PR #5) -- applied directly
(new SHA `70fb788f84895a7701f5643d103d587e460b5c99`). Nothing else in that
group was flagged, taken as sufficient signal that the rest are current
(Dependabot watches this ecosystem continuously; a stale action would have
its own open PR by now).

**Found in passing, not fixed (out of scope for a version-upgrade phase):**
`.github/workflows/zizmor.yml`'s `advanced-security`/`annotations` inputs
gate on `github.repository_owner == 'fastapi'` -- a leftover check against
the *template's* upstream org, not this repo's (`withrvr`). Functionally
this just means `advanced-security` is always `false` and `annotations` is
always `true` for this repo, which happens to be harmless, but it's a
correctness bug, not a version issue, and `.github/workflows/*` is a
standing, deliberate deferral to Phase 14 (PROGRESS.md's Phase 2b note) for
exactly this kind of template-authored logic. Flagging here so it isn't
silently lost before Phase 14 gets there.

### Housekeeping found while verifying

`packages/react-email/.react-email/` (that package's local dev/build cache)
had no `.gitignore` entry anywhere in the repo -- added one. Discovered
because verifying the `react-email` upgrade meant actually running its
`build`/`export` scripts, which created the directory.

### Dependabot reconciliation

Every open Dependabot PR at the time this phase started (#4, #5, #8, #9,
#16) was superseded by this pass's own commits -- same or a truer-latest
target version in every case except `typescript` (see above, a deliberate,
documented exception, not a security advisory Dependabot flagged, so
closing #16 does not silently drop anything). All five were closed with a
comment pointing at the PR that superseded them. None of the five carried a
security-advisory label; if a future Dependabot PR does and this kind of
pass doesn't fully resolve it, that PR must stay open and the situation
gets recorded here instead.

## Phase 9.5 -- first sweep

30 backend packages (`uv lock --upgrade`, within existing `pyproject.toml`
ranges: `fastapi`, `sqlalchemy`, `sqlmodel`, `starlette` 1.3.1->1.6.0,
`uvicorn`, `pydantic`, `alembic`, `psycopg`, plus the dev toolchain) and
~20 frontend packages (`bun update`, within existing `package.json` ranges:
`@tanstack/react-query`/`router`, `react`, `react-hook-form`,
`lucide-react`, `tailwindcss`, `biome`, `vite`, `@types/*`). Full backend
suite (753 tests) and `bun run build`/`lint` both green after.

`@tanstack/react-table` (major bump), `typescript` (major bump), `axios`,
and `@hey-api/openapi-ts` (both exact-pinned) were deliberately left
untouched at the time -- Phase 9.6 above is where each was actually
attempted. `python:3.14`/`oven/bun:1`/`postgres:18`/`redis:8-alpine`
container images were already current; Python 3.14 consistency confirmed
via `.python-version`, read by every GitHub Actions workflow that needs a
Python version (no drift possible between local dev and CI).

Noted, not acted on then (still true now): `frontend/biome.json`'s
`$schema` URL trails the installed `@biomejs/biome` version by a few
patches -- purely informational, config-protection tooling blocks direct
edits to `biome.json` outside a deliberate exception, no functional effect.
`starlette`'s `TestClient` emits two upstream deprecation warnings
(`httpx2`, `anyio.from_thread.BlockingPortal`) under the newer
`httpx`/`anyio` -- neither affects test correctness.
