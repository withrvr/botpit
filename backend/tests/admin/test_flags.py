"""DB-backed tests for ``app.flags``."""

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from app import flags
from app.authz import grant_role
from app.models import FeatureFlag, FeatureFlagOverride, Role
from tests.utils.user import create_random_user

FLAG = "chat.enabled"


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Any:
    flags.invalidate()
    yield
    db.rollback()
    db.execute(delete(FeatureFlagOverride))
    db.execute(delete(FeatureFlag))
    db.commit()
    flags.invalidate()


def test_unknown_flag_is_off_and_known_seeded(db: Session) -> None:
    assert flags.is_feature_enabled(db, "nope", None) is False
    assert flags.get_flag(db, "nope") is None
    assert flags.is_feature_enabled(db, FLAG, None) is False
    row = db.get(FeatureFlag, FLAG)
    assert row is not None
    assert row.description == flags.KNOWN_FLAGS[FLAG]
    assert {f.key for f in flags.list_flags(db, None)} == set(flags.KNOWN_FLAGS)


def test_precedence(db: Session) -> None:
    user = create_random_user(db)
    other = create_random_user(db)
    assert flags.is_feature_enabled(db, FLAG, user) is False

    flags.set_flag(db, FLAG, True, None)
    assert flags.is_feature_enabled(db, FLAG, user) is True
    flags.set_flag(db, FLAG, False, None)

    # global false + role true -> true
    flags.set_override(db, FLAG, role="player", user_id=None, enabled=True)
    assert flags.is_feature_enabled(db, FLAG, user) is True
    assert flags.is_feature_enabled(db, FLAG, None) is False

    # any role true wins over a role false
    grant_role(db, user.id, "spectator")
    flags.set_override(db, FLAG, role="spectator", user_id=None, enabled=False)
    assert flags.is_feature_enabled(db, FLAG, user) is True

    # user false beats role true
    flags.set_override(db, FLAG, role=None, user_id=user.id, enabled=False)
    assert flags.is_feature_enabled(db, FLAG, user) is False
    assert flags.is_feature_enabled(db, FLAG, other) is True

    # user true beats global false, with no role override around
    db.execute(delete(FeatureFlagOverride))
    db.commit()
    flags.invalidate()
    flags.set_override(db, FLAG, role=None, user_id=user.id, enabled=True)
    assert flags.is_feature_enabled(db, FLAG, user) is True
    assert flags.is_feature_enabled(db, FLAG, other) is False

    listed = {f.key: f for f in flags.list_flags(db, user)}
    assert listed[FLAG].effective is True
    assert listed[FLAG].enabled is False
    assert [o.user_id for o in listed[FLAG].overrides] == [user.id]


def test_set_override_validation(db: Session) -> None:
    user = create_random_user(db)
    with pytest.raises(ValueError):
        flags.set_override(db, FLAG, role="player", user_id=user.id, enabled=True)
    with pytest.raises(ValueError):
        flags.set_override(db, FLAG, role=None, user_id=None, enabled=True)
    with pytest.raises(LookupError):
        flags.set_override(db, FLAG, role="nope", user_id=None, enabled=True)
    with pytest.raises(LookupError):
        flags.set_override(db, "nope", role="player", user_id=None, enabled=True)
    with pytest.raises(LookupError):
        flags.set_flag(db, "nope", True, None)
    # upsert: second write updates the same row
    a = flags.set_override(db, FLAG, role="player", user_id=None, enabled=True)
    b = flags.set_override(db, FLAG, role="player", user_id=None, enabled=False)
    assert a.id == b.id
    assert b.enabled is False
    assert flags.delete_override(db, FLAG, b.id) is True
    assert flags.delete_override(db, FLAG, b.id) is False
    assert flags.delete_override(db, FLAG, uuid.uuid4()) is False


def test_check_constraint_rejects_both_or_neither(db: Session) -> None:
    user = create_random_user(db)
    flags.get_flag(db, FLAG)
    role = db.exec(select(Role)).first()
    assert role is not None
    for role_id, user_id in ((role.id, user.id), (None, None)):
        db.add(
            FeatureFlagOverride(
                flag_key=FLAG, role_id=role_id, user_id=user_id, enabled=True
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
