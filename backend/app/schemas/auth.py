"""Authentication request and response schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from ..auth.security import (
    normalize_cn_phone,
    normalize_email,
    validate_password,
)


class RegisterTenantRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=100)
    admin_name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    phone: str
    password: str
    accept_terms: bool

    @field_validator("email")
    @classmethod
    def _canonical_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("phone")
    @classmethod
    def _canonical_phone(cls, value: str) -> str:
        return normalize_cn_phone(value)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        validate_password(value)
        return value

    @field_validator("accept_terms")
    @classmethod
    def _accepted_terms(cls, value: bool) -> bool:
        if not value:
            raise ValueError("请先同意服务条款与隐私告知")
        return value


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class RegisterResponse(BaseModel):
    message: str
    status: str


class VerifyEmailResponse(BaseModel):
    message: str
    status: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginUserOut(BaseModel):
    id: str
    name: str
    email: str
    phone_masked: str
    role: str | None = None


class LoginTenantOut(BaseModel):
    id: str
    name: str


class LoginResponse(BaseModel):
    message: str
    csrf_token: str
    user: LoginUserOut
    tenant: LoginTenantOut | None
    role: str | None
    permissions: list[str] = Field(default_factory=list)


class MeResponse(LoginResponse):
    pass


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _valid_new_password(cls, value: str) -> str:
        validate_password(value)
        return value


class ResetPasswordResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _valid_new_password(cls, value: str) -> str:
        validate_password(value)
        return value


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    phone: str | None = None


class ProfileUpdateResponse(BaseModel):
    message: str
    user: LoginUserOut
