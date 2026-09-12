"""Roles and permissions (hand-built RBAC, see docs/roles-and-permissions.md)."""

from app.authz.deps import current_permissions, require_permission
from app.authz.permissions import ALL_PERMISSIONS, DEFAULT_ROLES, P
from app.authz.service import (
    ensure_default_role,
    grant_role,
    list_roles,
    load_permissions,
    revoke_role,
)

__all__ = [
    "ALL_PERMISSIONS",
    "DEFAULT_ROLES",
    "P",
    "current_permissions",
    "ensure_default_role",
    "grant_role",
    "list_roles",
    "load_permissions",
    "require_permission",
    "revoke_role",
]
