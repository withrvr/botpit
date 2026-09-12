"""Fixtures for botpit unit tests: no database, no network, no Ollama."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.config import settings as _settings


@pytest.fixture
def settings() -> Settings:
    return _settings.model_copy(
        update={
            "EVENT_BUS": "memory",
            "LLM_HEALTH_REQUIRED_AT_STARTUP": False,
            "LLM_BASE_URL": "http://127.0.0.1:9",
        }
    )
