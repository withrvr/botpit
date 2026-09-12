"""Structured logging for botpit: wide events, JSON lines, tail sampling."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from botpit.logging.json_formatter import ConsoleFormatter, JsonFormatter
from botpit.logging.sampling import TailSampler
from botpit.logging.wide import WideEvent, current_event, set_service_meta, wide_event

if TYPE_CHECKING:
    from botpit.config import Settings

__all__ = ["WideEvent", "configure_logging", "current_event", "get_logger", "wide_event"]

_OWNED = "_botpit_owned"


def get_logger(name: str) -> logging.Logger:
    if name != "botpit" and not name.startswith("botpit."):
        name = f"botpit.{name}"
    return logging.getLogger(name)


def configure_logging(settings: Settings) -> None:
    """Idempotent: re-running replaces botpit-owned handlers/filters instead of stacking."""
    set_service_meta(version=settings.service_version, env=str(settings.env))

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    handlers[0].setFormatter(
        JsonFormatter() if settings.log_format == "json" else ConsoleFormatter()
    )
    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        handlers.append(file_handler)
    for handler in handlers:
        setattr(handler, _OWNED, True)
        root.addHandler(handler)

    wide = logging.getLogger("botpit.wide")
    for flt in list(wide.filters):
        if isinstance(flt, TailSampler):
            wide.removeFilter(flt)
    wide.addFilter(TailSampler(settings.log_sample_rate, settings.log_slow_request_ms))

    # The wide http.request event replaces uvicorn's access line.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.setLevel(logging.WARNING)
    error = logging.getLogger("uvicorn.error")
    error.handlers.clear()
    error.propagate = True
