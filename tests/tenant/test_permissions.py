"""Fixed-role permission dependency tests."""

from __future__ import annotations

from conftest import authenticate


def test_viewer_cannot_update_settings(client, viewer_auth):
    headers = authenticate(client, viewer_auth)
    response = client.put(
        "/api/settings",
        json={"items": []},
        headers=headers,
    )
    assert response.status_code == 403


def test_viewer_cannot_read_settings(client, viewer_auth):
    authenticate(client, viewer_auth)
    response = client.get("/api/settings")
    assert response.status_code == 403


def test_tenant_admin_can_read_settings(client, admin_auth):
    authenticate(client, admin_auth)
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert isinstance(response.json()["settings"], list)
