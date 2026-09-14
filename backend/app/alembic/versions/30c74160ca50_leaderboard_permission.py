"""Leaderboard view permission (Phase 12)

Revision ID: 30c74160ca50
Revises: f54e8ebd4583
Create Date: 2026-09-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

from app.authz.permissions import permission_id, role_id

# revision identifiers, used by Alembic.
revision = '30c74160ca50'
down_revision = 'f54e8ebd4583'
branch_labels = None
depends_on = None

# Deliberately literal, not `app.authz.permissions.P.LEADERBOARD_VIEW` /
# `ALL_PERMISSIONS` / `DEFAULT_ROLES` -- those constants keep growing as
# later phases add permissions, so re-deriving from them here would re-seed
# rows the *original* rbac migration (a1b2c3d4e5f6) already inserted and
# collide on the unique `name` constraint. A new permission gets a new,
# self-contained migration naming exactly what it adds.
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
    op.bulk_insert(
        permission,
        [{"id": permission_id(_PERMISSION), "name": _PERMISSION, "description": ""}],
    )
    op.bulk_insert(
        role_permission,
        [
            {"role_id": role_id(name), "permission_id": permission_id(_PERMISSION)}
            for name in _GRANTED_TO
        ],
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
