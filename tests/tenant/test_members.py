"""Tenant member invitation and permission management tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from conftest import authenticate
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog, TenantMembership
from app.services import tenant_member_service
from app.services.tenant_member_service import MemberOperationError


def _raw_token_from_invite(delivered: dict) -> str:
    return delivered["url"].split("token=", 1)[1]


def test_tenant_admin_invites_and_accepts_member(client, admin_auth, monkeypatch):
    headers = authenticate(client, admin_auth)
    delivered = {}

    monkeypatch.setattr(
        "app.services.tenant_member_service.send_invitation_email",
        lambda email, url: delivered.update(email=email, url=url),
    )

    invited = client.post(
        "/api/tenant/members/invite",
        json={
            "name": "王五",
            "email": "wang@example.com",
            "phone": "13900139000",
            "role": "reviewer",
        },
        headers=headers,
    )
    assert invited.status_code == 201
    assert delivered["email"] == "wang@example.com"
    assert "/accept-invite#token=" in delivered["url"]
    assert "?token=" not in delivered["url"]

    raw_token = _raw_token_from_invite(delivered)
    accepted = client.post(
        "/api/auth/accept-invite",
        json={"token": raw_token, "password": "Reviewer2026"},
    )
    assert accepted.status_code == 200

    login = client.post(
        "/api/auth/login",
        json={
            "email": "wang@example.com",
            "password": "Reviewer2026",
        },
    )
    assert login.status_code == 200
    assert login.json()["role"] == "reviewer"


def test_last_admin_cannot_be_demoted(client, admin_auth):
    headers = authenticate(client, admin_auth)
    response = client.patch(
        f"/api/tenant/members/{admin_auth.membership_id}",
        json={"role": "reviewer"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "LAST_TENANT_ADMIN"


def test_cross_tenant_member_update_returns_404(client, admin_b, admin_auth):
    headers = authenticate(client, admin_b)
    response = client.patch(
        f"/api/tenant/members/{admin_auth.membership_id}",
        json={"role": "viewer"},
        headers=headers,
    )
    assert response.status_code == 404


def test_disabling_member_revokes_their_sessions(
    client,
    admin_auth,
    reviewer_auth,
):
    headers = authenticate(client, admin_auth)
    response = client.patch(
        f"/api/tenant/members/{reviewer_auth.membership_id}",
        json={"status": "disabled"},
        headers=headers,
    )
    assert response.status_code == 200

    authenticate(client, reviewer_auth)
    assert client.get("/api/auth/me").status_code == 401


def test_member_list_is_tenant_scoped(client, admin_auth, admin_b):
    headers = authenticate(client, admin_auth)
    response = client.get("/api/tenant/members", headers=headers)
    assert response.status_code == 200
    emails = {item["email"] for item in response.json()["members"]}
    assert admin_auth.email in emails
    assert admin_b.email not in emails
    assert all("phone" not in item for item in response.json()["members"])


def test_reveal_member_phone_is_scoped_and_audited(client, admin_auth, admin_b):
    headers = authenticate(client, admin_auth)
    response = client.post(
        f"/api/tenant/members/{admin_auth.membership_id}/phone",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["phone"].startswith("+86")

    with SessionLocal() as db:
        audit = db.execute(
            select(AuditLog)
            .where(
                AuditLog.tenant_id == admin_auth.tenant_id,
                AuditLog.actor_user_id == admin_auth.user_id,
                AuditLog.action == "member.phone.reveal",
            )
            .order_by(AuditLog.created_at.desc())
        ).scalars().first()
        assert audit is not None
        assert str(audit.target_id) == str(admin_auth.membership_id)

    headers = authenticate(client, admin_b)
    assert client.post(
        f"/api/tenant/members/{admin_auth.membership_id}/phone",
        headers=headers,
    ).status_code == 404


def test_reveal_invitation_phone_is_audited(client, admin_auth, monkeypatch):
    headers = authenticate(client, admin_auth)
    monkeypatch.setattr(
        "app.services.tenant_member_service.send_invitation_email",
        lambda email, url: None,
    )
    invited = client.post(
        "/api/tenant/members/invite",
        json={
            "name": "赵六",
            "email": "zhao@example.com",
            "phone": "13700137000",
            "role": "viewer",
        },
        headers=headers,
    )
    assert invited.status_code == 201
    invitation_id = invited.json()["id"]
    assert "phone" not in invited.json()

    revealed = client.post(
        f"/api/tenant/members/invitations/{invitation_id}/phone",
        headers=headers,
    )
    assert revealed.status_code == 200
    assert revealed.json()["phone"] == "+8613700137000"

    with SessionLocal() as db:
        audit = db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == admin_auth.tenant_id,
                AuditLog.action == "invitation.phone.reveal",
            )
        ).scalars().first()
        assert audit is not None


def test_concurrent_admin_demotions_keep_one_admin(
    two_tenants,
    admin_auth,
    make_auth_fixture,
):
    tenant_id = two_tenants[0].id
    second_admin = make_auth_fixture(
        tenant_id,
        "tenant_admin",
        "second-admin@example.com",
    )
    barrier = Barrier(2)

    def demote(membership_id):
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            try:
                tenant_member_service.update_member(
                    db,
                    admin_auth.user_id,
                    tenant_id,
                    membership_id,
                    role="viewer",
                )
                return "updated"
            except MemberOperationError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                demote,
                [admin_auth.membership_id, second_admin.membership_id],
            )
        )

    assert sorted(results) == ["LAST_TENANT_ADMIN", "updated"]
    with SessionLocal() as db:
        active_admins = db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == "tenant_admin",
                TenantMembership.status == "active",
            )
        ).scalars().all()
        assert len(active_admins) == 1
