from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from botpit.db import (
    Base,
    RuntimeSetting,
    build_engine,
    create_all_for_tests,
    dispose_engine,
    downgrade_to_base,
    get_engine,
    get_session,
    get_sessionmaker,
    upgrade_to_head,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = get_engine()
    await create_all_for_tests(engine)
    yield engine
    await dispose_engine()


async def test_in_memory_engine_builds(engine: AsyncEngine) -> None:
    assert engine.dialect.name == "sqlite"
    assert get_engine() is engine


async def test_roundtrip_json_and_timestamps(engine: AsyncEngine) -> None:
    async with get_sessionmaker()() as session:
        session.add(RuntimeSetting(key="max_concurrent_games", value={"n": 4}, description="x"))
        await session.commit()
    async with get_sessionmaker()() as session:
        row = await session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.key == "max_concurrent_games")
        )
    assert row is not None
    assert row.value == {"n": 4}
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_get_session_rolls_back_on_exception(engine: AsyncEngine) -> None:
    gen = get_session()
    session = await anext(gen)
    session.add(RuntimeSetting(key="doomed", value={}))
    await session.flush()
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("boom"))
    async with get_sessionmaker()() as check:
        assert (
            await check.scalar(select(RuntimeSetting).where(RuntimeSetting.key == "doomed")) is None
        )


async def _schema_diff(engine: AsyncEngine) -> list[object]:
    def compare(conn: object) -> list[object]:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})  # type: ignore[arg-type]
        return compare_metadata(ctx, Base.metadata)

    async with engine.connect() as conn:
        return await conn.run_sync(compare)


async def test_migrations_up_down_and_no_drift(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    await upgrade_to_head(url)
    engine = build_engine(url)
    try:
        assert await _schema_diff(engine) == []
        await downgrade_to_base(url)
        diff = await _schema_diff(engine)
        assert any(op[0] == "add_table" for op in diff)  # type: ignore[index]
    finally:
        await engine.dispose()
