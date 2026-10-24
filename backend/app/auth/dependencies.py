"""FastAPI dependencies that derive the authenticated tenant context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Tenant, TenantMembership, UserAccount
from ..observability import bind_principal
from ..tenant_scope import bind_tenant
from .permissions import permissions_for
from .session_tokens import get_active_session, touch_session


@dataclass(frozen=True)
class AuthContext:
    """Authenticated identity and permissions for one request."""

    user: Any
    tenant: Any | None
    membership: Any | None
    session: Any
    permissions: frozenset[str]


def get_auth_context(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext:
    """Resolve and validate session, account, tenant and membership state."""
    token = request.cookies.get("docgraph_session")
    session = get_active_session(db, token)
    if session is None:
        raise HTTPException(status_code=401, detail="未登录或会话已失效")

    user = db.get(UserAccount, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if user.locked_until is not None and user.locked_until > now:
        raise HTTPException(status_code=403, detail="账号已锁定")

    tenant: Tenant | None = None
    membership: TenantMembership | None = None
    if session.tenant_id is not None:
        row = db.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user.id,
                TenantMembership.tenant_id == session.tenant_id,
                TenantMembership.status == "active",
                Tenant.status == "active",
            )
            .limit(1)
        ).first()
        if row is None:
            raise HTTPException(status_code=403, detail="账号或租户状态异常")
        membership, tenant = row
    elif not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="账号状态异常")

    bind_tenant(db, tenant.id if tenant is not None else None)
    touch_session(db, session)
    bind_principal(user.id, tenant.id if tenant is not None else None)
    request.state.actor_user_id = str(user.id)
    request.state.tenant_id = str(tenant.id) if tenant is not None else None
    role = membership.role if membership is not None else None
    permissions = permissions_for(role, user.is_platform_admin)
    return AuthContext(
        user=user,
        tenant=tenant,
        membership=membership,
        session=session,
        permissions=permissions,
    )


def require_permission(name: str):
    """Build a dependency requiring the authenticated user to hold a permission."""

    def dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if name not in ctx.permissions:
            raise HTTPException(
                status_code=403,
                detail="没有执行此操作的权限",
            )
        return ctx

    return dependency


def require_platform_admin(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Require a platform administrator session."""
    if not ctx.user.is_platform_admin:
        raise HTTPException(status_code=403, detail="仅平台管理员可执行此操作")
    return ctx
