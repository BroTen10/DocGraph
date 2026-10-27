"""Platform administration request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RegistrationReviewOut(BaseModel):
    id: UUID
    organization_name: str
    admin_name: str
    email: str
    phone_masked: str
    status: str
    email_verified_at: datetime | None = None
    terms_accepted_at: datetime | None = None
    review_note: str | None = None
    rejection_reason: str | None = None
    created_at: datetime


class RegistrationListResponse(BaseModel):
    registrations: list[RegistrationReviewOut]
    total: int


class ApproveRegistrationRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class RejectRegistrationRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class ReviewActionResponse(BaseModel):
    message: str
    tenant_id: UUID | None = None
    tenant_status: str | None = None
    admin_role: str | None = None
    status: str


class TenantOut(BaseModel):
    id: UUID
    name: str
    status: str
    approved_at: datetime | None = None
    created_at: datetime
    member_count: int = 0
    admin_role: Literal["tenant_admin"] | None = None
    admin_name: str | None = None
    admin_email: str | None = None
    admin_phone_masked: str | None = None


class TenantListResponse(BaseModel):
    tenants: list[TenantOut]
    total: int


class TenantStatusUpdateRequest(BaseModel):
    status: Literal["active", "suspended", "archived"]
