# Persistence

SQLModel with a **sync** engine (psycopg driver), Postgres only, migrated
with Alembic — the template's own persistence layer (DECISIONS.md §15),
adopted at the Phase 2b template pivot in place of the original Phase 1
design (async SQLAlchemy 2.0 across SQLite-for-dev and Postgres-for-later,
which is what an older version of this document described). One models
module, one migration chain, one dialect.

## Layout

| Path | Role |
|---|---|
| `backend/app/models.py` | every table (`SQLModel` subclasses with `table=True`) plus every request/response schema, in one module |
| `backend/app/core/db.py` | `engine = create_engine(str(settings.DATABASE_URL), pool_pre_ping=True)`; `init_db(session)` seeds the first superuser |
| `backend/app/api/deps.py` | `SessionDep` — the FastAPI dependency every route uses to get a `Session` |
| `backend/app/alembic/` | Alembic config: `env.py` (imports `app.models.SQLModel.metadata` as `target_metadata`, reads the URL from `settings.DATABASE_URL`), `versions/` |
| `backend/scripts/prestart.sh` | `alembic upgrade head` then `python app/initial_data.py` — run before the server starts (in `compose.yml`'s `backend` entrypoint chain and by hand in dev), **not** in the app lifespan |

`DATABASE_URL` is a `PostgresDsn`; a `postgres://`/`postgresql://` scheme is
rewritten to `postgresql+psycopg://` by a `field_validator` in
`backend/app/core/config.py` so the URL in `.env` can be written either way.

## Adding a model

1. Add the class to `backend/app/models.py`, subclassing `SQLModel` with
   `table=True`. Use `Field(foreign_key=..., ondelete=...)` for
   relationships; portable column types only — the two-dialect rules that
   used to apply here (SQLite compatibility, batch-mode `ALTER`) no longer
   do, since Postgres is the only target, but keep using SQLModel's
   `sa_type=` for anything that needs an explicit Postgres type
   (`DateTime(timezone=True)`, `BigInteger`, `JSON` via `Column(JSON, ...)`
   — see `Room.seed`, `RuntimeSetting.value` for examples already in the
   codebase).
2. Write request/response schemas (plain `SQLModel` without `table=True`)
   next to it — this codebase keeps DB tables and their public/create/update
   shapes in the same file, not split into separate `schemas.py`.
3. Autogenerate a migration (below) and read the result before committing.

## Autogenerating a migration

```bash
cd backend
uv run alembic upgrade head                                  # bring the dev DB to head first
uv run alembic revision --autogenerate -m "add players"      # writes app/alembic/versions/<rev>_add_players.py
```

Hand-check the generated file: constraint/index names should come out
deterministic (SQLModel/SQLAlchemy's default naming), foreign keys should
carry the `ondelete` you intended (autogenerate sometimes needs a nudge for
this), and the downgrade must actually undo the upgrade. `f54e8ebd4583_game_history_phase_9.py`
(Phase 9's `GameRecord`/`GameEvent` migration) is a recent example of a
clean autogenerate against two new tables only — a good reference for what
"generated cleanly" looks like.

## Migration chain

Chronological, oldest first (template migrations, then botpit's own):

| Revision | What it added |
|---|---|
| `e2412789c190` | initial models (template: `User`, `Item`) |
| `d98dd8ec85a3` | UUID primary keys replacing integer ids |
| `1a31ce608336` | cascade-delete relationships |
| `9c0a54914c78` | explicit `String`/`VARCHAR` max lengths |
| `fe56fa70289e` | `created_at` on `User`/`Item` |
| `a1b2c3d4e5f6` | RBAC: `role`, `permission`, `role_permission`, `user_role`, `room_role` (Phase 6, `docs/roles-and-permissions.md`) |
| `ce9cf9ad654e` | rooms: `room`, `room_seat`, `runtime_setting`, `feature_flag`, `feature_flag_override`, plus the deferred `room_role.room_id` FK (Phase 7, `docs/rooms.md`) |
| `f54e8ebd4583` | game history: `game_record`, `game_event` (Phase 9, `docs/games.md`) |
| `30c74160ca50` | `leaderboard:view` permission + role grants (Phase 12, `docs/leaderboard.md`) |

### Adding a permission after `a1b2c3d4e5f6`

`a1b2c3d4e5f6` (the RBAC seed migration above) imports
`ALL_PERMISSIONS`/`DEFAULT_ROLES` **live** from `app/authz/permissions.py`
and re-evaluates them every time it runs -- not a frozen snapshot of what
existed when it was written. That means on any database that has never
run a migration before (a fresh CI run, a new deployment), *that* older
migration seeds whatever permission set is current at the time it runs --
including a permission added by a much later phase. A later migration
that plain-inserts the same permission then collides with a row
`a1b2c3d4e5f6` already seeded. `30c74160ca50` hit exactly this (a real CI
failure on PR #22, never reproduced against the local dev DB because that
DB had already applied `a1b2c3d4e5f6` long before the new permission
existed). Any future migration that adds a permission needs
`postgresql.insert(...).on_conflict_do_nothing(...)` instead of
`op.bulk_insert`, correct on both a fresh database (a no-op, since
`a1b2c3d4e5f6` already seeded it) and an existing one that predates the
new permission (a real insert).

## How tests migrate

The test suite runs against a real Postgres instance (no in-memory or
SQLite fallback — Postgres is the only supported dialect now). CI and local
test runs point `DATABASE_URL` at the Compose-provided Postgres
(`docs/development/setup.md`) and migrations are applied the same way a
real deploy applies them: `alembic upgrade head` before the suite runs.
