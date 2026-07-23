"""审查执行路由。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Contract, ReviewTask
from ..schemas.review import (
    ReviewResultByDoc,
    ReviewResultByRule,
    ReviewStartRequest,
    ReviewTaskListItem,
    ReviewTaskSummary,
)
from ..services import review_service

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.post("/start", response_model=ReviewTaskSummary)
def start_review(
    payload: ReviewStartRequest, db: Session = Depends(get_db)
) -> ReviewTaskSummary:
    """启动审查任务。"""
    try:
        task = review_service.start_review(db, payload.contract_id, payload.snapshot_id)
        return ReviewTaskSummary.model_validate(task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=list[ReviewTaskListItem])
def list_tasks(
    contract_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ReviewTaskListItem]:
    """审查任务列表（默认按开始时间倒序，可按合同过滤）。"""
    stmt = (
        select(ReviewTask, Contract.contract_no)
        .join(Contract, Contract.id == ReviewTask.contract_id, isouter=True)
        .order_by(ReviewTask.start_time.desc())
        .limit(limit)
    )
    if contract_id is not None:
        stmt = stmt.where(ReviewTask.contract_id == contract_id)
    rows = db.execute(stmt).all()
    out: list[ReviewTaskListItem] = []
    for task, contract_no in rows:
        item = ReviewTaskListItem.model_validate(task)
        item.contract_no = contract_no
        out.append(item)
    return out


@router.get("/{task_id}", response_model=ReviewTaskSummary)
def get_task_status(
    task_id: uuid.UUID, db: Session = Depends(get_db)
) -> ReviewTaskSummary:
    """查询审查进度/结果。"""
    task = review_service.get_task_status(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ReviewTaskSummary.model_validate(task)


@router.get("/{task_id}/by-rule", response_model=ReviewResultByRule)
def get_results_by_rule(
    task_id: uuid.UUID, db: Session = Depends(get_db)
) -> ReviewResultByRule:
    """按规则维度结果视图。"""
    return ReviewResultByRule(**review_service.get_results_by_rule(db, task_id))


@router.get("/{task_id}/by-doc", response_model=ReviewResultByDoc)
def get_results_by_doc(
    task_id: uuid.UUID, db: Session = Depends(get_db)
) -> ReviewResultByDoc:
    """按文档维度结果视图。"""
    return ReviewResultByDoc(**review_service.get_results_by_doc(db, task_id))
