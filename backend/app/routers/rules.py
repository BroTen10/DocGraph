"""规则管理路由。"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.graph import GraphSnapshotOut
from ..schemas.rule import RuleCreate, RuleImportRequest, RuleImportResponse, RuleOut, RuleUpdate
from ..services import rule_import_service, rule_service

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(
    doc_type: Optional[str] = Query(None),
    check_category: Optional[str] = Query(None),
    enabled_only: bool = Query(False),
    db: Session = Depends(get_db),
) -> list[RuleOut]:
    """规则列表，支持过滤。"""
    return rule_service.list_rules(db, doc_type, check_category, enabled_only)


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)) -> RuleOut:
    return rule_service.create_rule(db, payload)


@router.post("/import-batch", response_model=RuleImportResponse)
def import_rules_batch(
    payload: RuleImportRequest, db: Session = Depends(get_db)
) -> RuleImportResponse:
    """批量导入自然语言规则清单（LLM 解析后入库）。"""
    try:
        result = rule_import_service.import_rules_from_text(db, payload.raw_text)
        return RuleImportResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: uuid.UUID, payload: RuleUpdate, db: Session = Depends(get_db)
) -> RuleOut:
    rule = rule_service.update_rule(db, rule_id, payload)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    return rule


@router.delete("/{rule_id}")
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    if not rule_service.delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"success": True, "message": "规则已删除"}


# ============ 规则快照 ============
@router.get("/snapshots", response_model=list[GraphSnapshotOut])
def list_snapshots(db: Session = Depends(get_db)) -> list[GraphSnapshotOut]:
    """规则快照列表（按时间倒序）。"""
    snaps = rule_service.list_snapshots(db)
    return [GraphSnapshotOut.model_validate(s) for s in snaps]


@router.get("/snapshots/{snapshot_id}", response_model=GraphSnapshotOut)
def get_snapshot(
    snapshot_id: uuid.UUID, db: Session = Depends(get_db)
) -> GraphSnapshotOut:
    snap = rule_service.get_snapshot(db, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return GraphSnapshotOut.model_validate(snap)
