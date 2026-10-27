"""Platform tenant approval and administration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app import bootstrap
from app.auth.security import hash_password
from app.database import SessionLocal
from app.models import (
    AuthSession,
    Tenant,
    TenantMembership,
    TenantRegistrationRequest,
    UserAccount,
)
from app.services.email_service import EmailDeliveryError
from conftest import authenticate


def _registration(
    db,
    *,
    email="applicant@example.com",
    phone="+8613800138000",
    status="pending_review",
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    request = TenantRegistrationRequest(
        organization_name="申请人贸易公司",
        admin_name="申请人",
        email_normalized=email,
        phone_e164=phone,
        password_hash=hash_password("DocGraph2026"),
        status=status,
        email_verified_at=now,
        terms_accepted_at=now,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def test_approval_creates_active_tenant_and_admin(
    client,
    migrated_db,
    platform_auth,
):
    registration = _registration(migrated_db)
    headers = authenticate(client, platform_auth)

    response = client.post(
        f"/api/platform/registrations/{registration.id}/approve",
        json={"note": "资料完整"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["tenant_status"] == "active"
    assert response.json()["admin_role"] == "tenant_admin"

    migrated_db.expire_all()
    reviewed = migrated_db.get(TenantRegistrationRequest, registration.id)
    assert reviewed.review_note == "资料完整"
    assert reviewed.reviewed_by == platform_auth.user_id
    assert reviewed.reviewed_at is not None

    login = client.post(
        "/api/auth/login",
        json={"email": "applicant@example.com", "password": "DocGraph2026"},
    )
    assert login.status_code == 200
    assert login.json()["role"] == "tenant_admin"


def test_reject_records_reason_and_does_not_create_account(
    client,
    migrated_db,
    platform_auth,
    monkeypatch,
):
    registration = _registration(migrated_db, email="reject@example.com")
    headers = authenticate(client, platform_auth)
    delivered = []
    monkeypatch.setattr(
        "app.services.platform_service.send_email",
        lambda **kwargs: delivered.append(kwargs),
    )

    response = client.post(
        f"/api/platform/registrations/{registration.id}/reject",
        json={"reason": "组织资料不完整"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    with SessionLocal() as db:
        row = db.get(TenantRegistrationRequest, registration.id)
        assert row.status == "rejected"
        assert row.rejection_reason == "组织资料不完整"
        account_count = db.execute(
            select(func.count(UserAccount.id)).where(
                UserAccount.email_normalized == "reject@example.com"
            )
        ).scalar_one()
        assert account_count == 0
    assert delivered[0]["to_email"] == "reject@example.com"
    assert "组织资料不完整" in delivered[0]["plain_text"]


def test_reject_email_failure_keeps_application_reviewable(
    client,
    migrated_db,
    platform_auth,
    monkeypatch,
):
    registration = _registration(migrated_db, email="retry-reject@example.com")
    headers = authenticate(client, platform_auth)

    def fail_delivery(**kwargs):
        raise EmailDeliveryError("smtp unavailable")

    monkeypatch.setattr(
        "app.services.platform_service.send_email",
        fail_delivery,
    )
    response = client.post(
        f"/api/platform/registrations/{registration.id}/reject",
        json={"reason": "资料不完整"},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["code"] == "EMAIL_DELIVERY_FAILED"

    migrated_db.expire_all()
    row = migrated_db.get(TenantRegistrationRequest, registration.id)
    assert row.status == "pending_review"


def test_approval_is_idempotent(client, migrated_db, platform_auth):
    registration = _registration(migrated_db, email="idempotent@example.com")
    headers = authenticate(client, platform_auth)
    url = f"/api/platform/registrations/{registration.id}/approve"

    assert client.post(url, json={"note": "ok"}, headers=headers).status_code == 200
    second = client.post(url, json={"note": "again"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "REGISTRATION_ALREADY_REVIEWED"


def test_approval_requires_verified_email(client, migrated_db, platform_auth):
    registration = _registration(migrated_db, email="unverified@example.com")
    registration.email_verified_at = None
    migrated_db.commit()
    headers = authenticate(client, platform_auth)

    response = client.post(
        f"/api/platform/registrations/{registration.id}/approve",
        json={},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "REGISTRATION_NOT_VERIFIED"


def test_repeated_rejection_does_not_send_duplicate_email(
    client,
    migrated_db,
    platform_auth,
    monkeypatch,
):
    registration = _registration(migrated_db, email="repeat-reject@example.com")
    headers = authenticate(client, platform_auth)
    delivered = []
    monkeypatch.setattr(
        "app.services.platform_service.send_email",
        lambda **kwargs: delivered.append(kwargs),
    )
    url = f"/api/platform/registrations/{registration.id}/reject"

    assert client.post(url, json={"reason": "资料不完整"}, headers=headers).status_code == 200
    second = client.post(url, json={"reason": "再次驳回"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "REGISTRATION_ALREADY_REVIEWED"
    assert len(delivered) == 1


def test_platform_admin_cannot_read_tenant_contracts(client, platform_auth):
    authenticate(client, platform_auth)
    assert client.get("/api/contracts").status_code == 403


def test_suspending_tenant_revokes_sessions_and_blocks_login(
    client,
    migrated_db,
    platform_auth,
):
    registration = _registration(migrated_db, email="suspend@example.com")
    approve_headers = authenticate(client, platform_auth)
    approved = client.post(
        f"/api/platform/registrations/{registration.id}/approve",
        json={},
        headers=approve_headers,
    )
    tenant_id = approved.json()["tenant_id"]

    login = client.post(
        "/api/auth/login",
        json={"email": "suspend@example.com", "password": "DocGraph2026"},
    )
    assert login.status_code == 200
    applicant_cookie = login.cookies.get("docgraph_session")

    suspend_headers = authenticate(client, platform_auth)
    suspend = client.patch(
        f"/api/platform/tenants/{tenant_id}/status",
        json={"status": "suspended"},
        headers=suspend_headers,
    )
    assert suspend.status_code == 200
    assert suspend.json()["status"] == "suspended"

    client.cookies.clear()
    client.cookies.set("docgraph_session", applicant_cookie)
    assert client.get("/api/auth/me").status_code == 401
    retry_login = client.post(
        "/api/auth/login",
        json={"email": "suspend@example.com", "password": "DocGraph2026"},
    )
    assert retry_login.status_code == 403
    assert retry_login.json()["code"] == "TENANT_SUSPENDED"


def test_platform_admin_lists_registrations_and_tenants(
    client,
    migrated_db,
    platform_auth,
):
    registration = _registration(migrated_db, email="listed@example.com")
    _registration(
        migrated_db,
        email="rejected-listed@example.com",
        phone="+8613900139000",
        status="rejected",
    )
    headers = authenticate(client, platform_auth)

    registrations = client.get("/api/platform/registrations", headers=headers)
    assert registrations.status_code == 200
    assert all(item["status"] == "pending_review" for item in registrations.json()["registrations"])
    rows = registrations.json()["registrations"]
    assert any(row["email"] == "listed@example.com" for row in rows)

    headers = authenticate(client, platform_auth)
    approved = client.post(
        f"/api/platform/registrations/{registration.id}/approve",
        json={},
        headers=headers,
    )
    assert approved.status_code == 200

    tenants = client.get("/api/platform/tenants", headers=headers)
    assert tenants.status_code == 200
    listed_tenant = next(
        row
        for row in tenants.json()["tenants"]
        if row["id"] == approved.json()["tenant_id"]
    )
    assert listed_tenant["admin_name"] == "申请人"
    assert listed_tenant["admin_email"] == "listed@example.com"
    assert listed_tenant["admin_phone_masked"] == "138****8000"
    assert listed_tenant["admin_role"] == "tenant_admin"


def test_platform_admin_session_uses_explicit_role_contract(client, platform_auth):
    login = client.post(
        "/api/auth/login",
        json={"email": platform_auth.email, "password": "DocGraph2026"},
    )
    assert login.status_code == 200
    assert login.json()["role"] == "platform_admin"
    assert login.json()["user"]["role"] == "platform_admin"
    assert login.json()["tenant"] is None

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "platform_admin"
    assert me.json()["user"]["role"] == "platform_admin"


def test_bootstrap_requires_environment(monkeypatch):
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PHONE", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    assert bootstrap.main() == 2


def test_bootstrap_creates_platform_admin(migrated_db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "bootstrap@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PHONE", "+86 137-0013-7000")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Bootstrap2026")
    monkeypatch.setattr("app.database.init_db", lambda: None)

    assert bootstrap.main() == 0

    with SessionLocal() as db:
        row = db.execute(
            select(UserAccount).where(
                UserAccount.email_normalized == "bootstrap@example.com"
            )
        ).scalar_one()
        assert row.is_platform_admin
        assert row.phone_e164 == "+8613700137000"
        assert row.password_hash != "Bootstrap2026"


def test_bootstrap_rejects_missing_or_invalid_phone(migrated_db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "bootstrap@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Bootstrap2026")
    monkeypatch.setattr("app.database.init_db", lambda: None)

    monkeypatch.delenv("BOOTSTRAP_ADMIN_PHONE", raising=False)
    assert bootstrap.main() == 2

    monkeypatch.setenv("BOOTSTRAP_ADMIN_PHONE", "010-12345678")
    assert bootstrap.main() == 1

    with SessionLocal() as db:
        count = db.execute(
            select(func.count(UserAccount.id)).where(
                UserAccount.email_normalized == "bootstrap@example.com"
            )
        ).scalar_one()
        assert count == 0


def test_bootstrap_creates_default_tenant_admin(client, migrated_db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_TENANT_ADMIN_EMAIL", "legacy-admin@example.com")
    monkeypatch.setenv("BOOTSTRAP_TENANT_ADMIN_PHONE", "136-0013-6000")
    monkeypatch.setenv("BOOTSTRAP_TENANT_ADMIN_PASSWORD", "LegacyAdmin2026")
    monkeypatch.setattr("app.database.init_db", lambda: None)

    assert bootstrap.main(["default-tenant-admin"]) == 0

    with SessionLocal() as db:
        tenant = db.execute(
            select(Tenant).where(Tenant.code == "default")
        ).scalar_one()
        user = db.execute(
            select(UserAccount).where(
                UserAccount.email_normalized == "legacy-admin@example.com"
            )
        ).scalar_one()
        membership = db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        ).scalar_one()
        assert membership.role == "tenant_admin"
        assert membership.status == "active"
        assert user.phone_e164 == "+8613600136000"

    login = client.post(
        "/api/auth/login",
        json={
            "email": "legacy-admin@example.com",
            "password": "LegacyAdmin2026",
        },
    )
    assert login.status_code == 200
    assert login.json()["role"] == "tenant_admin"
    assert login.json()["tenant"]["name"] == "默认租户"
