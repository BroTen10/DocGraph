"""Tenant member invitation and permission management tests."""

from __future__ import annotations

from conftest import authenticate


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
