"""检查项（检查意图）管理路由。

把检查项从「rule_sets.check_categories 字符串数组」升级为可治理的注册表：
列表 / 新建 / 改名 / 合并 / 启停 / 删除。规则导入发现的新检查项为 pending_review，
人工确认后才参与规则矩阵分列与执行分派。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_permission
from ..database import get_db
from ..services import check_intent_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/check-intents", tags=["检查项管理"])


class CheckIntentOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    aliases: list[str] = []
    sort_order: int = 100
    source: str
    status: str
    rule_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


class CheckIntentCreate(BaseModel):
    name: str
    description: str | None = None
    aliases: list[str] = []
    sort_order: int = 100
    status: str = "active"


class CheckIntentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    aliases: list[str] | None = None
    sort_order: int | None = None
    status: str | None = None


class CheckIntentMergeRequest(BaseModel):
    target_id: uuid.UUID


def _to_out(row, rule_counts: dict[str, int]) -> CheckIntentOut:
    return CheckIntentOut(
        id=str(row.id),
        name=row.name,
        description=row.description,
        aliases=list(row.aliases or []),
        sort_order=row.sort_order,
        source=row.source,
        status=row.status,
        rule_count=rule_counts.get(row.name, 0),
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("", response_model=list[CheckIntentOut])
def list_check_intents(
    status: str | None = Query(
        default=None, description="按状态过滤：active / pending_review / rejected"
    ),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.view")),
) -> list[CheckIntentOut]:
    """列出检查项注册表（含每个检查项被多少条规则使用）。"""
    rows = check_intent_service.list_intents(db, ctx.tenant.id, status=status)
    counts = check_intent_service.count_rules_by_intent(db, ctx.tenant.id)
    return [_to_out(row, counts) for row in rows]


@router.post("", response_model=CheckIntentOut, status_code=201)
def create_check_intent(
    payload: CheckIntentCreate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> CheckIntentOut:
    """手动新建检查项。"""
    try:
        row = check_intent_service.create_intent(
            db,
            ctx.tenant.id,
            name=payload.name,
            description=payload.description,
            aliases=payload.aliases,
            sort_order=payload.sort_order,
            status=payload.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_out(row, check_intent_service.count_rules_by_intent(db, ctx.tenant.id))


@router.put("/{intent_id}", response_model=CheckIntentOut)
def update_check_intent(
    intent_id: uuid.UUID,
    payload: CheckIntentUpdate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> CheckIntentOut:
    """更新检查项（改名会同步改写存量规则的引用；别名用于后续导入写时归一）。"""
    try:
        row = check_intent_service.update_intent(
            db,
            ctx.tenant.id,
            intent_id,
            name=payload.name,
            description=payload.description,
            aliases=payload.aliases,
            sort_order=payload.sort_order,
            status=payload.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="检查项不存在")
    return _to_out(row, check_intent_service.count_rules_by_intent(db, ctx.tenant.id))


@router.post("/{intent_id}/merge", response_model=CheckIntentOut)
def merge_check_intent(
    intent_id: uuid.UUID,
    payload: CheckIntentMergeRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> CheckIntentOut:
    """把本检查项合并进目标检查项（存量规则引用改写为 target，本项旧名转为 target 的别名）。"""
    try:
        row = check_intent_service.merge_intent(
            db, ctx.tenant.id, intent_id, payload.target_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="检查项不存在")
    return _to_out(row, check_intent_service.count_rules_by_intent(db, ctx.tenant.id))


@router.delete("/{intent_id}")
def delete_check_intent(
    intent_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> dict:
    """删除检查项（仍被规则引用时拒绝，需先改名或合并）。"""
    try:
        ok = check_intent_service.delete_intent(db, ctx.tenant.id, intent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="检查项不存在")
    return {"success": True}
