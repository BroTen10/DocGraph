"""Security audit and request correlation regressions."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from conftest import authenticate
from app.database import SessionLocal
from app.models import AuditLog


def _latest(action: str) -> AuditLog | None:
    with SessionLocal() as db:
        return db.execute(
            select(AuditLog)
            .where(AuditLog.action == action)
            .order_by(AuditLog.created_at.desc())
        ).scalars().first()


def test_role_change_writes_audit_log(
    client,
    admin_auth,
    reviewer_auth,
):
    headers = authenticate(client, admin_auth)
    response = client.patch(
        f"/api/tenant/members/{reviewer_auth.membership_id}",
        json={"role": "viewer"},
        headers=headers,
    )
    assert response.status_code == 200

    audit = _latest("member.role.update")
    assert audit is not None
    assert audit.tenant_id == admin_auth.tenant_id
    assert audit.actor_user_id == admin_auth.user_id
    assert str(audit.target_id) == str(reviewer_auth.membership_id)
    assert audit.metadata_json == {
        "from_role": "reviewer",
        "to_role": "viewer",
    }


def test_audit_metadata_is_redacted(migrated_db, admin_auth):
    from app.services import audit_service

    audit_service.record(
        migrated_db,
        tenant_id=admin_auth.tenant_id,
        actor_user_id=admin_auth.user_id,
        action="test.redaction",
        target_type="test",
        result="success",
        metadata={
            "password": "never-store",
            "session_token": "secret-token",
            "phone": "+8613800138000",
            "document_text": "confidential body",
            "nested": {"authorization": "Bearer secret"},
            "safe": "visible",
        },
    )
    migrated_db.commit()

    with SessionLocal() as db:
        row = db.execute(
            select(AuditLog).where(AuditLog.action == "test.redaction")
        ).scalar_one()
    serialized = str(row.metadata_json)
    assert "never-store" not in serialized
    assert "secret-token" not in serialized
    assert "+8613800138000" not in serialized
    assert "confidential body" not in serialized
    assert row.metadata_json["safe"] == "visible"
    assert row.metadata_json["phone"] == "[REDACTED]"


def test_login_success_and_failure_are_audited(
    client,
    active_tenant_user,
):
    failed = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "WrongPassword1"},
    )
    assert failed.status_code == 401
    failure = _latest("auth.login.failed")
    assert failure is not None
    assert failure.result == "failure"

    success = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "DocGraph2026"},
    )
    assert success.status_code == 200
    login = _latest("auth.login.success")
    assert login is not None
    assert login.actor_user_id == active_tenant_user.id
    assert login.result == "success"
    assert "password" not in login.metadata_json


def test_request_id_is_returned_and_stored(client, admin_auth, reviewer_auth):
    response = client.get(
        "/api/tenant/members",
        headers={"X-Request-ID": "audit-request-123"},
    )
    assert response.status_code == 401

    headers = authenticate(client, admin_auth)
    response = client.get(
        "/api/tenant/members",
        headers={**headers, "X-Request-ID": "audit-request-123"},
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "audit-request-123"

    headers = authenticate(client, admin_auth)
    client.patch(
        f"/api/tenant/members/{reviewer_auth.membership_id}",
        json={"role": "viewer"},
        headers={**headers, "X-Request-ID": "audit-request-456"},
    )
    with SessionLocal() as db:
        row = db.execute(
            select(AuditLog)
            .where(AuditLog.action == "member.role.update")
            .order_by(AuditLog.created_at.desc())
        ).scalars().first()
    assert row is not None
    assert row.request_id == "audit-request-456"


def test_authenticated_object_denial_is_audited(client, admin_auth):
    authenticate(client, admin_auth)
    missing_id = uuid.uuid4()
    response = client.get(f"/api/contracts/{missing_id}")
    assert response.status_code == 404

    audit = _latest("security.access.denied")
    assert audit is not None
    assert audit.tenant_id == admin_auth.tenant_id
    assert audit.actor_user_id == admin_auth.user_id
    assert audit.metadata_json["status"] == 404
    assert str(missing_id) not in str(audit.metadata_json)


def test_log_text_redacts_tokens_and_credentials():
    from app.observability import redact_text

    text = (
        "password=Secret123 token=abcdef "
        "Authorization: Bearer eyJhbGciOi "
        "url=https://example.com/verify?token=raw-token"
    )
    redacted = redact_text(text)
    assert "Secret123" not in redacted
    assert "abcdef" not in redacted
    assert "eyJhbGciOi" not in redacted
    assert "raw-token" not in redacted
