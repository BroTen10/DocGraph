"""CSRF and Origin validation helpers for state-changing requests."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlparse

from fastapi import Request

from ..config import settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

PUBLIC_AUTH_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/verify-email",
    "/api/auth/accept-invite",
    "/api/auth/password/forgot",
    "/api/auth/password/reset",
})


def is_unsafe(request: Request) -> bool:
    return request.method.upper() in UNSAFE_METHODS


def is_public_auth(request: Request) -> bool:
    return request.url.path in PUBLIC_AUTH_PATHS


def origin_matches(request: Request) -> bool:
    """Accept a missing Origin and reject a present, mismatched Origin."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    base = urlparse(settings.public_base_url)
    allowed = f"{base.scheme}://{base.netloc}".rstrip("/")
    return origin.rstrip("/") == allowed


def csrf_header_matches_session(request: Request, session_csrf_hash: str) -> bool:
    """Compare the X-CSRF-Token header against the session CSRF hash."""
    header = request.headers.get("x-csrf-token")
    if not header:
        return False
    provided_hash = hashlib.sha256(header.encode("utf-8")).hexdigest()
    return hmac.compare_digest(provided_hash, session_csrf_hash)
