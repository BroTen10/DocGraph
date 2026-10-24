"""Session lifecycle helpers for revocable server-side authentication."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuthSession
from .security import new_token


def utcnow() -> datetime:
    """Return naive UTC matching the application DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class AuthSessionContext:
    """A created session plus the raw secrets returned only to the caller."""

    session: AuthSession
    raw_session_token: str
    raw_csrf_token: str
    user: Any
    tenant: Any | None
    membership: Any | None


def create_session(
    db: Session,
    user,
    tenant_id: uuid.UUID | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[AuthSession, str, str]:
    """Add a server-side session to the caller's transaction."""
    now = utcnow()
    raw_session, _ = new_token()
    session_hash = session_token_hash(raw_session)
    raw_csrf, csrf_hash = new_token()
    session = AuthSession(
        user_id=user.id,
        tenant_id=tenant_id,
        token_hash=session_hash,
        csrf_hash=csrf_hash,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        idle_expires_at=now + timedelta(minutes=settings.session_idle_minutes),
        last_seen_at=now,
        ip=ip[:45] if ip else None,
        user_agent=user_agent[:512] if user_agent else None,
    )
    db.add(session)
    db.flush()
    db.refresh(session)
    return session, raw_session, raw_csrf


def session_token_hash(raw_session_token: str) -> str:
    """Pepper stored session digests with the deployment secret."""
    return hmac.new(
        settings.app_secret_key.encode("utf-8"),
        raw_session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def get_active_session(db: Session, raw_session_token: str) -> AuthSession | None:
    """Resolve a session and reject revoked or expired records."""
    if not raw_session_token:
        return None
    session = db.execute(
        select(AuthSession).where(
            AuthSession.token_hash == session_token_hash(raw_session_token)
        )
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None
    now = utcnow()
    if session.expires_at <= now or session.idle_expires_at <= now:
        return None
    return session


def touch_session(db: Session, session: AuthSession) -> None:
    """Extend idle lifetime and record the most recent activity."""
    session.idle_expires_at = utcnow() + timedelta(
        minutes=settings.session_idle_minutes
    )
    session.last_seen_at = utcnow()
    db.commit()


def rotate_csrf(db: Session, session: AuthSession) -> str:
    """Rotate the CSRF secret and return the raw value for the client."""
    raw_csrf, csrf_hash = new_token()
    session.csrf_hash = csrf_hash
    db.commit()
    return raw_csrf


def revoke_session(db: Session, raw_session_token: str) -> bool:
    """Revoke the current session if it exists."""
    session = get_active_session(db, raw_session_token)
    if session is None:
        return False
    session.revoked_at = utcnow()
    db.commit()
    return True


def revoke_user_sessions(db: Session, user_id: uuid.UUID) -> int:
    """Revoke every active session for a user and return the count."""
    rows = db.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ).scalars().all()
    now = utcnow()
    for session in rows:
        session.revoked_at = now
    db.commit()
    return len(rows)


def revoke_tenant_sessions(db: Session, tenant_id: uuid.UUID) -> int:
    """Revoke every active session belonging to one tenant."""
    rows = db.execute(
        select(AuthSession).where(
            AuthSession.tenant_id == tenant_id,
            AuthSession.revoked_at.is_(None),
        )
    ).scalars().all()
    now = utcnow()
    for session in rows:
        session.revoked_at = now
    db.commit()
    return len(rows)
