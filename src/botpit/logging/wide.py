"""Wide events: one context-rich record per unit of work (https://loggingsucks.com).

A ``WideEvent`` accumulates fields while the work runs and is emitted exactly
once on exit through the ``botpit.wide`` logger. Correlation ids set on a
parent event are inherited by nested children.
"""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import TracebackType

CORRELATION_FIELDS = ("request_id", "game_id", "room_id", "turn_id", "user_id", "seat")

_current: ContextVar[WideEvent | None] = ContextVar("botpit_wide_event", default=None)
_logger = logging.getLogger("botpit.wide")
_meta: dict[str, str] = {"version": "0.1.0", "env": "dev"}


def set_service_meta(*, version: str, env: str) -> None:
    """Stamp ``version``/``env`` on every event created after this call."""
    _meta.update(version=version, env=env)


def current_event() -> WideEvent | None:
    return _current.get()


class WideEvent:
    """Mutable field accumulator; also a sync and async context manager."""

    def __init__(self, kind: str, **base_fields: Any) -> None:
        self.fields: dict[str, Any] = {
            "event_kind": kind,
            "event_id": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "service": "botpit",
            "version": _meta["version"],
            "env": _meta["env"],
            **base_fields,
        }
        self._start = time.perf_counter()
        self._token: Token[WideEvent | None] | None = None

    # -- accumulation -------------------------------------------------------

    def set(self, **fields: Any) -> Self:
        self.fields.update(fields)
        return self

    def add(self, field: str, value: Any) -> None:
        self.fields.setdefault(field, []).append(value)

    def incr(self, field: str, n: int = 1) -> None:
        self.fields[field] = self.fields.get(field, 0) + n

    def error(self, exc: BaseException, *, retriable: bool = False) -> None:
        self.fields["error.type"] = type(exc).__name__
        self.fields["error.message"] = str(exc)
        self.fields["error.retriable"] = retriable
        if _logger.isEnabledFor(logging.DEBUG):
            self.fields["error.traceback"] = "".join(traceback.format_exception(exc))

    @property
    def duration_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000, 3)

    def __getitem__(self, key: str) -> Any:
        return self.fields[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.fields[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self.fields

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> Self:
        parent = _current.get()
        if parent is not None:
            self.fields["parent_id"] = parent.fields["event_id"]
            for key in CORRELATION_FIELDS:
                if key in parent.fields:
                    self.fields.setdefault(key, parent.fields[key])
        self._start = time.perf_counter()
        self._token = _current.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None
        if exc is not None and "error.type" not in self.fields:
            self.error(exc)
        outcome = "ok" if exc is None else "error"
        self.fields["outcome"] = outcome
        self.fields["duration_ms"] = self.duration_ms
        _logger.log(
            logging.INFO if exc is None else logging.ERROR,
            "%s %s",
            self.fields["event_kind"],
            outcome,
            extra={"wide": dict(self.fields)},
        )

    async def __aenter__(self) -> Self:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


def wide_event(kind: str, **base_fields: Any) -> WideEvent:
    """``with wide_event("http.request", request_id=rid) as ev:`` (sync or async)."""
    return WideEvent(kind, **base_fields)
