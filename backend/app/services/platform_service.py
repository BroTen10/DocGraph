"""Platform tenant approval and administration service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.security import (
    hash_password,
    normalize_cn_phone,
    normalize_email,
    validate_password,
    verify_password,
)
from ..auth.session_tokens import revoke_tenant_sessions
from ..models import (
    Tenant,
    TenantMembership,
    TenantRegistrationRequest,
    UserAccount,
)
from ..migrations.security_v1 import DEFAULT_TENANT_CODE
from .email_service import EmailDeliveryError, send_email
from . import audit_service


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


def create_platform_admin(email: str, phone: str, password: str) -> UserAccount:
    """Create the first platform administrator from an operator-supplied password."""
    try:
        canonical_email = normalize_email(email)
        canonical_phone = normalize_cn_phone(phone)
        validate_password(password)
    except ValueError as exc:
        raise PlatformOperationError("INVALID_ADMIN_INPUT", str(exc)) from exc

    from ..database import SessionLocal

    with SessionLocal() as db:
        existing = db.execute(
            select(UserAccount).where(
                UserAccount.email_normalized == canonical_email
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise PlatformOperationError("ADMIN_EXISTS", "平台管理员已存在")

        admin = UserAccount(
            name="平台管理员",
            email_normalized=canonical_email,
            phone_e164=canonical_phone,
            password_hash=hash_password(password),
            email_verified_at=_utcnow(),
            is_platform_admin=True,
        )
        db.add(admin)
        db.flush()
        audit_service.record(
            db,
            None,
            admin.id,
            "platform.admin.bootstrap",
            target_type="user",
            target_id=admin.id,
            result="success",
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise PlatformOperationError(
                "ADMIN_CONFLICT",
                "邮箱或手机号与既有账号冲突",
            )
        db.refresh(admin)
        return admin


def create_default_tenant_admin(
    email: str,
    phone: str,
    password: str,
    *,
    name: str = "默认租户管理员",
) -> UserAccount:
    """Provision the tenant administrator that owns migrated legacy data.

    This is intentionally separate from platform-admin provisioning: platform
    operators do not receive tenant business permissions.
    """
    try:
        canonical_email = normalize_email(email)
        canonical_phone = normalize_cn_phone(phone)
        validate_password(password)
    except ValueError as exc:
        raise PlatformOperationError("INVALID_ADMIN_INPUT", str(exc)) from exc

    from ..database import SessionLocal

    with SessionLocal() as db:
        tenant = db.execute(
            select(Tenant).where(Tenant.code == DEFAULT_TENANT_CODE)
        ).scalar_one_or_none()
        if tenant is None:
            raise PlatformOperationError(
                "DEFAULT_TENANT_MISSING",
                "默认租户不存在，请先完成数据库迁移",
            )

        existing = db.execute(
            select(UserAccount).where(
                (UserAccount.email_normalized == canonical_email)
                | (UserAccount.phone_e164 == canonical_phone)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.email_normalized != canonical_email
                or existing.phone_e164 != canonical_phone
            ):
                raise PlatformOperationError(
                    "ADMIN_CONFLICT",
                    "邮箱或手机号与既有账号冲突",
                )
            if existing.is_platform_admin:
                raise PlatformOperationError(
                    "PLATFORM_ADMIN_CONFLICT",
                    "平台管理员不能兼任租户管理员，请使用独立的租户邮箱",
                )
            if not verify_password(existing.password_hash, password):
                raise PlatformOperationError(
                    "ADMIN_PASSWORD_MISMATCH",
                    "既有账号密码校验失败，无法绑定默认租户",
                )
            user = existing
        else:
            user = UserAccount(
                name=name.strip() or "默认租户管理员",
                email_normalized=canonical_email,
                phone_e164=canonical_phone,
                password_hash=hash_password(password),
                email_verified_at=_utcnow(),
                terms_accepted_at=_utcnow(),
            )
            db.add(user)
            db.flush()

        membership = db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        ).scalar_one_or_none()
        if membership is None:
            membership = TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                role="tenant_admin",
                status="active",
                joined_at=_utcnow(),
            )
            db.add(membership)
        else:
            membership.role = "tenant_admin"
            membership.status = "active"
        db.flush()

        audit_service.record(
            db,
            tenant.id,
            user.id,
            "tenant.admin.bootstrap",
            target_type="tenant_member",
            target_id=membership.id,
            result="success",
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise PlatformOperationError(
                "ADMIN_CONFLICT",
                "初始化默认租户管理员时发生数据冲突",
            )
        db.refresh(user)
        return user


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
        stmt = stmt.where(TenantRegistrationRequest.status == "pending_review")
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
    from .settings_service import seed_tenant_defaults

    seed_tenant_defaults(db, tenant.id)

    tenant.approved_by = actor_id
    tenant.approved_at = now
    request.status = "approved"
    request.reviewed_by = actor_id
    request.reviewed_at = now
    request.review_note = (note or "").strip() or None
    request.rejection_reason = None
    audit_service.record(
        db,
        tenant.id,
        actor_id,
        "tenant.approved",
        target_type="tenant_registration",
        target_id=request.id,
        result="success",
        metadata={"tenant_id": str(tenant.id)},
    )
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
    audit_service.record(
        db,
        None,
        actor_id,
        "tenant.rejected",
        target_type="tenant_registration",
        target_id=request.id,
        result="success",
    )
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
    admin_contact = (
        select(
            TenantMembership.tenant_id,
            TenantMembership.role.label("admin_role"),
            UserAccount.name.label("admin_name"),
            UserAccount.email_normalized.label("admin_email"),
            UserAccount.phone_e164.label("admin_phone_e164"),
            func.row_number().over(
                partition_by=TenantMembership.tenant_id,
                order_by=(TenantMembership.created_at, TenantMembership.id),
            ).label("admin_rank"),
        )
        .join(UserAccount, UserAccount.id == TenantMembership.user_id)
        .where(
            TenantMembership.role == "tenant_admin",
            TenantMembership.status == "active",
        )
        .subquery()
    )
    rows = db.execute(
        select(
            Tenant,
            func.coalesce(member_count.c.count, 0),
            admin_contact.c.admin_role,
            admin_contact.c.admin_name,
            admin_contact.c.admin_email,
            admin_contact.c.admin_phone_e164,
        )
        .outerjoin(member_count, member_count.c.tenant_id == Tenant.id)
        .outerjoin(
            admin_contact,
            (admin_contact.c.tenant_id == Tenant.id)
            & (admin_contact.c.admin_rank == 1),
        )
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
            "admin_role": admin_role,
            "admin_name": admin_name,
            "admin_email": admin_email,
            "admin_phone_masked": (
                _mask_phone(admin_phone_e164) if admin_phone_e164 else None
            ),
        }
        for tenant, count, admin_role, admin_name, admin_email, admin_phone_e164 in rows
    ]


def set_tenant_status(
    db: Session,
    actor_user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    status: str,
) -> dict:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise PlatformOperationError("TENANT_NOT_FOUND", "租户不存在")
    old_status = tenant.status
    tenant.status = status
    audit_service.record(
        db,
        tenant.id,
        actor_user_id,
        "tenant.status.update",
        target_type="tenant",
        target_id=tenant.id,
        result="success",
        metadata={"from_status": old_status, "to_status": status},
    )
    db.commit()
    if status in {"suspended", "archived"}:
        revoke_tenant_sessions(db, tenant_id)
    return {"id": tenant.id, "status": tenant.status}
