"""Thread-safe in-memory sliding window rate limiters."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Mapping

from fastapi import HTTPException, Request

from ..config import settings


@dataclass(frozen=True)
class LimitRule:
    """A named rate-limit rule applied to a key."""

    limit: int
    window_seconds: int


class SlidingWindowLimiter:
    """Limit keyed events within a rolling window using monotonically stamped hits."""

    def __init__(self, limit: int, window_seconds: int, clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_LOGIN_LIMITER = SlidingWindowLimiter(limit=10, window_seconds=60)
_LOGIN_IP_LIMITER = SlidingWindowLimiter(limit=30, window_seconds=60)
_PUBLIC_ACTION_LIMITER = SlidingWindowLimiter(limit=5, window_seconds=60)
_TOKEN_ACTION_LIMITER = SlidingWindowLimiter(limit=10, window_seconds=60)


def _valid_ip(value: str | None) -> str | None:
    """Return the normalized address when ``value`` is a syntactically valid IP."""
    if not value:
        return None
    candidate = value.strip()
    try:
        ip_address(candidate)
    except ValueError:
        return None
    return candidate


def resolve_client_ip(
    peer_host: str | None,
    headers: Mapping[str, str],
    trust_proxy_headers: bool,
) -> str | None:
    """Resolve the effective client IP for rate limiting.

    Proxy headers are only honored when the deployment explicitly trusts them.
    This deployment keeps the backend port host-loopback-only behind nginx, so
    untrusted direct clients cannot inject ``X-Real-IP``/``X-Forwarded-For``.
    """
    if trust_proxy_headers:
        real_ip = _valid_ip(headers.get("x-real-ip"))
        if real_ip is not None:
            return real_ip
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            for part in forwarded.split(","):
                candidate = _valid_ip(part)
                if candidate is not None:
                    return candidate
    return _valid_ip(peer_host)


def client_ip(request: Request) -> str | None:
    """Extract the rate-limit key IP from a request."""
    peer_host = request.client.host if request.client is not None else None
    return resolve_client_ip(
        peer_host,
        request.headers,
        settings.trust_proxy_headers,
    )


def _client_key(ip: str | None) -> str:
    return ip or "unknown"


def enforce_login_rate(ip: str | None, email: str) -> None:
    """Enforce independent IP and IP+account login buckets."""
    if not _LOGIN_IP_LIMITER.allow(_client_key(ip)):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    key = f"{_client_key(ip)}:{email.strip().lower()}"
    if not _LOGIN_LIMITER.allow(key):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")


def enforce_public_action_rate(ip: str | None) -> None:
    """Enforce registration and password-reset limits by client IP."""
    if not _PUBLIC_ACTION_LIMITER.allow(_client_key(ip)):
        raise HTTPException(
            status_code=429,
            detail="操作过于频繁，请稍后再试",
        )


def enforce_token_action_rate(ip: str | None) -> None:
    """Limit email verification and invitation token attempts by IP."""
    if not _TOKEN_ACTION_LIMITER.allow(_client_key(ip)):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")


def reset_limiters() -> None:
    """Clear limiter state; intended for tests and single-process restarts."""
    _LOGIN_LIMITER.reset()
    _LOGIN_IP_LIMITER.reset()
    _PUBLIC_ACTION_LIMITER.reset()
    _TOKEN_ACTION_LIMITER.reset()
