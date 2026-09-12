"""Same migration chain and models against a live Postgres. Run with

DATABASE_URL=postgresql+asyncpg://botpit:botpit@localhost/botpit uv run pytest -m integration
"""

from __future__ import annotations

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from botpit.db import Base, RuntimeSetting, build_engine, downgrade_to_base, upgrade_to_head

# Captured at import: the autouse conftest fixture scrubs DATABASE_URL before each test.
PG_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not PG_URL.startswith("postgresql"), reason="DATABASE_URL is not Postgres"),
]


async def test_postgres_migrate_roundtrip_no_drift() -> None:
    await upgrade_to_head(PG_URL)
    engine = build_engine(PG_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(RuntimeSetting(key="pg_probe", value={"ok": True}))
            await session.commit()
            row = await session.scalar(
                select(RuntimeSetting).where(RuntimeSetting.key == "pg_probe")
            )
            assert row is not None
            assert row.value == {"ok": True}
            assert row.created_at.tzinfo is not None
            await session.delete(row)
            await session.commit()

        def compare(conn: object) -> list[object]:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})  # type: ignore[arg-type]
            return compare_metadata(ctx, Base.metadata)

        async with engine.connect() as conn:
            assert await conn.run_sync(compare) == []
    finally:
        await engine.dispose()
        await downgrade_to_base(PG_URL)
