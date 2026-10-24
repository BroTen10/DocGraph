"""Tenant member invitation and role management service."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth.security import hash_password, new_token
from ..auth.session_tokens import revoke_user_sessions
from ..config import settings
from ..models import (
    TenantInvitation,
    TenantMembership,
    UserAccount,
)
from .email_service import EmailDeliveryError, send_email

logger = logging.getLogger(__name__)

INVITATION_TOKEN_HOURS = 72


class MemberOperationError(ValueError):
    """A member operation that should map to a specific HTTP error."""

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


def membership_to_dict(membership: TenantMembership, user: UserAccount) -> dict:
    return {
        "id": membership.id,
        "name": user.name,
        "email": user.email_normalized,
        "phone": user.phone_e164,
        "phone_masked": _mask_phone(user.phone_e164),
        "role": membership.role,
        "status": membership.status,
        "joined_at": membership.joined_at,
        "created_at": membership.created_at,
        "updated_at": membership.updated_at,
    }


def invitation_to_dict(invitation: TenantInvitation) -> dict:
    return {
        "id": invitation.id,
        "name": invitation.name,
        "email": invitation.email_normalized,
        "phone_masked": _mask_phone(invitation.phone_e164),
        "role": invitation.role,
        "status": invitation.status,
        "expires_at": invitation.expires_at,
        "created_at": invitation.created_at,
    }


def send_invitation_email(email: str, url: str) -> None:
    """Deliver the invitation acceptance link."""
    send_email(
        to_email=email,
        subject="加入 DocGraph 租户的邀请",
        plain_text=(
            "你被邀请加入 DocGraph。请打开以下链接设置密码：\n"
            f"{url}\n\n"
            "链接 72 小时内有效。如果这不是你的操作，请忽略本邮件。"
        ),
    )


def invite_member(
    db: Session,
    actor_user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    name: str,
    email: str,
    phone: str,
    role: str,
) -> TenantInvitation:
    """Create a pending invitation for an external email."""
    account = db.execute(
        select(UserAccount.id).where(
            (UserAccount.email_normalized == email)
            | (UserAccount.phone_e164 == phone)
        )
    ).scalar_one_or_none()
    if account is not None:
        raise MemberOperationError("ACCOUNT_EXISTS", "该邮箱或手机号已被注册")

    pending = db.execute(
        select(TenantInvitation).where(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.email_normalized == email,
            TenantInvitation.status.in_(("invited", "resent")),
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise MemberOperationError("INVITE_EXISTS", "该邮箱已有待接受邀请")

    raw_token, token_hash = new_token()
    invitation = TenantInvitation(
        tenant_id=tenant_id,
        name=name.strip(),
        email_normalized=email,
        phone_e164=phone,
        role=role,
        token_hash=token_hash,
        status="invited",
        expires_at=_utcnow() + timedelta(hours=INVITATION_TOKEN_HOURS),
        invited_by=actor_user_id,
    )
    db.add(invitation)
    db.flush()

    url = f"{settings.public_base_url}/accept-invite?token={raw_token}"
    try:
        send_invitation_email(email, url)
    except EmailDeliveryError:
        db.rollback()
        logger.exception("成员邀请邮件发送失败: %s", email)
        raise

    db.commit()
    db.refresh(invitation)
    return invitation


def accept_invitation(db: Session, raw_token: str, password: str) -> None:
    """Accept an invitation and create the account plus membership."""
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    invitation = db.execute(
        select(TenantInvitation).where(
            TenantInvitation.token_hash == token_hash
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise MemberOperationError("INVITE_INVALID", "邀请链接无效")
    if invitation.status == "accepted":
        raise MemberOperationError("INVITE_USED", "邀请链接已使用")
    if invitation.status == "revoked":
        raise MemberOperationError("INVITE_REVOKED", "邀请已被撤销")
    if invitation.expires_at is None or invitation.expires_at < _utcnow():
        raise MemberOperationError("INVITE_EXPIRED", "邀请链接已过期")

    existing = db.execute(
        select(UserAccount.id).where(
            (UserAccount.email_normalized == invitation.email_normalized)
            | (UserAccount.phone_e164 == invitation.phone_e164)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise MemberOperationError("ACCOUNT_EXISTS", "该邮箱或手机号已被注册")

    now = _utcnow()
    user = UserAccount(
        name=invitation.name,
        email_normalized=invitation.email_normalized,
        phone_e164=invitation.phone_e164,
        password_hash=hash_password(password),
        email_verified_at=now,
        terms_accepted_at=now,
    )
    db.add(user)
    db.flush()

    db.add(
        TenantMembership(
            tenant_id=invitation.tenant_id,
            user_id=user.id,
            role=invitation.role,
            status="active",
            invited_by=invitation.invited_by,
            joined_at=now,
        )
    )
    invitation.status = "accepted"
    invitation.accepted_at = now
    db.commit()


def list_members(db: Session, tenant_id: uuid.UUID) -> dict:
    """List active members and pending invitations for one tenant."""
    rows = db.execute(
        select(TenantMembership, UserAccount)
        .join(UserAccount, UserAccount.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(TenantMembership.created_at.desc())
    ).all()
    invitations = db.execute(
        select(TenantInvitation)
        .where(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.status.in_(("invited", "resent")),
        )
        .order_by(TenantInvitation.created_at.desc())
    ).scalars().all()
    return {
        "members": [membership_to_dict(m, u) for m, u in rows],
        "invitations": [invitation_to_dict(i) for i in invitations],
    }


def _has_other_active_admin(
    db: Session,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> bool:
    count = db.execute(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.role == "tenant_admin",
            TenantMembership.status == "active",
            TenantMembership.id != membership_id,
        )
    ).scalar_one()
    return count > 0


def update_member(
    db: Session,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    role: str | None = None,
    status: str | None = None,
) -> dict | None:
    """Change a member's role or status within one tenant."""
    row = db.execute(
        select(TenantMembership, UserAccount)
        .join(UserAccount, UserAccount.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.id == membership_id,
        )
    ).first()
    if row is None:
        return None
    membership, user = row

    will_change_admin = (role is not None and membership.role == "tenant_admin" and role != "tenant_admin") or (
        status == "disabled" and membership.role == "tenant_admin"
    )
    if will_change_admin and not _has_other_active_admin(db, tenant_id, membership_id):
        raise MemberOperationError("LAST_TENANT_ADMIN", "至少需要保留一名有效的租户管理员")

    if role is not None:
        membership.role = role
    if status is not None:
        membership.status = status

    db.commit()
    if status == "disabled":
        revoke_user_sessions(db, user.id)
    return membership_to_dict(membership, user)


def revoke_invitation(
    db: Session,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> bool:
    invitation = db.execute(
        select(TenantInvitation).where(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.id == invitation_id,
        )
    ).scalar_one_or_none()
    if invitation is None:
        return False
    invitation.status = "revoked"
    db.commit()
    return True


def resend_invitation(
    db: Session,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> TenantInvitation | None:
    invitation = db.execute(
        select(TenantInvitation).where(
            TenantInvitation.tenant_id == tenant_id,
            TenantInvitation.id == invitation_id,
        )
    ).scalar_one_or_none()
    if invitation is None or invitation.status == "accepted":
        return None

    raw_token, token_hash = new_token()
    invitation.token_hash = token_hash
    invitation.status = "resent"
    invitation.expires_at = _utcnow() + timedelta(hours=INVITATION_TOKEN_HOURS)
    url = f"{settings.public_base_url}/accept-invite?token={raw_token}"
    try:
        send_invitation_email(invitation.email_normalized, url)
    except EmailDeliveryError:
        db.rollback()
        raise
    db.commit()
    db.refresh(invitation)
    return invitation
