"""Platform tenant approval and administration service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.security import hash_password, normalize_email, validate_password
from ..auth.session_tokens import revoke_tenant_sessions
from ..models import (
    Tenant,
    TenantMembership,
    TenantRegistrationRequest,
    UserAccount,
)
from .email_service import EmailDeliveryError, send_email


class PlatformOperationError(ValueError):
    """A stable error code for platform review conflicts."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mask_phone(phone_e164: str) -> str:
    national = phone_e164[3:]
    if len(national) < 8:
        return phone_e164
    return f"{national[:3]}****{national[-4:]}"


def create_platform_admin(email: str, password: str) -> UserAccount:
    """Create the first platform administrator from an operator-supplied password."""
    canonical_email = normalize_email(email)
    validate_password(password)
    from ..database import SessionLocal

    with SessionLocal() as db:
        existing = db.execute(
            select(UserAccount).where(
                UserAccount.email_normalized == canonical_email
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise PlatformOperationError("ADMIN_EXISTS", "平台管理员已存在")

        # Platform admins still need a globally unique mainland phone even though
        # it is never used for tenant callbacks.
        admin = UserAccount(
            name="平台管理员",
            email_normalized=canonical_email,
            phone_e164="+8613000000000",
            password_hash=hash_password(password),
            email_verified_at=_utcnow(),
            is_platform_admin=True,
        )
        db.add(admin)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise PlatformOperationError(
                "ADMIN_CONFLICT",
                "邮箱或默认手机号与既有账号冲突",
            )
        db.refresh(admin)
        return admin


def list_registrations(
    db: Session,
    status: str | None = None,
) -> list[TenantRegistrationRequest]:
    stmt = select(TenantRegistrationRequest).order_by(
        TenantRegistrationRequest.created_at.desc()
    )
    if status is not None:
        stmt = stmt.where(TenantRegistrationRequest.status == status)
    else:
        stmt = stmt.where(
            TenantRegistrationRequest.status.in_(
                ("pending_review", "approved", "rejected")
            )
        )
    return list(db.execute(stmt).scalars().all())


def get_registration(
    db: Session,
    registration_id: uuid.UUID,
) -> TenantRegistrationRequest | None:
    return db.get(TenantRegistrationRequest, registration_id)


def approve_registration(
    db: Session,
    actor_id: uuid.UUID,
    registration_id: uuid.UUID,
    note: str | None,
) -> dict:
    """Approve a verified application, atomically creating tenant and admin."""
    request = get_registration(db, registration_id)
    if request is None:
        raise PlatformOperationError("REGISTRATION_NOT_FOUND", "注册申请不存在")
    if request.status == "approved":
        raise PlatformOperationError(
            "REGISTRATION_ALREADY_REVIEWED",
            "注册申请已审核通过",
        )
    if request.status != "pending_review":
        raise PlatformOperationError("REGISTRATION_NOT_REVIEWABLE", "注册申请不在待审核状态")
    if request.email_verified_at is None:
        raise PlatformOperationError(
            "REGISTRATION_NOT_VERIFIED",
            "注册邮箱尚未完成验证",
        )

    conflict = db.execute(
        select(UserAccount.id).where(
            (UserAccount.email_normalized == request.email_normalized)
            | (UserAccount.phone_e164 == request.phone_e164)
        )
    ).scalar_one_or_none()
    if conflict is not None:
        raise PlatformOperationError(
            "ACCOUNT_CONFLICT",
            "该邮箱或手机号已存在正式账号",
        )

    now = _utcnow()
    tenant = Tenant(name=request.organization_name, status="active")
    db.add(tenant)
    db.flush()

    user = UserAccount(
        name=request.admin_name,
        email_normalized=request.email_normalized,
        phone_e164=request.phone_e164,
        password_hash=request.password_hash,
        email_verified_at=request.email_verified_at,
        terms_accepted_at=request.terms_accepted_at,
    )
    db.add(user)
    db.flush()

    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role="tenant_admin",
        status="active",
        joined_at=now,
    )
    db.add(membership)

    tenant.approved_by = actor_id
    tenant.approved_at = now
    request.status = "approved"
    request.reviewed_by = actor_id
    request.reviewed_at = now
    if note:
        request.rejection_reason = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise PlatformOperationError(
            "APPROVAL_CONFLICT",
            "审核通过时发生数据冲突，请重试",
        )
    return {
        "tenant_id": tenant.id,
        "tenant_status": tenant.status,
        "admin_role": membership.role,
    }


def reject_registration(
    db: Session,
    actor_id: uuid.UUID,
    registration_id: uuid.UUID,
    reason: str,
) -> dict:
    request = get_registration(db, registration_id)
    if request is None:
        raise PlatformOperationError("REGISTRATION_NOT_FOUND", "注册申请不存在")
    if request.status == "approved":
        raise PlatformOperationError(
            "REGISTRATION_ALREADY_REVIEWED",
            "注册申请已审核通过",
        )
    if request.status == "rejected":
        raise PlatformOperationError(
            "REGISTRATION_ALREADY_REVIEWED",
            "注册申请已驳回",
        )
    if request.status != "pending_review":
        raise PlatformOperationError("REGISTRATION_NOT_REVIEWABLE", "注册申请不在待审核状态")

    try:
        send_email(
            to_email=request.email_normalized,
            subject="DocGraph 租户注册审核结果",
            plain_text=(
                f"你提交的组织“{request.organization_name}”注册申请未通过审核。\n"
                f"驳回原因：{reason}\n"
                "如需重新申请，请联系平台管理员。"
            ),
        )
    except EmailDeliveryError:
        db.rollback()
        raise

    request.status = "rejected"
    request.rejection_reason = reason
    request.reviewed_by = actor_id
    request.reviewed_at = _utcnow()
    db.commit()
    return {"status": request.status}


def list_tenants(db: Session) -> list[dict]:
    member_count = (
        select(
            TenantMembership.tenant_id,
            func.count(TenantMembership.id).label("count"),
        )
        .group_by(TenantMembership.tenant_id)
        .subquery()
    )
    rows = db.execute(
        select(Tenant, func.coalesce(member_count.c.count, 0))
        .outerjoin(member_count, member_count.c.tenant_id == Tenant.id)
        .order_by(Tenant.created_at.desc())
    ).all()
    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "status": tenant.status,
            "approved_at": tenant.approved_at,
            "created_at": tenant.created_at,
            "member_count": int(count),
        }
        for tenant, count in rows
    ]


def set_tenant_status(
    db: Session,
    tenant_id: uuid.UUID,
    status: str,
) -> dict:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise PlatformOperationError("TENANT_NOT_FOUND", "租户不存在")
    tenant.status = status
    db.commit()
    if status in {"suspended", "archived"}:
        revoke_tenant_sessions(db, tenant_id)
    return {"id": tenant.id, "status": tenant.status}
