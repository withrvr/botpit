"""Application settings.

One ``Settings`` class for every environment. ``BOTPIT_ENV`` selects which
``.env.<env>`` file is layered under process environment variables; nothing
else in the codebase branches on the environment name. Behaviour differences
between dev, staging and production are expressed as values here.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Env(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


def _env_name() -> Env:
    return Env(os.environ.get("BOTPIT_ENV", Env.DEV))


def _env_file() -> tuple[Path, ...]:
    """``.env.<env>`` if present next to the project root, else nothing.

    Resolution is relative to the current working directory on purpose: docker
    compose, uvicorn and pytest all run from the repo root, and a missing file
    is not an error so CI can run on process environment alone.
    """
    candidate = Path.cwd() / f".env.{_env_name()}"
    return (candidate,) if candidate.is_file() else ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # Identity
    env: Env = Field(default_factory=_env_name, alias="BOTPIT_ENV")
    app_name: str = "botpit"
    debug: bool = False
    service_version: str = "0.1.0"

    # HTTP
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # Persistence
    database_url: str = "sqlite+aiosqlite:///./data/botpit.db"
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # Event bus: memory for single-process dev, redis for multi-worker.
    event_bus: Literal["memory", "redis"] = "memory"
    event_stream_maxlen: int = 10_000

    # LLM backend. Ollama's native API is the dev default; anything speaking
    # /v1/chat/completions (llama-server, vLLM, Ollama itself) uses openai_compat.
    llm_provider: Literal["ollama_native", "openai_compat"] = "ollama_native"
    llm_base_url: str = "http://localhost:11434"
    llm_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["gemma3:4b", "llama3.1:8b", "qwen3:4b", "phi4-mini:latest"]
    )
    llm_default_model: str = "qwen3:4b"
    llm_timeout_seconds: float = 120.0
    llm_keep_alive: str = "30m"
    llm_num_ctx: int = 4096
    llm_health_required_at_startup: bool = True

    # Auth (Phase 6 consumes these; declared now so every env file is complete)
    jwt_secret: SecretStr = SecretStr("dev-only-change-me")
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 60 * 60 * 12

    # Capacity defaults. Runtime overrides live in the database (Phase 7).
    max_concurrent_games: int = 4
    max_connections: int = 200
    max_viewers_per_room: int = 20

    # Logging (wide events, see docs/admin-and-observability.md)
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    log_sample_rate: float = 1.0
    log_slow_request_ms: int = 1000
    log_file: str | None = None

    # Metrics
    metrics_enabled: bool = True

    @field_validator("cors_origins", "llm_models", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("log_sample_rate")
    @classmethod
    def _rate_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            msg = "log_sample_rate must be between 0 and 1"
            raise ValueError(msg)
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """For tests that mutate the environment."""
    get_settings.cache_clear()
