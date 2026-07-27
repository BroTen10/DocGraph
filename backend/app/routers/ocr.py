"""OCR 任务路由：独立触发 OCR 与进度查询。

提供两种粒度：
- POST /api/ocr/documents/{doc_id}       单文档触发
- POST /api/ocr/contracts/{contract_id}  合同级批量触发
- GET  /api/ocr/tasks                    任务列表
- GET  /api/ocr/tasks/{task_id}          单任务进度
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.ocr_task import OcrTaskBrief, OcrTaskOut
from ..services import ocr_task_service

router = APIRouter(prefix="/api/ocr", tags=["ocr"])


@router.post("/documents/{doc_id}", response_model=OcrTaskOut, status_code=201)
def trigger_doc_ocr(
    doc_id: uuid.UUID,
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    db: Session = Depends(get_db),
) -> OcrTaskOut:
    """触发单个文档 OCR（异步，立即返回 task）。"""
    try:
        return OcrTaskOut.model_validate(
            ocr_task_service.start_ocr_for_doc(db, rule_set_id, doc_id)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/contracts/{contract_id}", response_model=OcrTaskOut, status_code=201)
def trigger_contract_ocr(
    contract_id: uuid.UUID,
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    db: Session = Depends(get_db),
) -> OcrTaskOut:
    """触发合同下所有 pending 文档批量 OCR（异步）。"""
    try:
        return OcrTaskOut.model_validate(
            ocr_task_service.start_ocr_for_contract(db, rule_set_id, contract_id)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tasks", response_model=list[OcrTaskBrief])
def list_tasks(
    rule_set_id: uuid.UUID = Query(..., description="按规则集过滤"),
    contract_id: uuid.UUID | None = Query(default=None, description="可选：按合同过滤"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[OcrTaskBrief]:
    """OCR 任务列表（按 rule_set_id 过滤，可按 contract_id 二次过滤）。"""
    tasks = ocr_task_service.list_ocr_tasks(db, rule_set_id, contract_id, limit)
    return [OcrTaskBrief.model_validate(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=OcrTaskOut)
def get_task(
    task_id: uuid.UUID, db: Session = Depends(get_db)
) -> OcrTaskOut:
    """查询单个 OCR 任务进度。"""
    t = ocr_task_service.get_ocr_task(db, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return OcrTaskOut.model_validate(t)
