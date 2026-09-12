"""Admin-adjustable runtime settings: DB row overrides the config default.

Read path is cached per process for a few seconds; writes go through
``set_value`` which invalidates. Same pattern as ``app.flags``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.models import RuntimeSetting, get_datetime_utc

CACHE_TTL_SECONDS = 5.0


def positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("must be an integer >= 1")
    return value


# key -> (default getter, description, validator). Validators raise ValueError.
KNOWN_SETTINGS: dict[str, tuple[Callable[[], Any], str, Callable[[Any], Any]]] = {
    "max_concurrent_games": (
        lambda: settings.MAX_CONCURRENT_GAMES,
        "Games allowed to run at once; further starts wait in the queue.",
        positive_int,
    ),
    "max_connections": (
        lambda: settings.MAX_CONNECTIONS,
        "Total websocket connections the server accepts.",
        positive_int,
    ),
    "max_viewers_per_room": (
        lambda: settings.MAX_VIEWERS_PER_ROOM,
        "Spectators per room; further viewers wait in the queue.",
        positive_int,
    ),
}

_MISSING = object()
# ponytail: per-process cache, Redis invalidate when multi-worker
_cache: dict[str, tuple[float, Any]] = {}


def invalidate() -> None:
    _cache.clear()


def get_value(session: Session, key: str, default: Any) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now:
        value = hit[1]
    else:
        row = session.get(RuntimeSetting, key)
        value = _MISSING if row is None else row.value
        _cache[key] = (now + CACHE_TTL_SECONDS, value)
    return default if value is _MISSING else value


def get_int(session: Session, key: str, default: int) -> int:
    value = get_value(session, key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def set_value(
    session: Session, key: str, value: Any, by: uuid.UUID | None
) -> RuntimeSetting:
    """Raises ``LookupError`` for an unknown key, ``ValueError`` for a bad value."""
    if key not in KNOWN_SETTINGS:
        raise LookupError(f"unknown setting: {key}")
    value = KNOWN_SETTINGS[key][2](value)
    row = session.get(RuntimeSetting, key)
    if row is None:
        row = RuntimeSetting(key=key, value=value, updated_by=by)
    else:
        row.value = value
        row.updated_by = by
        row.updated_at = get_datetime_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    invalidate()
    return row


def all_settings(session: Session) -> dict[str, Any]:
    """Known keys -> {value, default, overridden, description}. Uncached."""
    rows = {r.key: r.value for r in session.exec(select(RuntimeSetting)).all()}
    out: dict[str, Any] = {}
    for key, (default, description, _) in KNOWN_SETTINGS.items():
        d = default()
        out[key] = {
            "key": key,
            "value": rows.get(key, d),
            "default": d,
            "overridden": key in rows,
            "description": description,
        }
    return out


def max_concurrent_games(session: Session) -> int:
    return get_int(session, "max_concurrent_games", settings.MAX_CONCURRENT_GAMES)


def max_connections(session: Session) -> int:
    return get_int(session, "max_connections", settings.MAX_CONNECTIONS)


def max_viewers_per_room(session: Session) -> int:
    return get_int(session, "max_viewers_per_room", settings.MAX_VIEWERS_PER_ROOM)
