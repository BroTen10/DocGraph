"""CSRF and in-memory rate limiter tests."""

from __future__ import annotations

import pytest

from app.auth.rate_limit import SlidingWindowLimiter, reset_limiters
from conftest import authenticate


@pytest.fixture(autouse=True)
def _reset_limits():
    reset_limiters()
    yield
    reset_limiters()


def test_write_without_csrf_is_rejected(client, admin_auth):
    authenticate(client, admin_auth)
    response = client.put("/api/settings", json={"items": []})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_INVALID"


def test_write_with_wrong_origin_is_rejected(client, admin_auth):
    headers = authenticate(client, admin_auth)
    headers["Origin"] = "https://evil.example"
    response = client.put(
        "/api/settings",
        json={"items": []},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_INVALID"


def test_sliding_window_limiter():
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10, clock=lambda: now[0])
    assert limiter.allow("k")
    assert limiter.allow("k")
    assert not limiter.allow("k")
    now[0] = 11.0
    assert limiter.allow("k")


def test_login_is_rate_limited(client, active_tenant_user):
    payload = {"email": "admin@example.com", "password": "DocGraph2026"}
    for _ in range(10):
        assert client.post("/api/auth/login", json=payload).status_code == 200
    assert client.post("/api/auth/login", json=payload).status_code == 429
