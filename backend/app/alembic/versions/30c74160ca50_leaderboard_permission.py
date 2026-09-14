"""Leaderboard view permission (Phase 12)

Revision ID: 30c74160ca50
Revises: f54e8ebd4583
Create Date: 2026-09-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.authz.permissions import permission_id, role_id

# revision identifiers, used by Alembic.
revision = '30c74160ca50'
down_revision = 'f54e8ebd4583'
branch_labels = None
depends_on = None

# Deliberately literal, not `app.authz.permissions.P.LEADERBOARD_VIEW` /
# `ALL_PERMISSIONS` / `DEFAULT_ROLES` -- those constants keep growing as
# later phases add permissions, and a new permission gets a new,
# self-contained migration naming exactly what it adds, rather than
# re-deriving from module state that will look different by the time some
# future phase's migration runs.
#
# `ON CONFLICT DO NOTHING`, not a plain insert: the *original* rbac
# migration (a1b2c3d4e5f6) imports `ALL_PERMISSIONS`/`DEFAULT_ROLES`
# *live* from `app.authz.permissions` and re-evaluates them every time it
# runs -- so on any database that has never run any migration yet (a fresh
# CI run, a new deployment), that older migration runs first and already
# sees `leaderboard:view` in the current `ALL_PERMISSIONS`/`DEFAULT_ROLES`,
# seeding it itself. This migration exists for the *other* case: a
# database that already applied a1b2c3d4e5f6 before this permission
# existed, where nothing will ever seed it otherwise. A plain
# `op.bulk_insert` broke exactly the first case (a real CI failure caught
# on this PR: `UniqueViolation` on `permission_pkey`, since a1b2c3d4e5f6
# had already inserted the identical row moments earlier in the same
# fresh-database migration run).
_PERMISSION = "leaderboard:view"
_GRANTED_TO = ("admin", "player", "spectator")


def upgrade():
    permission = sa.table(
        "permission",
        sa.column("id"),
        sa.column("name"),
        sa.column("description"),
    )
    role_permission = sa.table(
        "role_permission", sa.column("role_id"), sa.column("permission_id")
    )
    op.execute(
        postgresql.insert(permission)
        .values(id=permission_id(_PERMISSION), name=_PERMISSION, description="")
        .on_conflict_do_nothing(index_elements=["id"])
    )
    for name in _GRANTED_TO:
        op.execute(
            postgresql.insert(role_permission)
            .values(role_id=role_id(name), permission_id=permission_id(_PERMISSION))
            .on_conflict_do_nothing(index_elements=["role_id", "permission_id"])
        )


def downgrade():
    op.execute(
        sa.text("DELETE FROM role_permission WHERE permission_id = :pid").bindparams(
            pid=permission_id(_PERMISSION)
        )
    )
    op.execute(
        sa.text("DELETE FROM permission WHERE id = :pid").bindparams(
            pid=permission_id(_PERMISSION)
        )
    )
