"""Alembic environment: async engine, one metadata, batch mode on SQLite."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

import botpit.db.models  # noqa: F401  # registers every table on Base.metadata
from botpit.config import get_settings
from botpit.db.base import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url: str | None = config.attributes.get("url")
    if not url:
        url = context.get_x_argument(as_dictionary=True).get("url")
    return url or get_settings().database_url


def run_migrations_offline() -> None:
    url = _url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


# Fixed key for pg_advisory_lock so N app workers starting at once run the
# migration exactly once and the rest wait on the lock. SQLite is single-process.
_MIGRATION_LOCK_KEY = 0x0B07_9170


def _run_sync(connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(f"SELECT pg_advisory_lock({_MIGRATION_LOCK_KEY})")
        # Session-level lock survives the commit; alembic then opens its own transaction.
        connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
