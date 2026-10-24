"""Thread-safe in-memory sliding window rate limiters."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException


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
_PUBLIC_ACTION_LIMITER = SlidingWindowLimiter(limit=5, window_seconds=60)


def _client_key(ip: str | None) -> str:
    return ip or "unknown"


def enforce_login_rate(ip: str | None, email: str) -> None:
    """Enforce login limits by IP and canonical email."""
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


def reset_limiters() -> None:
    """Clear limiter state; intended for tests and single-process restarts."""
    _LOGIN_LIMITER.reset()
    _PUBLIC_ACTION_LIMITER.reset()
