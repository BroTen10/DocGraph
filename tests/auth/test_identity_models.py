from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditLog,
    AuthSession,
    AuthToken,
    Tenant,
    TenantInvitation,
    TenantMembership,
    TenantRegistrationRequest,
    UserAccount,
)


def _make_tenant(db, name="甲公司"):
    tenant = Tenant(name=name, status="active")
    db.add(tenant)
    db.flush()
    return tenant


def _make_user(db, email, phone):
    user = UserAccount(
        name="张三",
        email_normalized=email,
        phone_e164=phone,
        password_hash="argon2-hash",
    )
    db.add(user)
    db.flush()
    return user


def test_identity_objects_can_be_persisted(migrated_db):
    tenant = _make_tenant(migrated_db)
    user = _make_user(migrated_db, "zhang@example.com", "+8613800138000")
    member = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role="tenant_admin",
        status="active",
    )
    migrated_db.add(member)
    migrated_db.commit()
    assert member.id


def test_membership_is_unique_per_tenant_and_user(migrated_db):
    tenant = _make_tenant(migrated_db, "乙公司")
    user = _make_user(migrated_db, "li@example.com", "+8613900139000")
    migrated_db.add_all(
        [
            TenantMembership(
                tenant_id=tenant.id, user_id=user.id, role="reviewer", status="active"
            ),
            TenantMembership(
                tenant_id=tenant.id, user_id=user.id, role="viewer", status="active"
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        migrated_db.commit()


def test_registration_invitation_session_token_and_audit_persist(migrated_db):
    tenant = _make_tenant(migrated_db, "丙公司")
    registration = TenantRegistrationRequest(
        organization_name="丙公司",
        admin_name="王五",
        email_normalized="wang@example.com",
        phone_e164="+8613700137000",
        password_hash="argon2-hash",
        status="pending_review",
    )
    user = _make_user(migrated_db, "zhao@example.com", "+8613600136000")
    invitation = TenantInvitation(
        tenant_id=tenant.id,
        name="赵六",
        email_normalized="zhao@example.com",
        phone_e164="+8613600136000",
        role="reviewer",
        token_hash="a" * 64,
        status="invited",
    )
    session = AuthSession(
        user_id=user.id,
        tenant_id=tenant.id,
        token_hash="b" * 64,
        csrf_hash="c" * 64,
        expires_at=datetime(2026, 10, 25),
        idle_expires_at=datetime(2026, 10, 24, 12),
    )
    token = AuthToken(
        user_id=user.id,
        purpose="password_reset",
        subject_id=user.id,
        token_hash="d" * 64,
        expires_at=datetime(2026, 10, 25),
    )
    audit = AuditLog(
        tenant_id=tenant.id,
        actor_user_id=user.id,
        action="tenant.approve",
        target_type="tenant",
        target_id=tenant.id,
        result="success",
        metadata_json={},
    )
    migrated_db.add_all([registration, invitation, session, token, audit])
    migrated_db.commit()
    assert registration.id
    assert invitation.id
    assert session.id
    assert token.id
    assert audit.id


def test_registration_requires_unique_email_and_phone(migrated_db):
    migrated_db.add(
        TenantRegistrationRequest(
            organization_name="丁公司",
            admin_name="钱七",
            email_normalized="dup@example.com",
            phone_e164="+8613500135000",
            password_hash="argon2-hash",
        )
    )
    migrated_db.commit()
    migrated_db.add(
        TenantRegistrationRequest(
            organization_name="戊公司",
            admin_name="孙八",
            email_normalized="dup@example.com",
            phone_e164="+8613500135000",
            password_hash="argon2-hash",
        )
    )
    with pytest.raises(IntegrityError):
        migrated_db.commit()
