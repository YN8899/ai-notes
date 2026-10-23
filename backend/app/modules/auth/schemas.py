from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize(cls, value):
        return value.lower()


class RegisterIn(LoginIn):
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    invite_code: str = Field(min_length=20, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    display_name: str
    role: Literal["user", "admin"]
    status: Literal["active", "disabled"]
    created_at: datetime


class CsrfOut(BaseModel):
    csrf_token: str


class TabSessionOut(BaseModel):
    user: UserOut
    session_token: str
    csrf_token: str


class OkOut(BaseModel):
    ok: bool = True


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    password: str = Field(min_length=10, max_length=128)


class ResetOut(BaseModel):
    token: str
    expires_at: datetime


class InviteIn(BaseModel):
    label: str = Field(default="", max_length=64)
    max_uses: int = Field(default=1, ge=1, le=100)
    valid_days: int = Field(default=7, ge=1, le=90)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    label: str
    max_uses: int
    used_count: int
    disabled: bool
    expires_at: datetime
    created_at: datetime


class CreatedInvite(InviteOut):
    code: str


class InvitePatch(BaseModel):
    disabled: bool


class UserPatch(BaseModel):
    status: Literal["active", "disabled"]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    actor_id: UUID | None
    action: str
    resource_id: str | None
    request_id: str
    details: dict
    created_at: datetime
