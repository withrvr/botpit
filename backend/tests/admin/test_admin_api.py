"""HTTP tests for /admin/settings, /admin/flags and /flags/me."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import flags
from app import settings_store as store
from app.core.config import settings
from app.models import FeatureFlag, FeatureFlagOverride, RuntimeSetting, User

API = settings.API_V1_STR
FLAG = "rooms.public_listing"


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Any:
    store.invalidate()
    flags.invalidate()
    yield
    db.execute(delete(FeatureFlagOverride))
    db.execute(delete(FeatureFlag))
    db.execute(delete(RuntimeSetting))
    db.commit()
    store.invalidate()
    flags.invalidate()


def test_non_admin_forbidden(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    h = normal_user_token_headers
    assert client.get(f"{API}/admin/settings", headers=h).status_code == 403
    assert (
        client.patch(
            f"{API}/admin/settings/max_connections", headers=h, json={"value": 1}
        ).status_code
        == 403
    )
    assert client.get(f"{API}/admin/flags", headers=h).status_code == 403
    assert (
        client.patch(
            f"{API}/admin/flags/{FLAG}", headers=h, json={"enabled": True}
        ).status_code
        == 403
    )
    assert client.get(f"{API}/admin/settings").status_code == 401
    assert client.get(f"{API}/flags/me").status_code == 401


def test_settings_roundtrip(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    h = superuser_token_headers
    r = client.get(f"{API}/admin/settings", headers=h)
    assert r.status_code == 200
    by_key = {s["key"]: s for s in r.json()["data"]}
    assert set(by_key) == set(store.KNOWN_SETTINGS)
    assert by_key["max_concurrent_games"] == {
        "key": "max_concurrent_games",
        "value": settings.MAX_CONCURRENT_GAMES,
        "default": settings.MAX_CONCURRENT_GAMES,
        "overridden": False,
        "description": store.KNOWN_SETTINGS["max_concurrent_games"][1],
    }

    assert store.max_concurrent_games(db) == settings.MAX_CONCURRENT_GAMES
    r = client.patch(
        f"{API}/admin/settings/max_concurrent_games", headers=h, json={"value": 11}
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == 11
    assert r.json()["overridden"] is True
    # PATCH invalidated the process cache: the helper sees the new value
    assert store.max_concurrent_games(db) == 11
    row = db.get(RuntimeSetting, "max_concurrent_games")
    assert row is not None
    superuser = db.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    assert row.updated_by == superuser.id

    for bad in (0, "5", None):
        r = client.patch(
            f"{API}/admin/settings/max_connections", headers=h, json={"value": bad}
        )
        assert r.status_code == 422, r.text
    r = client.patch(f"{API}/admin/settings/nope", headers=h, json={"value": 1})
    assert r.status_code == 422


def test_flags_admin_and_me(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    h = superuser_token_headers
    r = client.get(f"{API}/admin/flags", headers=h)
    assert r.status_code == 200
    assert {f["key"] for f in r.json()} == set(flags.KNOWN_FLAGS)

    me = client.get(f"{API}/flags/me", headers=normal_user_token_headers)
    assert me.status_code == 200
    assert me.json() == dict.fromkeys(flags.KNOWN_FLAGS, False)

    r = client.patch(f"{API}/admin/flags/nope", headers=h, json={"enabled": True})
    assert r.status_code == 404

    user = db.exec(select(User).where(User.email == settings.EMAIL_TEST_USER)).one()
    r = client.post(
        f"{API}/admin/flags/{FLAG}/overrides",
        headers=h,
        json={"user_id": str(user.id), "enabled": True},
    )
    assert r.status_code == 200, r.text
    override_id = r.json()["id"]
    assert r.json()["role"] is None
    me = client.get(f"{API}/flags/me", headers=normal_user_token_headers).json()
    assert me[FLAG] is True

    r = client.get(f"{API}/admin/flags", headers=h)
    flag = next(f for f in r.json() if f["key"] == FLAG)
    assert flag["enabled"] is False
    assert flag["effective"] is False  # superuser has no override
    assert [o["id"] for o in flag["overrides"]] == [override_id]

    r = client.post(
        f"{API}/admin/flags/{FLAG}/overrides",
        headers=h,
        json={"role": "player", "user_id": str(user.id), "enabled": True},
    )
    assert r.status_code == 422
    r = client.post(
        f"{API}/admin/flags/{FLAG}/overrides",
        headers=h,
        json={"role": "nope", "enabled": True},
    )
    assert r.status_code == 404

    r = client.delete(f"{API}/admin/flags/{FLAG}/overrides/{override_id}", headers=h)
    assert r.status_code == 200
    assert (
        client.delete(
            f"{API}/admin/flags/{FLAG}/overrides/{uuid.uuid4()}", headers=h
        ).status_code
        == 404
    )
    me = client.get(f"{API}/flags/me", headers=normal_user_token_headers).json()
    assert me[FLAG] is False

    r = client.patch(f"{API}/admin/flags/{FLAG}", headers=h, json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    me = client.get(f"{API}/flags/me", headers=normal_user_token_headers).json()
    assert me[FLAG] is True
