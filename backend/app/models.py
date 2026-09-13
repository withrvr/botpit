import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import EmailStr, model_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# --- botpit --------------------------------------------------------------


class HealthReport(SQLModel):
    status: str
    version: str
    env: str
    database: str
    event_bus: str
    llm: str


class Role(SQLModel, table=True):
    id: uuid.UUID = Field(primary_key=True)
    name: str = Field(unique=True, max_length=64)
    scope: str = Field(max_length=16)  # "global" | "room"
    description: str = Field(default="", max_length=255)


class Permission(SQLModel, table=True):
    id: uuid.UUID = Field(primary_key=True)
    name: str = Field(unique=True, max_length=64)
    description: str = Field(default="", max_length=255)


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permission"
    role_id: uuid.UUID = Field(foreign_key="role.id", primary_key=True)
    permission_id: uuid.UUID = Field(foreign_key="permission.id", primary_key=True)


class UserRole(SQLModel, table=True):
    __tablename__ = "user_role"
    user_id: uuid.UUID = Field(
        foreign_key="user.id", primary_key=True, ondelete="CASCADE"
    )
    role_id: uuid.UUID = Field(foreign_key="role.id", primary_key=True)


class RoomRole(SQLModel, table=True):
    __tablename__ = "room_role"
    __table_args__ = (Index("ix_room_role_room_id_user_id", "room_id", "user_id"),)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", primary_key=True, ondelete="CASCADE"
    )
    role_id: uuid.UUID = Field(foreign_key="role.id", primary_key=True)
    room_id: uuid.UUID = Field(
        primary_key=True, foreign_key="room.id", ondelete="CASCADE"
    )


class RolePublic(SQLModel):
    id: uuid.UUID
    name: str
    scope: str
    description: str


class PermissionsPublic(SQLModel):
    permissions: list[str]


class RoleAssignment(SQLModel):
    role: str
    room_id: uuid.UUID | None = None


class UserRolesPublic(SQLModel):
    user_id: uuid.UUID
    roles: list[RoleAssignment]


# --- rooms (Phase 7) -------------------------------------------------------


def _tz() -> Any:
    return DateTime(timezone=True)


class Room(SQLModel, table=True):
    __table_args__ = (
        Index("ix_room_status_visibility", "status", "visibility"),
        Index("ix_room_created_at", "created_at"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(unique=True, index=True, max_length=12)
    game: str = Field(max_length=64)
    seat_count: int
    status: str = Field(default="waiting", max_length=16)
    visibility: str = Field(default="private", max_length=16)
    creator_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, ondelete="SET NULL"
    )
    seed: int = Field(sa_type=BigInteger)
    created_at: datetime = Field(default_factory=get_datetime_utc, sa_type=_tz())
    started_at: datetime | None = Field(default=None, sa_type=_tz())
    finished_at: datetime | None = Field(default=None, sa_type=_tz())


class RoomSeat(SQLModel, table=True):
    __tablename__ = "room_seat"
    __table_args__ = (
        UniqueConstraint("room_id", "seat", name="uq_room_seat_room_id_seat"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    room_id: uuid.UUID = Field(foreign_key="room.id", ondelete="CASCADE")
    seat: int
    kind: str = Field(max_length=8)  # human | agent
    user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        nullable=True,
        ondelete="SET NULL",
        index=True,
    )
    invited_user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, ondelete="SET NULL"
    )
    model: str | None = Field(default=None, max_length=128)
    persona: str | None = Field(default=None, max_length=64)
    status: str = Field(default="open", max_length=8)  # open | filled


class RoomSeatSpec(SQLModel):
    kind: Literal["human", "agent"]
    user_id: uuid.UUID | None = None
    invited_user_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=128)
    persona: str | None = Field(default=None, max_length=64)


class RoomCreate(SQLModel):
    game: str = Field(max_length=64)
    seat_count: int = Field(ge=2, le=10)
    visibility: Literal["private", "unlisted", "public"] = "private"
    seats: list[RoomSeatSpec]

    @model_validator(mode="after")
    def _seats_match_count(self) -> Self:
        if len(self.seats) != self.seat_count:
            raise ValueError("seats must have exactly seat_count entries")
        return self


class RoomSeatPublic(RoomSeatSpec):
    id: uuid.UUID
    seat: int
    status: str


class RoomPublic(SQLModel):
    id: uuid.UUID
    code: str
    game: str
    seat_count: int
    status: str
    visibility: str
    creator_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    seats: list[RoomSeatPublic]


class RoomsPublic(SQLModel):
    data: list[RoomPublic]
    count: int


# --- runtime settings + feature flags (admin-adjustable, see docs/rooms.md) --


class RuntimeSetting(SQLModel, table=True):
    __tablename__ = "runtime_setting"
    key: str = Field(primary_key=True, max_length=64)
    value: Any = Field(sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=get_datetime_utc, sa_type=_tz())
    updated_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, ondelete="SET NULL"
    )


class RuntimeSettingPublic(SQLModel):
    key: str
    value: Any
    default: Any
    overridden: bool
    description: str


class RuntimeSettingsPublic(SQLModel):
    data: list[RuntimeSettingPublic]


class RuntimeSettingUpdate(SQLModel):
    value: Any


class FeatureFlag(SQLModel, table=True):
    __tablename__ = "feature_flag"
    key: str = Field(primary_key=True, max_length=64)
    enabled: bool = False
    description: str | None = Field(default=None, max_length=255)
    updated_at: datetime = Field(default_factory=get_datetime_utc, sa_type=_tz())
    updated_by: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, ondelete="SET NULL"
    )


class FeatureFlagOverride(SQLModel, table=True):
    __tablename__ = "feature_flag_override"
    __table_args__ = (
        CheckConstraint(
            "(role_id IS NULL) <> (user_id IS NULL)",
            name="ck_feature_flag_override_role_xor_user",
        ),
        # A compound UniqueConstraint over (flag_key, role_id, user_id) would never
        # fire here: exactly one of role_id/user_id is always NULL (see the CHECK
        # above), and NULL is never equal to NULL for uniqueness purposes, so two
        # partial unique indexes -- one per case -- are the only way to actually
        # enforce "one override per flag+role" and "one override per flag+user".
        Index(
            "uq_feature_flag_override_flag_role",
            "flag_key",
            "role_id",
            unique=True,
            postgresql_where=text("role_id IS NOT NULL"),
        ),
        Index(
            "uq_feature_flag_override_flag_user",
            "flag_key",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    flag_key: str = Field(
        foreign_key="feature_flag.key", ondelete="CASCADE", max_length=64
    )
    role_id: uuid.UUID | None = Field(
        default=None, foreign_key="role.id", nullable=True, ondelete="CASCADE"
    )
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, ondelete="CASCADE"
    )
    enabled: bool


class FeatureFlagOverridePublic(SQLModel):
    id: uuid.UUID
    role: str | None
    user_id: uuid.UUID | None
    enabled: bool


class FeatureFlagPublic(SQLModel):
    key: str
    enabled: bool
    effective: bool
    description: str | None
    overrides: list[FeatureFlagOverridePublic]


class FeatureFlagUpdate(SQLModel):
    enabled: bool


class FeatureFlagOverrideCreate(SQLModel):
    role: str | None = None
    user_id: uuid.UUID | None = None
    enabled: bool
