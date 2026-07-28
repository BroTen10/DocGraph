"""规则管理路由。"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.graph import GraphSnapshotOut
from ..schemas.rule import (
    ConflictDetectionResponse,
    RuleBatchConfirmRequest,
    RuleCreate,
    RuleImportRequest,
    RuleImportResponse,
    RuleOut,
    RuleUpdate,
)
from ..services import rule_conflict_detector, rule_import_service, rule_service

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    doc_type: Optional[str] = Query(None),
    check_category: Optional[str] = Query(None),
    enabled_only: bool = Query(False),
    db: Session = Depends(get_db),
) -> list[RuleOut]:
    """规则列表，支持按规则集 / 文件类型 / 检查项 / 启用状态过滤。"""
    return rule_service.list_rules(db, rule_set_id, doc_type, check_category, enabled_only)


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(
    payload: RuleCreate,
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    db: Session = Depends(get_db),
) -> RuleOut:
    """创建规则（挂在指定规则集下）。"""
    return rule_service.create_rule(db, rule_set_id, payload)


@router.post("/import-batch", response_model=RuleImportResponse)
def import_rules_batch(
    payload: RuleImportRequest,
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    db: Session = Depends(get_db),
) -> RuleImportResponse:
    """批量导入自然语言规则清单（LLM 解析后入库），归到指定规则集下。

    可选的 skill_ids 参数指定应用的 Skill；不传则使用该规则集已启用的所有 Skill。
    """
    try:
        result = rule_import_service.import_rules_with_skills(
            db, rule_set_id, payload.raw_text, skill_ids=payload.skill_ids
        )
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


@router.delete("")
def delete_rules_batch(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    ids: Optional[str] = Query(
        None, description="逗号分隔的规则 ID 列表；不提供则清空该规则集全部规则"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """批量删除规则。提供 ids 则仅删除指定规则；否则清空该规则集下所有规则。"""
    id_list: Optional[list[uuid.UUID]] = None
    if ids:
        try:
            id_list = [uuid.UUID(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"非法规则 ID: {e}")
    deleted = rule_service.delete_rules_batch(db, rule_set_id, id_list)
    return {"success": True, "deleted": deleted}


# ============ 批量确认 ============
@router.post("/confirm")
def confirm_rules_batch(
    payload: RuleBatchConfirmRequest,
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    confirmed_by: str = Query("user", description="确认人"),
    db: Session = Depends(get_db),
) -> dict:
    """批量确认规则。提供 ids 则仅确认这些；不提供则确认该规则集下所有 pending 规则。"""
    count = rule_service.confirm_rules_batch(
        db, rule_set_id, payload.ids, confirmed_by=confirmed_by,
    )
    return {"success": True, "confirmed": count, "message": f"已确认 {count} 条规则"}


# ============ 规则快照 ============
@router.get("/snapshots", response_model=list[GraphSnapshotOut])
def list_snapshots(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
) -> list[GraphSnapshotOut]:
    """规则快照列表（按规则集过滤、按时间倒序）。"""
    snaps = rule_service.list_snapshots(db, rule_set_id)
    return [GraphSnapshotOut.model_validate(s) for s in snaps]


@router.get("/snapshots/{snapshot_id}", response_model=GraphSnapshotOut)
def get_snapshot(
    snapshot_id: uuid.UUID, db: Session = Depends(get_db)
) -> GraphSnapshotOut:
    snap = rule_service.get_snapshot(db, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return GraphSnapshotOut.model_validate(snap)


# ============ 语义冲突检测 ============
@router.post("/detect-conflicts", response_model=ConflictDetectionResponse)
def detect_conflicts(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
) -> ConflictDetectionResponse:
    """检测指定规则集内所有启用规则的语义冲突。

    按 (doc_type, check_category) 分组后逐组用 LLM 检测矛盾关系，
    结果写入各规则的 defects 字段，并返回冲突报告。
    """
    result = rule_conflict_detector.run_conflict_detection(db, str(rule_set_id))
    return ConflictDetectionResponse(**result)
