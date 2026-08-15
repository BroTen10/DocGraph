"""规则管理服务：CRUD + 快照查询。

多 RuleSet 改造后，所有查询和写入都需要按 rule_set_id 过滤，
否则会用错规则集的图谱。
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Rule, RuleSnapshot
from ..schemas.rule import RuleCreate, RuleOut, RuleUpdate
from .doc_normalizer import (
    normalize_doc_type,
    normalize_scope,
    normalize_structure,
)


def list_rules(
    db: Session,
    rule_set_id: uuid.UUID,
    doc_type: Optional[str] = None,
    check_category: Optional[str] = None,
    enabled_only: bool = False,
    defect_severity: Optional[str] = None,
    only_confirmed: bool = False,
) -> list[RuleOut]:
    """规则列表，支持按规则集 / 文件类型 / 检查项 / 启用状态 / 缺陷严重程度 / 确认状态过滤。"""
    stmt = (
        select(Rule)
        .where(Rule.rule_set_id == rule_set_id)
        .order_by(Rule.doc_type, Rule.check_category, Rule.priority)
    )
    if doc_type:
        stmt = stmt.where(Rule.doc_type == doc_type)
    if check_category:
        stmt = stmt.where(Rule.check_category == check_category)
    if enabled_only:
        stmt = stmt.where(Rule.enabled.is_(True))
    if only_confirmed:
        stmt = stmt.where(Rule.status == "confirmed")
    rows = db.execute(stmt).scalars().all()
    result = [RuleOut.model_validate(r) for r in rows]

    # defect_severity 过滤在 Python 层做（JSONB 复杂查询）
    if defect_severity:
        if defect_severity == "none":
            # 无缺陷的规则
            result = [r for r in result if not r.defects or len(r.defects) == 0]
        elif defect_severity == "conflict":
            # 有冲突类型缺陷的规则
            conflict_types = {"logical_contradiction", "boundary_overlap", "redundant"}
            result = [r for r in result if any(d.type in conflict_types for d in (r.defects or []))]
        elif defect_severity == "error":
            result = [r for r in result if any(d.severity == "error" for d in (r.defects or []))]
        elif defect_severity == "warning":
            result = [r for r in result if any(d.severity == "warning" for d in (r.defects or []))]
        elif defect_severity == "info":
            result = [r for r in result if any(d.severity == "info" for d in (r.defects or []))]

    return result


def get_rule(db: Session, rule_id: uuid.UUID) -> Optional[Rule]:
    return db.get(Rule, rule_id)


def create_rule(db: Session, rule_set_id: uuid.UUID, payload: RuleCreate) -> RuleOut:
    """创建规则（必须挂在指定规则集下）。"""
    data = payload.model_dump()
    # 批次 11：写时归一——手工创建/导入的规则同样归一文档类型与字段名
    if data.get("doc_type"):
        data["doc_type"] = normalize_doc_type(db, data["doc_type"]) or data["doc_type"]
    if data.get("scope"):
        data["scope"] = normalize_scope(db, data["scope"])
    if data.get("structure"):
        data["structure"] = normalize_structure(db, data["structure"], data.get("doc_type"))
    rule = Rule(rule_set_id=rule_set_id, **data)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return RuleOut.model_validate(rule)


def update_rule(
    db: Session, rule_id: uuid.UUID, payload: RuleUpdate
) -> Optional[RuleOut]:
    rule = db.get(Rule, rule_id)
    if rule is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    # 批次 11：写时归一——手工编辑规则同样归一文档类型与字段名
    if data.get("doc_type"):
        data["doc_type"] = normalize_doc_type(db, data["doc_type"]) or data["doc_type"]
    if data.get("scope"):
        data["scope"] = normalize_scope(db, data["scope"])
    if data.get("structure"):
        data["structure"] = normalize_structure(
            db, data["structure"], data.get("doc_type") or rule.doc_type
        )
    for k, v in data.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return RuleOut.model_validate(rule)


def delete_rule(db: Session, rule_id: uuid.UUID) -> bool:
    rule = db.get(Rule, rule_id)
    if rule is None:
        return False
    db.delete(rule)
    db.commit()
    return True


def delete_rules_batch(
    db: Session,
    rule_set_id: uuid.UUID,
    ids: Optional[list[uuid.UUID]] = None,
) -> int:
    """批量删除规则。提供 ids 则仅删除这些（且必须属于该规则集）；否则清空该规则集全部规则。

    Returns:
        实际删除的规则条数
    """
    stmt = select(Rule).where(Rule.rule_set_id == rule_set_id)
    if ids:
        stmt = stmt.where(Rule.id.in_(ids))
    rows = db.execute(stmt).scalars().all()
    n = len(rows)
    for r in rows:
        db.delete(r)
    db.commit()
    return n


def list_snapshots(db: Session, rule_set_id: uuid.UUID) -> list[RuleSnapshot]:
    """规则快照列表（按规则集过滤、按时间倒序）。"""
    stmt = (
        select(RuleSnapshot)
        .where(RuleSnapshot.rule_set_id == rule_set_id)
        .order_by(RuleSnapshot.snapshot_time.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_snapshot(db: Session, snapshot_id: uuid.UUID) -> Optional[RuleSnapshot]:
    return db.get(RuleSnapshot, snapshot_id)


def get_latest_snapshot(
    db: Session, rule_set_id: uuid.UUID
) -> Optional[RuleSnapshot]:
    """获取指定规则集下的最新快照（必须按 rule_set_id 过滤）。"""
    stmt = (
        select(RuleSnapshot)
        .where(RuleSnapshot.rule_set_id == rule_set_id)
        .order_by(RuleSnapshot.snapshot_time.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def confirm_rules_batch(
    db: Session, rule_set_id: uuid.UUID, ids: Optional[list[uuid.UUID]] = None,
    confirmed_by: str = "user",
) -> int:
    """批量确认规则：将指定规则（或所有 pending 规则）状态改为 confirmed 并启用。

    Returns:
        实际确认的规则条数
    """
    from datetime import datetime
    stmt = select(Rule).where(
        Rule.rule_set_id == rule_set_id,
        Rule.status != "confirmed",
    )
    if ids:
        stmt = stmt.where(Rule.id.in_(ids))
    rows = db.execute(stmt).scalars().all()
    now = datetime.now()
    for r in rows:
        r.status = "confirmed"
        r.confirmed_at = now
        r.confirmed_by = confirmed_by
        r.enabled = True
    db.commit()
    return len(rows)


def get_enabled_rules_for_snapshot(
    db: Session, rule_set_id: uuid.UUID
) -> list[Rule]:
    """获取指定规则集下所有已启用且已确认的规则（用于生成快照/图谱构建）。"""
    stmt = (
        select(Rule)
        .where(Rule.rule_set_id == rule_set_id)
        .where(Rule.enabled.is_(True))
        .where(Rule.status == "confirmed")
        .order_by(Rule.doc_type, Rule.check_category, Rule.priority)
    )
    return list(db.execute(stmt).scalars().all())


def get_defect_summary(db: Session, rule_set_id: uuid.UUID) -> dict:
    """获取规则集缺陷概览统计。

    Returns:
        {"total_rules": N, "healthy": N, "conflict": N, "error": N, "warning": N, "info": N}
    """
    rules = db.execute(
        select(Rule).where(Rule.rule_set_id == rule_set_id)
    ).scalars().all()

    total = len(rules)
    conflict = 0
    error = 0
    warning = 0
    info = 0
    conflict_types = {"logical_contradiction", "boundary_overlap", "redundant"}

    for r in rules:
        defects = r.defects or []
        has_conflict = False
        for d in defects:
            if d.get("type") in conflict_types:
                has_conflict = True
            sev = d.get("severity", "info")
            if sev == "error":
                error += 1
            elif sev == "warning":
                warning += 1
            else:
                info += 1
        if has_conflict:
            conflict += 1

    # healthy = 无任何缺陷的规则数
    healthy = sum(1 for r in rules if not r.defects or len(r.defects) == 0)

    return {
        "total_rules": total,
        "healthy": healthy,
        "conflict": conflict,
        "error": error,
        "warning": warning,
        "info": info,
    }
