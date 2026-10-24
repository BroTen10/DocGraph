"""Tenant registration and email verification service."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.security import (
    hash_password,
    new_token,
    normalize_cn_phone,
    normalize_email,
    validate_password,
    verify_password,
)
from ..auth.session_tokens import (
    AuthSessionContext,
    create_session,
    get_active_session,
    revoke_session,
    revoke_user_sessions,
)
from ..config import settings
from ..models import (
    AuthToken,
    Tenant,
    TenantMembership,
    TenantRegistrationRequest,
    UserAccount,
)
from ..schemas.auth import RegisterTenantRequest
from .email_service import EmailDeliveryError, send_email
from . import audit_service

logger = logging.getLogger(__name__)

VERIFICATION_TOKEN_HOURS = 24
RESET_TOKEN_MINUTES = 30
MAX_LOGIN_FAILURES = 5
LOCK_MINUTES = 15
_DUMMY_PASSWORD_HASH = hash_password("DummyPassword2026")


class LoginDeniedError(Exception):
    """A stable code for login denials that need a 403 response."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utcnow() -> datetime:
    """Return naive UTC aligned with the database DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mask_phone(phone_e164: str) -> str:
    """Return a masked phone like ``136****6000``."""
    national = phone_e164[3:]
    if len(national) < 8:
        return phone_e164
    return f"{national[:3]}****{national[-4:]}"


def _login_context_for_user(db: Session, user: UserAccount) -> tuple:
    """Resolve tenant and membership context for an authenticated user."""
    if user.is_platform_admin:
        return None, None

    membership = db.execute(
        select(TenantMembership)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
        .order_by(TenantMembership.created_at, TenantMembership.id)
        .limit(1)
    ).scalar_one_or_none()
    if membership is None:
        audit_service.record(
            db, None, user.id, "auth.login.denied", result="denied",
            metadata={"reason": "membership_missing"},
        )
        db.commit()
        raise LoginDeniedError("ACCOUNT_UNAVAILABLE", "账号未开通")
    if membership.status != "active":
        audit_service.record(
            db, membership.tenant_id, user.id, "auth.login.denied",
            result="denied", metadata={"reason": "member_disabled"},
        )
        db.commit()
        raise LoginDeniedError("MEMBER_DISABLED", "账号已停用")

    tenant = db.get(Tenant, membership.tenant_id)
    if tenant is None:
        audit_service.record(
            db, membership.tenant_id, user.id, "auth.login.denied",
            result="denied", metadata={"reason": "tenant_missing"},
        )
        db.commit()
        raise LoginDeniedError("ACCOUNT_UNAVAILABLE", "账号所属租户不存在")
    if tenant.status != "active":
        audit_service.record(
            db, tenant.id, user.id, "auth.login.denied", result="denied",
            metadata={"reason": "tenant_inactive"},
        )
        db.commit()
        raise LoginDeniedError("TENANT_SUSPENDED", "租户已暂停，请联系平台管理员")

    return tenant, membership


def authenticate(
    db: Session,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuthSessionContext:
    """Verify credentials and create a revocable server-side session."""
    try:
        canonical_email = normalize_email(email)
    except ValueError:
        audit_service.record(
            db, None, None, "auth.login.failed", result="failure",
            metadata={"reason": "invalid_credentials"},
        )
        db.commit()
        raise ValueError("邮箱或密码错误")

    user = db.execute(
        select(UserAccount).where(UserAccount.email_normalized == canonical_email)
    ).scalar_one_or_none()
    if user is None:
        verify_password(_DUMMY_PASSWORD_HASH, password)
        audit_service.record(
            db, None, None, "auth.login.failed", result="failure",
            metadata={"reason": "invalid_credentials"},
        )
        db.commit()
        raise ValueError("邮箱或密码错误")

    now = _utcnow()
    if user.locked_until is not None and user.locked_until > now:
        verify_password(user.password_hash, password)
        audit_service.record(
            db, None, user.id, "auth.login.locked", result="failure",
            metadata={"reason": "account_locked"},
        )
        db.commit()
        raise ValueError("邮箱或密码错误")

    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        lock_triggered = user.failed_login_count >= MAX_LOGIN_FAILURES
        if lock_triggered:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
        audit_service.record(
            db, None, user.id, "auth.login.failed", result="failure",
            metadata={
                "reason": "invalid_credentials",
                "failed_attempts": user.failed_login_count,
            },
        )
        if lock_triggered:
            audit_service.record(
                db, None, user.id, "auth.account.locked",
                target_type="user", target_id=user.id, result="failure",
                metadata={"lock_minutes": LOCK_MINUTES},
            )
        db.commit()
        raise ValueError("邮箱或密码错误")

    if not user.is_platform_admin and user.email_verified_at is None:
        audit_service.record(
            db, None, user.id, "auth.login.denied", result="denied",
            metadata={"reason": "email_unverified"},
        )
        db.commit()
        raise ValueError("账号邮箱未验证")

    tenant, membership = _login_context_for_user(db, user)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    audit_service.record(
        db,
        tenant.id if tenant else None,
        user.id,
        "auth.login.success",
        target_type="user",
        target_id=user.id,
        result="success",
        metadata={"platform_admin": user.is_platform_admin},
    )

    try:
        session, raw_session_token, raw_csrf_token = create_session(
            db,
            user,
            tenant_id=tenant.id if tenant else None,
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        raise
    return AuthSessionContext(
        session=session,
        raw_session_token=raw_session_token,
        raw_csrf_token=raw_csrf_token,
        user=user,
        tenant=tenant,
        membership=membership,
    )


def request_password_reset(db: Session, email: str) -> bool:
    """Create and deliver a one-time reset token when the account is valid."""
    try:
        canonical_email = normalize_email(email)
    except ValueError:
        return False
    user = db.execute(
        select(UserAccount).where(UserAccount.email_normalized == canonical_email)
    ).scalar_one_or_none()
    if user is None or user.email_verified_at is None:
        return False

    raw_token, token_hash = new_token()
    db.add(
        AuthToken(
            user_id=user.id,
            subject_id=user.id,
            purpose="reset_password",
            token_hash=token_hash,
            expires_at=_utcnow() + timedelta(minutes=RESET_TOKEN_MINUTES),
        )
    )
    audit_service.record(
        db, None, user.id, "auth.password.reset_requested",
        target_type="user", target_id=user.id, result="success",
    )
    url = f"{settings.public_base_url}/reset-password#token={raw_token}"
    try:
        send_email(
            to_email=user.email_normalized,
            subject="重置你的 DocGraph 登录密码",
            plain_text=(
                "请打开以下链接重置密码，链接 30 分钟内有效：\n"
                f"{url}\n\n"
                "如果这不是你的操作，请忽略本邮件。"
            ),
        )
    except EmailDeliveryError:
        db.rollback()
        return False
    db.commit()
    return True


def reset_password(db: Session, raw_token: str, new_password: str) -> None:
    """Consume a reset token, set the new password and revoke sessions."""
    validate_password(new_password)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token = db.execute(
        select(AuthToken).where(
            AuthToken.purpose == "reset_password",
            AuthToken.token_hash == token_hash,
        )
    ).scalar_one_or_none()
    if token is None:
        raise ValueError("重置链接无效")
    if token.used_at is not None:
        raise ValueError("重置链接已使用")
    if token.expires_at is None or token.expires_at < _utcnow():
        raise ValueError("重置链接已过期")
    if token.user_id is None:
        raise ValueError("重置链接无效")

    user = db.get(UserAccount, token.user_id)
    if user is None:
        raise ValueError("重置链接无效")

    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    token.used_at = _utcnow()
    audit_service.record(
        db, None, user.id, "auth.password.reset",
        target_type="user", target_id=user.id, result="success",
    )
    db.commit()
    revoke_user_sessions(db, user.id)


def change_password(
    db: Session,
    user: UserAccount,
    current_password: str,
    new_password: str,
) -> None:
    """Change a password after re-authenticating with the current one."""
    if not verify_password(user.password_hash, current_password):
        raise ValueError("当前密码不正确")
    user.password_hash = hash_password(new_password)
    audit_service.record(
        db, None, user.id, "auth.password.change",
        target_type="user", target_id=user.id, result="success",
    )
    db.commit()
    revoke_user_sessions(db, user.id)


def update_profile(
    db: Session,
    user: UserAccount,
    *,
    tenant_id=None,
    name: str | None = None,
    phone: str | None = None,
) -> UserAccount:
    """Update self-service profile fields with normalization and auditing."""
    changed: dict[str, str] = {}
    if name is not None:
        canonical_name = name.strip()
        if not 1 <= len(canonical_name) <= 50:
            raise ValueError("姓名长度必须为 1-50 个字符")
        if canonical_name != user.name:
            changed["name"] = canonical_name
            user.name = canonical_name
    if phone is not None:
        canonical_phone = normalize_cn_phone(phone)
        if canonical_phone != user.phone_e164:
            conflict = db.execute(
                select(UserAccount.id).where(
                    UserAccount.phone_e164 == canonical_phone,
                    UserAccount.id != user.id,
                )
            ).scalar_one_or_none()
            if conflict is not None:
                raise ValueError("该手机号已被其他账号使用")
            changed["phone"] = "updated"
            user.phone_e164 = canonical_phone
    if changed:
        audit_service.record(
            db,
            tenant_id,
            user.id,
            "profile.updated",
            target_type="user",
            target_id=user.id,
            result="success",
            metadata={"fields": sorted(changed)},
        )
        db.commit()
        db.refresh(user)
    return user


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
    audit_service.record(
        db, None, None, "registration.submitted",
        target_type="tenant_registration",
        target_id=request.id,
        result="success",
        metadata={"status": request.status},
    )

    raw_token, token_hash = new_token()
    db.add(
        AuthToken(
            purpose="verify_registration",
            subject_id=request.id,
            token_hash=token_hash,
            expires_at=_utcnow() + timedelta(hours=VERIFICATION_TOKEN_HOURS),
        )
    )

    url = f"{settings.public_base_url}/verify-email#token={raw_token}"
    try:
        send_verification_email(payload.email, url)
    except EmailDeliveryError:
        db.rollback()
        logger.exception("注册验证邮件发送失败: %s", payload.email)
        raise

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None

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
    audit_service.record(
        db, None, None, "registration.email_verified",
        target_type="tenant_registration",
        target_id=request.id,
        result="success",
    )
    db.commit()
    return request.status
