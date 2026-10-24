"""Append-only audit events with tenant, actor and request correlation."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog
from ..observability import (
    actor_user_id_var,
    request_id_var,
    request_ip_var,
    sanitize_metadata,
    tenant_id_var,
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
    effective_tenant = tenant_id
    if effective_tenant is None and tenant_id_var.get():
        effective_tenant = uuid.UUID(tenant_id_var.get())
    effective_actor = actor_user_id
    if effective_actor is None and actor_user_id_var.get():
        effective_actor = uuid.UUID(actor_user_id_var.get())

    row = AuditLog(
        tenant_id=effective_tenant,
        actor_user_id=effective_actor,
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
