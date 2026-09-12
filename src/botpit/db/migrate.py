"""Run Alembic programmatically so the app lifespan and tests can migrate."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

_ROOT = Path(__file__).resolve().parents[3]


def _config(url: str | None) -> Config:
    config = Config(_ROOT / "alembic.ini")
    if url is not None:
        config.attributes["url"] = url  # read by migrations/env.py; no ini interpolation
    return config


async def upgrade_to_head(url: str | None = None) -> None:
    # alembic's command API is sync and env.py calls asyncio.run, so keep it off our loop.
    await asyncio.to_thread(command.upgrade, _config(url), "head")


async def downgrade_to_base(url: str | None = None) -> None:
    await asyncio.to_thread(command.downgrade, _config(url), "base")
