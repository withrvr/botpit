"""Rooms, seats, runtime settings and feature flags (Phase 7)

Revision ID: ce9cf9ad654e
Revises: a1b2c3d4e5f6
Create Date: 2026-09-12 22:06:42.653599

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = 'ce9cf9ad654e'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('feature_flag',
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('room',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sqlmodel.sql.sqltypes.AutoString(length=12), nullable=False),
    sa.Column('game', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('seat_count', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('visibility', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('creator_id', sa.Uuid(), nullable=True),
    sa.Column('seed', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['creator_id'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_room_code'), 'room', ['code'], unique=True)
    op.create_index('ix_room_created_at', 'room', ['created_at'], unique=False)
    op.create_index('ix_room_status_visibility', 'room', ['status', 'visibility'], unique=False)
    op.create_table('runtime_setting',
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('feature_flag_override',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('flag_key', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=True),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.CheckConstraint('(role_id IS NULL) <> (user_id IS NULL)', name='ck_feature_flag_override_role_xor_user'),
    sa.ForeignKeyConstraint(['flag_key'], ['feature_flag.key'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    # A compound UniqueConstraint over (flag_key, role_id, user_id) never fires:
    # exactly one of role_id/user_id is always NULL (see the CHECK above), and
    # NULL is never equal to NULL for uniqueness purposes. Two partial unique
    # indexes -- one per case -- are what actually enforces "one override per
    # flag+role" and "one override per flag+user".
    op.create_index(
        'uq_feature_flag_override_flag_role', 'feature_flag_override',
        ['flag_key', 'role_id'], unique=True,
        postgresql_where=sa.text('role_id IS NOT NULL'),
    )
    op.create_index(
        'uq_feature_flag_override_flag_user', 'feature_flag_override',
        ['flag_key', 'user_id'], unique=True,
        postgresql_where=sa.text('user_id IS NOT NULL'),
    )
    op.create_table('room_seat',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('seat', sa.Integer(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('invited_user_id', sa.Uuid(), nullable=True),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.Column('persona', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=False),
    sa.ForeignKeyConstraint(['invited_user_id'], ['user.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['room_id'], ['room.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('room_id', 'seat', name='uq_room_seat_room_id_seat')
    )
    op.create_index(op.f('ix_room_seat_user_id'), 'room_seat', ['user_id'], unique=False)
    # deferred from a1b2c3d4e5f6: room_role.room_id now points at a real table
    op.create_foreign_key('fk_room_role_room', 'room_role', 'room', ['room_id'], ['id'], ondelete='CASCADE')


def downgrade():
    op.drop_constraint('fk_room_role_room', 'room_role', type_='foreignkey')
    op.drop_index(op.f('ix_room_seat_user_id'), table_name='room_seat')
    op.drop_table('room_seat')
    op.drop_table('feature_flag_override')
    op.drop_table('runtime_setting')
    op.drop_index('ix_room_status_visibility', table_name='room')
    op.drop_index('ix_room_created_at', table_name='room')
    op.drop_index(op.f('ix_room_code'), table_name='room')
    op.drop_table('room')
    op.drop_table('feature_flag')
