import warnings
from typing import Annotated, Any, Literal, Self

from pydantic import (
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development", "staging", "production"] | None = None
    VERSION: str = "0.1.0"

    @property
    def env_name(self) -> str:
        return self.FASTAPI_ENV or "production"

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    # --- botpit: real-time transport -------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    # memory for a single-process dev server, redis for anything multi-worker.
    EVENT_BUS: Literal["memory", "redis"] = "memory"
    EVENT_STREAM_MAXLEN: int = 10_000

    # --- botpit: LLM backend ----------------------------------------------
    # Ollama's native API is the dev default (Ollama on the Windows host);
    # anything speaking /v1/chat/completions (llama-server, vLLM, Ollama) is openai_compat.
    LLM_PROVIDER: Literal["ollama_native", "openai_compat"] = "ollama_native"
    LLM_BASE_URL: str = "http://localhost:11434"
    LLM_MODELS: Annotated[list[str], NoDecode] = [
        "gemma3:4b",
        "llama3.1:8b",
        "qwen3:4b",
        "phi4-mini:latest",
    ]
    LLM_DEFAULT_MODEL: str = "qwen3:4b"
    LLM_TIMEOUT_SECONDS: float = 120.0
    LLM_KEEP_ALIVE: str = "30m"
    LLM_NUM_CTX: int = 4096
    # Per-model overrides of provider/base_url/num_ctx/keep_alive/timeout_s (JSON), e.g.
    # {"qwen3:4b": {"provider": "openai_compat", "base_url": "http://llm-qwen:8080/v1"}}
    LLM_MODEL_OVERRIDES: dict[str, dict[str, Any]] = {}
    # Opt-in: when true the API refuses to start without a healthy LLM backend.
    # Off by default so CI, tests and a clone without Ollama still serve; rooms
    # refuse to seat an AI player while /health/llm is degraded (Phase 7).
    LLM_HEALTH_REQUIRED_AT_STARTUP: bool = False

    @field_validator("LLM_MODELS", mode="before")
    @classmethod
    def _split_models(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # --- botpit: capacity defaults (runtime overrides live in the DB) ------
    MAX_CONCURRENT_GAMES: int = 4
    MAX_CONNECTIONS: int = 200
    MAX_VIEWERS_PER_ROOM: int = 20
    # "count/seconds" per user; parsed to (count, seconds). Dev-generous.
    RATE_LIMIT_ROOM_CREATE: Annotated[tuple[int, int], NoDecode] = (5, 60)
    RATE_LIMIT_ROOM_JOIN: Annotated[tuple[int, int], NoDecode] = (20, 60)
    RATE_LIMIT_CHAT: Annotated[tuple[int, int], NoDecode] = (10, 10)

    @field_validator(
        "RATE_LIMIT_ROOM_CREATE",
        "RATE_LIMIT_ROOM_JOIN",
        "RATE_LIMIT_CHAT",
        mode="before",
    )
    @classmethod
    def _parse_rate(cls, value: object) -> object:
        if isinstance(value, str):
            count, _, seconds = value.partition("/")
            return (int(count), int(seconds))
        return value

    # --- botpit: wide-event logging (docs/admin-and-observability.md) ------
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"
    LOG_SAMPLE_RATE: float = 1.0
    LOG_SLOW_REQUEST_MS: int = 1000
    LOG_FILE: str | None = None
    METRICS_ENABLED: bool = True

    @field_validator("LOG_SAMPLE_RATE")
    @classmethod
    def _rate_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("LOG_SAMPLE_RATE must be between 0 and 1")
        return value

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
