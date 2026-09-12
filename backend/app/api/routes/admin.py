"""Runtime settings and feature flags (admin), plus ``GET /flags/me``."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import flags, settings_store
from app.api.deps import CurrentUser, SessionDep
from app.authz import P, require_permission
from app.models import (
    FeatureFlagOverrideCreate,
    FeatureFlagOverridePublic,
    FeatureFlagPublic,
    FeatureFlagUpdate,
    Message,
    RuntimeSettingPublic,
    RuntimeSettingsPublic,
    RuntimeSettingUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])
me_router = APIRouter(prefix="/flags", tags=["flags"])

_settings_admin = Depends(require_permission(P.ADMIN_MANAGE_SETTINGS))
_flags_admin = Depends(require_permission(P.ADMIN_MANAGE_FLAGS))


@router.get(
    "/settings", response_model=RuntimeSettingsPublic, dependencies=[_settings_admin]
)
def read_settings(session: SessionDep) -> Any:
    return RuntimeSettingsPublic(
        data=[
            RuntimeSettingPublic(**v)
            for v in settings_store.all_settings(session).values()
        ]
    )


@router.patch(
    "/settings/{key}",
    response_model=RuntimeSettingPublic,
    dependencies=[_settings_admin],
)
def update_setting(
    session: SessionDep, user: CurrentUser, key: str, body: RuntimeSettingUpdate
) -> Any:
    try:
        settings_store.set_value(session, key, body.value, user.id)
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return RuntimeSettingPublic(**settings_store.all_settings(session)[key])


@router.get(
    "/flags", response_model=list[FeatureFlagPublic], dependencies=[_flags_admin]
)
def read_flags(session: SessionDep, user: CurrentUser) -> Any:
    return flags.list_flags(session, user)


@router.patch(
    "/flags/{key}", response_model=FeatureFlagPublic, dependencies=[_flags_admin]
)
def update_flag(
    session: SessionDep, user: CurrentUser, key: str, body: FeatureFlagUpdate
) -> Any:
    try:
        flags.set_flag(session, key, body.enabled, user.id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Flag not found")
    return next(f for f in flags.list_flags(session, user) if f.key == key)


@router.post(
    "/flags/{key}/overrides",
    response_model=FeatureFlagOverridePublic,
    dependencies=[_flags_admin],
)
def create_override(
    session: SessionDep, key: str, body: FeatureFlagOverrideCreate
) -> Any:
    try:
        row = flags.set_override(
            session, key, role=body.role, user_id=body.user_id, enabled=body.enabled
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return FeatureFlagOverridePublic(
        id=row.id, role=body.role, user_id=row.user_id, enabled=row.enabled
    )


@router.delete(
    "/flags/{key}/overrides/{override_id}",
    response_model=Message,
    dependencies=[_flags_admin],
)
def remove_override(session: SessionDep, key: str, override_id: uuid.UUID) -> Any:
    if not flags.delete_override(session, key, override_id):
        raise HTTPException(status_code=404, detail="Override not found")
    return Message(message="Override removed")


@me_router.get("/me")
def read_my_flags(session: SessionDep, user: CurrentUser) -> dict[str, bool]:
    return {k: flags.is_feature_enabled(session, k, user) for k in flags.KNOWN_FLAGS}
