"""CSRF and in-memory rate limiter tests."""

from __future__ import annotations

import pytest

from app.auth.rate_limit import (
    SlidingWindowLimiter,
    reset_limiters,
    resolve_client_ip,
)
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


def test_resolve_client_ip_prefers_valid_real_ip():
    ip = resolve_client_ip(
        "172.18.0.3",
        {"x-real-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.10"},
        trust_proxy_headers=True,
    )
    assert ip == "203.0.113.9"


def test_resolve_client_ip_falls_back_when_header_is_invalid():
    ip = resolve_client_ip(
        "172.18.0.3",
        {"x-real-ip": "not-an-ip", "x-forwarded-for": "invalid, 198.51.100.7"},
        trust_proxy_headers=True,
    )
    assert ip == "198.51.100.7"


def test_resolve_client_ip_ignores_headers_when_not_trusting():
    ip = resolve_client_ip(
        "172.18.0.3",
        {"x-real-ip": "203.0.113.9"},
        trust_proxy_headers=False,
    )
    assert ip == "172.18.0.3"


def test_resolve_client_ip_returns_none_for_invalid_peer():
    assert resolve_client_ip(None, {}, trust_proxy_headers=False) is None


def test_login_is_rate_limited(client, active_tenant_user):
    payload = {"email": "admin@example.com", "password": "DocGraph2026"}
    for _ in range(10):
        assert client.post("/api/auth/login", json=payload).status_code == 200
    assert client.post("/api/auth/login", json=payload).status_code == 429
