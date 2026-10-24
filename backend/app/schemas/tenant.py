"""Tenant member and invitation schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ..auth.security import normalize_cn_phone, normalize_email, validate_password

ALLOWED_ROLES = {"tenant_admin", "reviewer", "viewer"}


class InviteMemberRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    phone: str
    role: str

    @field_validator("email")
    @classmethod
    def _canonical_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("phone")
    @classmethod
    def _canonical_phone(cls, value: str) -> str:
        return normalize_cn_phone(value)

    @field_validator("role")
    @classmethod
    def _valid_role(cls, value: str) -> str:
        if value not in ALLOWED_ROLES:
            raise ValueError("无效的成员角色")
        return value


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        validate_password(value)
        return value


class MemberUpdateRequest(BaseModel):
    role: str | None = None
    status: str | None = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_ROLES:
            raise ValueError("无效的成员角色")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"active", "disabled"}:
            raise ValueError("无效的成员状态")
        return value


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    phone: str
    phone_masked: str
    role: str
    status: str
    joined_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    phone_masked: str
    role: str
    status: str
    expires_at: datetime | None = None
    created_at: datetime


class MemberListResponse(BaseModel):
    members: list[MemberOut]
    invitations: list[InvitationOut]


class AcceptInviteResponse(BaseModel):
    message: str
