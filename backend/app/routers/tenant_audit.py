"""Tenant-scoped audit log API."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_permission
from ..database import get_db
from ..models import AuditLog

router = APIRouter(prefix="/api/tenant/audit-logs", tags=["tenant-audit"])


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: uuid.UUID | None
    result: str | None
    metadata: dict
    request_id: str | None
    ip: str | None
    created_at: datetime


class AuditLogListOut(BaseModel):
    items: list[AuditLogOut]
    total: int
    offset: int
    limit: int


@router.get("", response_model=AuditLogListOut)
def list_audit_logs(
    action: str | None = Query(default=None, max_length=128),
    actor_user_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AuthContext = Depends(require_permission("audit.read")),
    db: Session = Depends(get_db),
) -> AuditLogListOut:
    assert ctx.tenant is not None
    filters = [AuditLog.tenant_id == ctx.tenant.id]
    if action:
        filters.append(AuditLog.action == action)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if created_from is not None:
        filters.append(AuditLog.created_at >= created_from)
    if created_to is not None:
        filters.append(AuditLog.created_at <= created_to)

    total = db.execute(
        select(func.count(AuditLog.id)).where(*filters)
    ).scalar_one()
    rows = db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    items = [
        AuditLogOut(
            id=row.id,
            actor_user_id=row.actor_user_id,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            result=row.result,
            metadata=row.metadata_json,
            request_id=row.request_id,
            ip=row.ip,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return AuditLogListOut(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )
