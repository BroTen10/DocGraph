"""Tenant registration and email verification service."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.security import hash_password, new_token
from ..config import settings
from ..models import (
    AuthToken,
    TenantRegistrationRequest,
    UserAccount,
)
from ..schemas.auth import RegisterTenantRequest
from .email_service import EmailDeliveryError, send_email

logger = logging.getLogger(__name__)

VERIFICATION_TOKEN_HOURS = 24


def _utcnow() -> datetime:
    """Return naive UTC aligned with the database DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def send_verification_email(email: str, url: str) -> None:
    """Deliver the registration verification link for a tenant application."""
    send_email(
        to_email=email,
        subject="请验证你的 DocGraph 注册邮箱",
        plain_text=(
            "请打开以下链接完成邮箱验证并进入平台审核队列：\n"
            f"{url}\n\n"
            "如果这不是你的操作，请忽略本邮件。"
        ),
    )


def register_tenant(
    db: Session,
    payload: RegisterTenantRequest,
) -> TenantRegistrationRequest | None:
    """Create a tenant application in ``pending_email`` state.

    Duplicate emails or phones return ``None`` without creating a row so the
    API can give a generic accepted response and avoid account enumeration.
    """
    duplicate = db.execute(
        select(TenantRegistrationRequest.id)
        .where(
            (TenantRegistrationRequest.email_normalized == payload.email)
            | (TenantRegistrationRequest.phone_e164 == payload.phone)
        )
        .limit(1)
    ).scalar_one_or_none()
    if duplicate is not None:
        return None

    account = db.execute(
        select(UserAccount.id)
        .where(
            (UserAccount.email_normalized == payload.email)
            | (UserAccount.phone_e164 == payload.phone)
        )
        .limit(1)
    ).scalar_one_or_none()
    if account is not None:
        return None

    request = TenantRegistrationRequest(
        organization_name=payload.organization_name.strip(),
        admin_name=payload.admin_name.strip(),
        email_normalized=payload.email,
        phone_e164=payload.phone,
        password_hash=hash_password(payload.password),
        status="pending_email",
        terms_accepted_at=_utcnow(),
    )
    db.add(request)
    db.flush()

    raw_token, token_hash = new_token()
    db.add(
        AuthToken(
            purpose="verify_registration",
            subject_id=request.id,
            token_hash=token_hash,
            expires_at=_utcnow() + timedelta(hours=VERIFICATION_TOKEN_HOURS),
        )
    )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None

    url = f"{settings.public_base_url}/verify-email?token={raw_token}"
    try:
        send_verification_email(payload.email, url)
    except EmailDeliveryError:
        logger.exception("注册验证邮件发送失败: %s", payload.email)

    return request


def verify_registration_token(db: Session, raw_token: str) -> str:
    """Mark a verified registration as pending platform review."""
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token = db.execute(
        select(AuthToken).where(
            AuthToken.purpose == "verify_registration",
            AuthToken.token_hash == token_hash,
        )
    ).scalar_one_or_none()

    if token is None:
        raise ValueError("验证链接无效")
    if token.used_at is not None:
        raise ValueError("验证链接已使用")
    if token.expires_at is None or token.expires_at < _utcnow():
        raise ValueError("验证链接已过期")

    request = db.get(TenantRegistrationRequest, token.subject_id)
    if request is None:
        raise ValueError("注册申请不存在")
    if request.status != "pending_email":
        raise ValueError("注册申请状态不允许验证")

    now = _utcnow()
    request.status = "pending_review"
    request.email_verified_at = now
    token.used_at = now
    db.commit()
    return request.status

