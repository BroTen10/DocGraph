"""Platform administrator endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_platform_admin
from ..database import get_db
from ..schemas.platform import (
    ApproveRegistrationRequest,
    RejectRegistrationRequest,
    RegistrationListResponse,
    RegistrationReviewOut,
    ReviewActionResponse,
    TenantListResponse,
    TenantOut,
    TenantStatusUpdateRequest,
)
from ..services import platform_service
from ..services.email_service import EmailDeliveryError
from ..services.platform_service import PlatformOperationError

router = APIRouter(prefix="/api/platform", tags=["platform"])


def _registration_out(row) -> RegistrationReviewOut:
    return RegistrationReviewOut(
        id=row.id,
        organization_name=row.organization_name,
        admin_name=row.admin_name,
        email=row.email_normalized,
        phone_masked=platform_service._mask_phone(row.phone_e164),
        status=row.status,
        email_verified_at=row.email_verified_at,
        terms_accepted_at=row.terms_accepted_at,
        review_note=row.review_note,
        rejection_reason=row.rejection_reason,
        created_at=row.created_at,
    )


@router.get("/registrations", response_model=RegistrationListResponse)
def list_registrations(
    status: str | None = None,
    ctx: AuthContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> RegistrationListResponse:
    rows = platform_service.list_registrations(db, status)
    return RegistrationListResponse(
        registrations=[_registration_out(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "/registrations/{registration_id}/approve",
    response_model=ReviewActionResponse,
)
def approve_registration(
    registration_id: uuid.UUID,
    payload: ApproveRegistrationRequest,
    ctx: AuthContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> ReviewActionResponse:
    try:
        result = platform_service.approve_registration(
            db,
            ctx.user.id,
            registration_id,
            payload.note,
        )
    except PlatformOperationError as exc:
        return JSONResponse(
            status_code=409 if exc.code != "REGISTRATION_NOT_FOUND" else 404,
            content={"code": exc.code, "message": exc.message},
        )
    return ReviewActionResponse(
        message="租户已开通",
        status="approved",
        **result,
    )


@router.post(
    "/registrations/{registration_id}/reject",
    response_model=ReviewActionResponse,
)
def reject_registration(
    registration_id: uuid.UUID,
    payload: RejectRegistrationRequest,
    ctx: AuthContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> ReviewActionResponse:
    try:
        result = platform_service.reject_registration(
            db,
            ctx.user.id,
            registration_id,
            payload.reason,
        )
    except EmailDeliveryError:
        return JSONResponse(
            status_code=503,
            content={
                "code": "EMAIL_DELIVERY_FAILED",
                "message": "审核结果邮件发送失败，请稍后重试",
            },
        )
    except PlatformOperationError as exc:
        return JSONResponse(
            status_code=409 if exc.code != "REGISTRATION_NOT_FOUND" else 404,
            content={"code": exc.code, "message": exc.message},
        )
    return ReviewActionResponse(
        message="注册申请已驳回",
        status=result["status"],
    )


@router.get("/tenants", response_model=TenantListResponse)
def list_tenants(
    ctx: AuthContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> TenantListResponse:
    rows = platform_service.list_tenants(db)
    tenants = [
        TenantOut(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            approved_at=row["approved_at"],
            created_at=row["created_at"],
            member_count=row["member_count"],
            admin_role=row["admin_role"],
            admin_name=row["admin_name"],
            admin_email=row["admin_email"],
            admin_phone_masked=row["admin_phone_masked"],
        )
        for row in rows
    ]
    return TenantListResponse(tenants=tenants, total=len(tenants))


@router.patch("/tenants/{tenant_id}/status", response_model=dict)
def set_tenant_status(
    tenant_id: uuid.UUID,
    payload: TenantStatusUpdateRequest,
    ctx: AuthContext = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = platform_service.set_tenant_status(
            db,
            ctx.user.id,
            tenant_id,
            payload.status,
        )
    except PlatformOperationError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    return {
        "id": str(result["id"]),
        "status": result["status"],
    }
