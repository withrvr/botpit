"""Shared fixtures. Keep this file small; per-module fixtures live next to their tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from botpit.config import Settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests never read the developer's .env.dev or real Ollama by accident."""
    for key in list(os.environ):
        if key.startswith(("BOTPIT_", "LLM_", "DATABASE_", "REDIS_", "EVENT_BUS", "LOG_", "JWT_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BOTPIT_ENV", "dev")
    monkeypatch.setenv("LLM_HEALTH_REQUIRED_AT_STARTUP", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings()
