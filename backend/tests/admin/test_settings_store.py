"""DB-backed tests for ``app.settings_store``."""

from typing import Any

import pytest
from sqlmodel import Session

from app import settings_store as store
from app.core.config import settings
from app.models import RuntimeSetting


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Any:
    store.invalidate()
    yield
    for key in store.KNOWN_SETTINGS:
        row = db.get(RuntimeSetting, key)
        if row is not None:
            db.delete(row)
    db.commit()
    store.invalidate()


def test_defaults_from_config(db: Session) -> None:
    assert store.max_concurrent_games(db) == settings.MAX_CONCURRENT_GAMES
    assert store.max_connections(db) == settings.MAX_CONNECTIONS
    assert store.max_viewers_per_room(db) == settings.MAX_VIEWERS_PER_ROOM
    assert store.get_value(db, "nope", "dflt") == "dflt"
    assert all(not v["overridden"] for v in store.all_settings(db).values())


def test_set_persists_and_invalidates(db: Session) -> None:
    assert store.max_concurrent_games(db) == settings.MAX_CONCURRENT_GAMES
    row = store.set_value(db, "max_concurrent_games", 9, None)
    assert row.value == 9
    assert store.max_concurrent_games(db) == 9
    info = store.all_settings(db)["max_concurrent_games"]
    assert info["value"] == 9
    assert info["overridden"] is True
    assert info["default"] == settings.MAX_CONCURRENT_GAMES


def test_cache_within_ttl(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
    store.set_value(db, "max_connections", 7, None)
    assert store.max_connections(db) == 7
    # bypass set_value: the cache must hide this until the TTL passes
    row = db.get(RuntimeSetting, "max_connections")
    assert row is not None
    row.value = 8
    db.add(row)
    db.commit()
    clock[0] += 4.0
    assert store.max_connections(db) == 7
    clock[0] += 2.0
    assert store.max_connections(db) == 8


@pytest.mark.parametrize("bad", [0, -1, "5", 2.5, True, None])
def test_validator_rejects(db: Session, bad: Any) -> None:
    with pytest.raises(ValueError):
        store.set_value(db, "max_connections", bad, None)


def test_unknown_key(db: Session) -> None:
    with pytest.raises(LookupError):
        store.set_value(db, "nope", 1, None)
