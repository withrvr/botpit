"""Roles, permissions and their seeds (Phase 6)

Revision ID: a1b2c3d4e5f6
Revises: fe56fa70289e
Create Date: 2026-09-12 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from app.authz.permissions import ALL_PERMISSIONS, DEFAULT_ROLES, permission_id, role_id


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'fe56fa70289e'
branch_labels = None
depends_on = None


def upgrade():
    role = op.create_table('role',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    permission = op.create_table('permission',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    role_permission = op.create_table('role_permission',
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('permission_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permission.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.PrimaryKeyConstraint('role_id', 'permission_id')
    )
    op.create_table('user_role',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id')
    )
    op.create_table('room_role',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id', 'room_id')
    )
    op.create_index('ix_room_role_room_id_user_id', 'room_role', ['room_id', 'user_id'], unique=False)

    op.bulk_insert(permission, [
        {'id': permission_id(name), 'name': name, 'description': ''}
        for name in sorted(ALL_PERMISSIONS)
    ])
    op.bulk_insert(role, [
        {'id': role_id(name), 'name': name, 'scope': scope, 'description': description}
        for name, (scope, description, _) in DEFAULT_ROLES.items()
    ])
    op.bulk_insert(role_permission, [
        {'role_id': role_id(name), 'permission_id': permission_id(perm)}
        for name, (_, _, perms) in DEFAULT_ROLES.items()
        for perm in sorted(perms)
    ])


def downgrade():
    op.drop_index('ix_room_role_room_id_user_id', table_name='room_role')
    op.drop_table('room_role')
    op.drop_table('user_role')
    op.drop_table('role_permission')
    op.drop_table('permission')
    op.drop_table('role')
