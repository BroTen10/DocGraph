"""审查执行路由。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_permission
from ..database import get_db
from ..models import Contract, ReviewTask
from ..schemas.review import (
    ReviewResultByDoc,
    ReviewResultByRule,
    ReviewResultItem,
    ReviewResultStatusUpdate,
    ReviewStartRequest,
    ReviewTaskListItem,
    ReviewTaskSummary,
)
from ..services import review_service

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.post("/start", response_model=ReviewTaskSummary)
def start_review(
    payload: ReviewStartRequest,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("review.execute")),
) -> ReviewTaskSummary:
    """启动审查任务。"""
    assert ctx.tenant is not None
    try:
        task = review_service.start_review(
            db,
            ctx.tenant.id,
            ctx.user.id,
            payload.contract_id,
            payload.snapshot_id,
        )
        return ReviewTaskSummary.model_validate(task)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=list[ReviewTaskListItem])
def list_tasks(
    contract_id: uuid.UUID | None = Query(default=None),
    rule_set_id: uuid.UUID | None = Query(
        default=None, description="按规则集过滤（通过 join contract）"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("review.view")),
) -> list[ReviewTaskListItem]:
    """审查任务列表（默认按开始时间倒序，可按合同/规则集过滤）。"""
    assert ctx.tenant is not None
    if contract_id is not None:
        contract = db.execute(
            select(Contract).where(
                Contract.tenant_id == ctx.tenant.id,
                Contract.id == contract_id,
            )
        ).scalars().first()
        if contract is None:
            raise HTTPException(status_code=404, detail="合同不存在")
    if rule_set_id is not None:
        from ..models import RuleSet

        rule_set = db.execute(
            select(RuleSet).where(
                RuleSet.tenant_id == ctx.tenant.id,
                RuleSet.id == rule_set_id,
            )
        ).scalars().first()
        if rule_set is None:
            raise HTTPException(status_code=404, detail="规则集不存在")
    # 顺带回收中断的审查任务，避免前端把陈旧的 running 状态当作"正在执行"
    review_service.reconcile_stale_review_tasks(db, tenant_id=ctx.tenant.id)
    stmt = (
        select(ReviewTask, Contract.contract_no)
        .join(
            Contract,
            (Contract.id == ReviewTask.contract_id)
            & (Contract.tenant_id == ReviewTask.tenant_id),
            isouter=True,
        )
        .where(ReviewTask.tenant_id == ctx.tenant.id)
        .order_by(ReviewTask.start_time.desc())
        .limit(limit)
    )
    if contract_id is not None:
        stmt = stmt.where(ReviewTask.contract_id == contract_id)
    if rule_set_id is not None:
        stmt = stmt.where(Contract.rule_set_id == rule_set_id)
    rows = db.execute(stmt).all()
    out: list[ReviewTaskListItem] = []
    for task, contract_no in rows:
        item = ReviewTaskListItem.model_validate(task)
        item.contract_no = contract_no
        out.append(item)
    return out


@router.get("/{task_id}", response_model=ReviewTaskSummary)
def get_task_status(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("review.view")),
) -> ReviewTaskSummary:
    """查询审查进度/结果。"""
    assert ctx.tenant is not None
    task = review_service.get_task_status(db, ctx.tenant.id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ReviewTaskSummary.model_validate(task)


@router.get("/{task_id}/by-rule", response_model=ReviewResultByRule)
def get_results_by_rule(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("review.view")),
) -> ReviewResultByRule:
    """按规则维度结果视图。"""
    assert ctx.tenant is not None
    try:
        return ReviewResultByRule(
            **review_service.get_results_by_rule(db, ctx.tenant.id, task_id)
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{task_id}/by-doc", response_model=ReviewResultByDoc)
def get_results_by_doc(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("review.view")),
) -> ReviewResultByDoc:
    """按文档维度结果视图。"""
    assert ctx.tenant is not None
    try:
        return ReviewResultByDoc(
            **review_service.get_results_by_doc(db, ctx.tenant.id, task_id)
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/results/{result_id}/status", response_model=ReviewResultItem)
def update_result_status(
    result_id: uuid.UUID,
    payload: ReviewResultStatusUpdate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("review.execute")),
) -> ReviewResultItem:
    """问题状态流转（批次 9）：open/confirmed/fixed/closed，带审计历史。"""
    assert ctx.tenant is not None
    try:
        result = review_service.update_result_status(
            db,
            ctx.tenant.id,
            result_id,
            payload.status,
            note=payload.note,
            by_user_id=ctx.user.id,
            by_display_name=ctx.user.name,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ReviewResultItem(**review_service._result_to_item(result))
