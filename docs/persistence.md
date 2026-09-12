# Persistence

SQLAlchemy 2.0 async + Alembic. One models module, one migration chain, two
dialects: SQLite (`sqlite+aiosqlite`) for dev and tests, Postgres
(`postgresql+asyncpg`) later. `DATABASE_URL` in settings picks the dialect;
nothing else in the app branches on it.

## Layout

| Path | Role |
|------|------|
| `src/botpit/db/base.py` | `Base`, type map, `TimestampMixin`, `UUIDPrimaryKeyMixin`, naming convention |
| `src/botpit/db/models.py` | every table; import this and Alembic sees all of them |
| `src/botpit/db/session.py` | lazy engine, `get_sessionmaker()`, `get_session()` FastAPI dependency, `dispose_engine()` |
| `src/botpit/db/migrate.py` | `upgrade_to_head(url)` / `downgrade_to_base(url)` for lifespan and tests |
| `alembic.ini`, `migrations/` | Alembic config, async `env.py`, versions |

## Adding a model

1. Add the class to `src/botpit/db/models.py`, subclassing `Base` plus the mixins you need, and list it in `__all__`.
2. Annotate with types from the `type_annotation_map`: `datetime` -> `DateTime(timezone=True)`, `dict[str, Any]` -> `JSON`, `str`, `int`, `bool`, `str | None`. Nothing dialect-specific.
3. Autogenerate a migration (below) and read the result before committing it.

## Autogenerating a migration

```bash
uv run alembic upgrade head                             # bring the dev DB to head first
uv run alembic revision --autogenerate -m "add players"  # writes migrations/versions/<rev>_add_players.py
```

Pass `-x url=sqlite+aiosqlite:///path.db` (or a Postgres URL) to target another database.
Hand-check the file: `server_default` should be `sa.func.now()` rather than the
dialect-rendered `sa.text('(CURRENT_TIMESTAMP)')`, and the downgrade must undo
the upgrade. `tests/unit/test_db.py::test_migrations_up_down_and_no_drift` fails if
models and migrations disagree, so a forgotten migration cannot ship.

## Two-dialect rules

- Portable column types only: `String`, `Integer`, `Boolean`, `DateTime(timezone=True)`, `JSON`. UUIDs are 32-char hex strings (`UUIDPrimaryKeyMixin`), not native UUID columns. No `JSONB`, no `ARRAY`, no Postgres enums.
- Every constraint gets a deterministic name from the `MetaData` naming convention. SQLite cannot `ALTER` constraints, so Alembic runs in batch mode there (`render_as_batch=True` in `env.py`); batch mode needs names to drop and recreate them. Use `with op.batch_alter_table("t") as batch:` in hand-written migrations that alter columns or constraints.
- No dialect-specific SQL in models, migrations or queries. The single allowed branch is `build_engine()` in `session.py` (aiosqlite `check_same_thread`, `StaticPool` for in-memory, `PRAGMA foreign_keys=ON`).
- SQLite stores timestamps as naive UTC strings; Postgres returns aware datetimes. Treat all stored times as UTC and never compare `tzinfo` in app code.

## How tests migrate

- Unit tests use the in-memory URL from `tests/conftest.py` and `create_all_for_tests(engine)` for speed; migration tests migrate a temp file DB through `upgrade_to_head(url)` / `downgrade_to_base(url)` and assert `alembic.autogenerate.compare_metadata` returns no diff.
- `tests/integration/test_db_postgres.py` (marker `integration`) runs the same chain against Postgres when `DATABASE_URL=postgresql+asyncpg://...` is set: `DATABASE_URL=... uv run pytest -m integration`.
