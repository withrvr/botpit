"""ORM models. Every table lives here so Alembic sees one metadata."""

from typing import Any

from sqlalchemy.orm import Mapped, mapped_column

from botpit.db.base import Base, TimestampMixin

__all__ = ["RuntimeSetting"]


class RuntimeSetting(Base, TimestampMixin):
    """Admin-adjustable runtime overrides (MAX_CONCURRENT_GAMES etc.)."""

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict[str, Any]]
    description: Mapped[str | None]
