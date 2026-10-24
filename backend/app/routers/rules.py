"""规则管理路由。"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_permission
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    doc_type: Optional[str] = Query(None),
    check_category: Optional[str] = Query(None),
    enabled_only: bool = Query(False),
    defect_severity: Optional[str] = Query(
        None,
        description=(
            "按缺陷归桶过滤（每个规则只属于一个桶，均为未处理缺陷）: "
            "none(正常规则) / conflict / error / warning / info"
        ),
    ),
    only_confirmed: bool = Query(False),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.view")),
) -> list[RuleOut]:
    """规则列表，支持按规则集 / 文件类型 / 检查项 / 启用状态 / 缺陷归桶过滤。"""
    try:
        return rule_service.list_rules(
            db, ctx.tenant.id, rule_set_id, doc_type, check_category, enabled_only,
            defect_severity=defect_severity,
            only_confirmed=only_confirmed,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(
    payload: RuleCreate,
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> RuleOut:
    """创建规则（挂在指定规则集下）。"""
    try:
        return rule_service.create_rule(db, ctx.tenant.id, rule_set_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/import-batch", response_model=RuleImportResponse)
def import_rules_batch(
    payload: RuleImportRequest,
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> RuleImportResponse:
    """批量导入自然语言规则清单（LLM 解析后入库），归到指定规则集下。

    可选的 skill_ids 参数指定应用的 Skill；不传则使用该规则集已启用的所有 Skill。
    """
    if rule_service.get_rule_set(db, ctx.tenant.id, rule_set_id) is None:
        raise HTTPException(status_code=404, detail="规则集不存在")
    try:
        result = rule_import_service.import_rules_with_skills(
            db,
            ctx.tenant.id,
            rule_set_id,
            payload.raw_text,
            skill_ids=payload.skill_ids,
        )
        return RuleImportResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: uuid.UUID,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> RuleOut:
    rule = rule_service.update_rule(db, ctx.tenant.id, rule_id, payload)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")

    # 如果规则被禁用，同步清理图谱中该规则对应的节点/关系
    if payload.enabled is False:
        try:
            from ..services.rule_service import get_latest_snapshot
            from ..neo4j_client import get_neo4j_client
            snap = get_latest_snapshot(db, ctx.tenant.id, rule.rule_set_id)
            if snap and snap.graph_id:
                neo4j = get_neo4j_client()
                neo4j.remove_nodes_by_rule_id(
                    snap.graph_id,
                    ctx.tenant.id,
                    str(rule_id),
                )
                logger.info("规则 %s 已禁用，已从图谱 %s 中移除对应节点", rule_id, snap.graph_id)
        except Exception:
            logger.warning("规则禁用后同步图谱失败（不影响规则更新）", exc_info=True)

    return rule


@router.delete("/{rule_id}")
def delete_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> dict:
    if not rule_service.delete_rule(db, ctx.tenant.id, rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"success": True, "message": "规则已删除"}


@router.delete("")
def delete_rules_batch(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    ids: Optional[str] = Query(
        None, description="逗号分隔的规则 ID 列表；不提供则清空该规则集全部规则"
    ),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> dict:
    """批量删除规则。提供 ids 则仅删除指定规则；否则清空该规则集下所有规则。"""
    id_list: Optional[list[uuid.UUID]] = None
    if ids:
        try:
            id_list = [uuid.UUID(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"非法规则 ID: {e}")
    try:
        deleted = rule_service.delete_rules_batch(
            db,
            ctx.tenant.id,
            rule_set_id,
            id_list,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "deleted": deleted}


# ============ 批量确认 ============
@router.post("/confirm")
def confirm_rules_batch(
    payload: RuleBatchConfirmRequest,
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> dict:
    """批量确认规则。提供 ids 则仅确认这些；不提供则确认该规则集下所有 pending 规则。

    方案 A：确认即视为用户认可该规则及其全部缺陷，未处理缺陷会标记为"已处理"，
    规则随即进入「正常规则」页签。
    """
    try:
        count = rule_service.confirm_rules_batch(
            db,
            ctx.tenant.id,
            rule_set_id,
            str(ctx.user.id),
            payload.ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "confirmed": count, "message": f"已确认 {count} 条规则"}


# ============ 缺陷概览 ============
@router.get("/defect-summary")
def get_defect_summary(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.view")),
) -> dict:
    """获取规则集缺陷概览统计：正常/冲突/错误/警告/提示各有多少**条规则**（互斥且穷尽）。"""
    try:
        return rule_service.get_defect_summary(db, ctx.tenant.id, rule_set_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ============ 规则快照 ============
@router.get("/snapshots", response_model=list[GraphSnapshotOut])
def list_snapshots(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.view")),
) -> list[GraphSnapshotOut]:
    """规则快照列表（按规则集过滤、按时间倒序）。"""
    try:
        snaps = rule_service.list_snapshots(db, ctx.tenant.id, rule_set_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return [GraphSnapshotOut.model_validate(s) for s in snaps]


@router.get("/snapshots/{snapshot_id}", response_model=GraphSnapshotOut)
def get_snapshot(
    snapshot_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.view")),
) -> GraphSnapshotOut:
    snap = rule_service.get_snapshot(db, ctx.tenant.id, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return GraphSnapshotOut.model_validate(snap)


# ============ 语义冲突检测 ============
@router.post("/detect-conflicts", response_model=ConflictDetectionResponse)
def detect_conflicts(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("rules.manage")),
) -> ConflictDetectionResponse:
    """检测指定规则集内所有规则的语义冲突。

    按 (doc_type, check_category) 分组后逐组用 LLM 检测矛盾关系，
    结果写入各规则的 defects 字段，并将受影响规则回落到待确认/禁用，返回冲突报告。
    """
    try:
        result = rule_conflict_detector.run_conflict_detection(
            db,
            ctx.tenant.id,
            str(rule_set_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return ConflictDetectionResponse(**result)
