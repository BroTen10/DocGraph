"""Append-only audit events with tenant, actor and request correlation."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog
from ..observability import (
    request_id_var,
    request_ip_var,
    sanitize_metadata,
)

logger = logging.getLogger(__name__)


def record(
    db: Session,
    tenant_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit row to the caller's transaction without committing it."""
    row = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        metadata_json=sanitize_metadata(metadata or {}),
        request_id=request_id_var.get(),
        ip=request_ip_var.get(),
    )
    db.add(row)
    return row


def record_and_commit(
    db: Session,
    tenant_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Record one isolated event and commit it."""
    row = record(
        db,
        tenant_id,
        actor_user_id,
        action,
        target_type,
        target_id,
        result,
        metadata,
    )
    db.commit()
    return row
