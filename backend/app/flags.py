"""Feature flags: global bool per key, per-user and per-role overrides.

Precedence: user override > any role override (true wins) > global row.
Unknown flags are off. Known flags are seeded lazily so the admin panel
lists them without a data migration. Cache pattern mirrors
``app.settings_store``.
"""

from __future__ import annotations

import time
import uuid

from sqlmodel import Session, col, select

from app.authz.service import get_role
from app.models import (
    FeatureFlag,
    FeatureFlagOverride,
    FeatureFlagOverridePublic,
    FeatureFlagPublic,
    Role,
    User,
    UserRole,
    get_datetime_utc,
)

CACHE_TTL_SECONDS = 5.0

KNOWN_FLAGS: dict[str, str] = {
    "rooms.public_listing": "List public rooms in the lobby.",
    "chat.enabled": "In-room chat between seated humans and viewers.",
    "admin.log_tail": "Live log tail on the admin dashboard.",
}

# ponytail: per-process cache, Redis invalidate when multi-worker
_cache: dict[tuple[str, uuid.UUID | None], tuple[float, bool]] = {}


def invalidate() -> None:
    _cache.clear()


def get_flag(session: Session, key: str) -> FeatureFlag | None:
    """The row for ``key``; created on first read when ``key`` is known."""
    row = session.get(FeatureFlag, key)
    if row is None and key in KNOWN_FLAGS:
        row = FeatureFlag(key=key, description=KNOWN_FLAGS[key])
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _resolve(session: Session, flag: str, user: User | None) -> bool:
    row = get_flag(session, flag)
    if row is None:
        return False
    if user is None:
        return row.enabled
    overrides = session.exec(
        select(FeatureFlagOverride).where(FeatureFlagOverride.flag_key == flag)
    ).all()
    for o in overrides:
        if o.user_id == user.id:
            return o.enabled
    role_ids = set(
        session.exec(select(UserRole.role_id).where(UserRole.user_id == user.id)).all()
    )
    role_votes = [o.enabled for o in overrides if o.role_id in role_ids]
    if role_votes:
        return any(role_votes)
    return row.enabled


def is_feature_enabled(session: Session, flag: str, user: User | None) -> bool:
    key = (flag, None if user is None else user.id)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    value = _resolve(session, flag, user)
    _cache[key] = (now + CACHE_TTL_SECONDS, value)
    return value


def set_flag(
    session: Session, key: str, enabled: bool, by: uuid.UUID | None
) -> FeatureFlag:
    """Raises ``LookupError`` for an unknown flag."""
    row = get_flag(session, key)
    if row is None:
        raise LookupError(f"unknown flag: {key}")
    row.enabled = enabled
    row.updated_by = by
    row.updated_at = get_datetime_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    invalidate()
    return row


def set_override(
    session: Session,
    key: str,
    *,
    role: str | None,
    user_id: uuid.UUID | None,
    enabled: bool,
) -> FeatureFlagOverride:
    """Upsert. Raises ``ValueError`` unless exactly one of role/user_id is set,
    ``LookupError`` for an unknown flag or role."""
    if (role is None) == (user_id is None):
        raise ValueError("exactly one of role or user_id is required")
    if get_flag(session, key) is None:
        raise LookupError(f"unknown flag: {key}")
    role_id: uuid.UUID | None = None
    if role is not None:
        role_row = get_role(session, role)
        if role_row is None:
            raise LookupError(f"unknown role: {role}")
        role_id = role_row.id
    row = session.exec(
        select(FeatureFlagOverride).where(
            FeatureFlagOverride.flag_key == key,
            FeatureFlagOverride.role_id == role_id,
            FeatureFlagOverride.user_id == user_id,
        )
    ).first()
    if row is None:
        row = FeatureFlagOverride(
            flag_key=key, role_id=role_id, user_id=user_id, enabled=enabled
        )
    else:
        row.enabled = enabled
    session.add(row)
    session.commit()
    session.refresh(row)
    invalidate()
    return row


def delete_override(session: Session, key: str, override_id: uuid.UUID) -> bool:
    row = session.get(FeatureFlagOverride, override_id)
    if row is None or row.flag_key != key:
        return False
    session.delete(row)
    session.commit()
    invalidate()
    return True


def list_flags(session: Session, user: User | None) -> list[FeatureFlagPublic]:
    for key in KNOWN_FLAGS:
        get_flag(session, key)
    role_names = {r.id: r.name for r in session.exec(select(Role)).all()}
    overrides = session.exec(
        select(FeatureFlagOverride).order_by(col(FeatureFlagOverride.id))
    ).all()
    out: list[FeatureFlagPublic] = []
    for row in session.exec(select(FeatureFlag).order_by(col(FeatureFlag.key))).all():
        out.append(
            FeatureFlagPublic(
                key=row.key,
                enabled=row.enabled,
                effective=is_feature_enabled(session, row.key, user),
                description=row.description,
                overrides=[
                    FeatureFlagOverridePublic(
                        id=o.id,
                        role=None if o.role_id is None else role_names.get(o.role_id),
                        user_id=o.user_id,
                        enabled=o.enabled,
                    )
                    for o in overrides
                    if o.flag_key == row.key
                ],
            )
        )
    return out
