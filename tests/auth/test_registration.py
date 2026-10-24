"""新租户注册与邮箱验证流程测试。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuthToken, TenantRegistrationRequest


def _register(client, **overrides):
    payload = {
        "organization_name": "测试贸易公司",
        "admin_name": "张三",
        "email": "Zhang@Example.com",
        "phone": "+86 138-0013-8000",
        "password": "DocGraph2026",
        "accept_terms": True,
    }
    payload.update(overrides)
    return client.post("/api/auth/register", json=payload)


def test_register_then_verify_enters_review_queue(client, monkeypatch):
    delivered = {}

    def capture(email, url):
        delivered["email"] = email
        delivered["url"] = url

    monkeypatch.setattr(
        "app.services.auth_service.send_verification_email",
        capture,
    )
    response = _register(client)
    assert response.status_code == 202
    assert delivered["email"] == "zhang@example.com"

    token = delivered["url"].split("token=", 1)[1]
    verified = client.post("/api/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert verified.json()["status"] == "pending_review"


def test_invalid_phone_is_rejected(client):
    response = _register(client, phone="010-12345678")
    assert response.status_code == 422


def test_duplicate_email_returns_generic_accepted_without_new_row(
    client,
    monkeypatch,
):
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.auth_service.send_verification_email",
        lambda email, url: captured.append((email, url)),
    )

    assert _register(client).status_code == 202
    assert len(captured) == 1

    assert _register(
        client,
        email="zhang@example.com",
        phone="+86 139-0013-9000",
    ).status_code == 202
    assert len(captured) == 1

    with SessionLocal() as db:
        count = db.execute(
            select(func.count(TenantRegistrationRequest.id)).where(
                TenantRegistrationRequest.email_normalized == "zhang@example.com"
            )
        ).scalar_one()
        assert count == 1


def test_expired_or_used_verification_token_is_rejected(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.auth_service.send_verification_email",
        lambda email, url: captured.update(email=email, url=url),
    )
    assert _register(client).status_code == 202
    token = captured["url"].split("token=", 1)[1]

    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    again = client.post("/api/auth/verify-email", json={"token": token})
    assert again.status_code == 400

    with SessionLocal() as db:
        used = db.execute(
            select(AuthToken).where(AuthToken.used_at.isnot(None))
        ).scalars().first()
        assert used is not None

