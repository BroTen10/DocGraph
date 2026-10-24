"""Login, session lifecycle and password recovery tests."""

from __future__ import annotations

from app.services.email_service import OUTBOX, clear_outbox


def _reset_token_from_outbox() -> str:
    last = OUTBOX[-1]["plain_text"]
    return last.split("token=", 1)[1].split("\n", 1)[0].strip()


def test_login_me_logout(client, active_tenant_user):
    response = client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.com",
            "password": "DocGraph2026",
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "tenant_admin"
    assert response.cookies.get("docgraph_session")
    assert response.json()["csrf_token"]
    csrf_headers = {"X-CSRF-Token": response.json()["csrf_token"]}

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "admin@example.com"
    assert me.json()["user"]["phone_masked"] == "136****6000"

    csrf_headers = {"X-CSRF-Token": me.json()["csrf_token"]}
    assert client.post("/api/auth/logout", headers=csrf_headers).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_five_failures_lock_account(client, active_tenant_user):
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={
                "email": "admin@example.com",
                "password": "WrongPassword1",
            },
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.com",
            "password": "DocGraph2026",
        },
    )
    assert locked.status_code == 403
    assert locked.json()["code"] == "ACCOUNT_LOCKED"


def test_forgot_and_reset_password_revokes_sessions(client, active_tenant_user):
    login = client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.com",
            "password": "DocGraph2026",
        },
    )
    assert login.status_code == 200

    clear_outbox()
    forgot = client.post(
        "/api/auth/password/forgot",
        json={"email": "admin@example.com"},
    )
    assert forgot.status_code == 202
    assert len(OUTBOX) == 1
    raw_token = _reset_token_from_outbox()

    reset = client.post(
        "/api/auth/password/reset",
        json={
            "token": raw_token,
            "new_password": "NewPassword2026",
        },
    )
    assert reset.status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    relogin = client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.com",
            "password": "NewPassword2026",
        },
    )
    assert relogin.status_code == 200


def test_forgot_password_unknown_email_is_generic(client, active_tenant_user):
    clear_outbox()
    response = client.post(
        "/api/auth/password/forgot",
        json={"email": "unknown@example.com"},
    )
    assert response.status_code == 202
    assert len(OUTBOX) == 0


def test_change_password_requires_current_password(client, active_tenant_user):
    login = client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.com",
            "password": "DocGraph2026",
        },
    )
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    wrong = client.put(
        "/api/auth/password",
        json={
            "current_password": "NotRight1",
            "new_password": "ChangedPass2026",
        },
        headers=csrf_headers,
    )
    assert wrong.status_code == 400

    changed = client.put(
        "/api/auth/password",
        json={
            "current_password": "DocGraph2026",
            "new_password": "ChangedPass2026",
        },
        headers=csrf_headers,
    )
    assert changed.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
