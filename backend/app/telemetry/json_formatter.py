"""JSON-lines formatter for machines, key=value console formatter for humans."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_CONSOLE_KEYS = (
    "event_kind",
    "outcome",
    "duration_ms",
    "request_id",
    "game_id",
    "error.type",
)


def _wide(record: logging.LogRecord) -> dict[str, Any] | None:
    wide = getattr(record, "wide", None)
    return wide if isinstance(wide, dict) else None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        wide = _wide(record)
        if wide is not None:
            out.update(wide)
        if record.exc_info:
            out["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S"
        )

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        line = super().formatMessage(record)
        wide = _wide(record)
        if wide is not None:
            line += "".join(f" {k}={wide[k]}" for k in _CONSOLE_KEYS if k in wide)
        return line
