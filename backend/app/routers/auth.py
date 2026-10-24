"""Public authentication endpoints for registration and email verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.auth import (
    RegisterResponse,
    RegisterTenantRequest,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from ..services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=202)
def register_tenant(
    payload: RegisterTenantRequest,
    db: Session = Depends(get_db),
) -> RegisterResponse:
    """Submit a new tenant application and queue email verification."""
    auth_service.register_tenant(db, payload)
    return RegisterResponse(
        message="申请已提交，请前往邮箱完成验证",
        status="pending_email",
    )


@router.post("/verify-email", response_model=VerifyEmailResponse)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> VerifyEmailResponse:
    """Verify an email and move the application into review queue."""
    try:
        status = auth_service.verify_registration_token(db, payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return VerifyEmailResponse(
        message="邮箱已验证，请等待平台管理员审核",
        status=status,
    )

