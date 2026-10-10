"""规则管理服务：CRUD + 快照查询。

多 RuleSet 改造后，所有查询和写入都需要按 rule_set_id 过滤，
否则会用错规则集的图谱。
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Rule, RuleSet, RuleSnapshot
from ..schemas.rule import RuleCreate, RuleOut, RuleUpdate
from . import rule_defects
from .doc_normalizer import (
    normalize_doc_type,
    normalize_scope,
    normalize_structure,
)


def format_rule_code(rule_no: int) -> str:
    """规则流水号统一格式：R0001、R0002。"""
    return f"R{int(rule_no):04d}"


def allocate_rule_no(db: Session, rule_set_id: uuid.UUID) -> int:
    """为规则集分配下一个流水号（行锁串行化，删除后不复用）。"""
    rule_set = db.execute(
        select(RuleSet)
        .where(RuleSet.id == rule_set_id)
        .with_for_update()
    ).scalars().first()
    if rule_set is None:
        raise ValueError(f"规则集不存在: {rule_set_id}")
    rule_no = int(rule_set.next_rule_no or 1)
    rule_set.next_rule_no = rule_no + 1
    db.flush()
    return rule_no


def _stamp_defect_rule_code(defects: list[dict] | None, rule_code: str) -> list[dict]:
    """确保缺陷项带所属规则流水号。"""
    stamped: list[dict] = []
    for raw in defects or []:
        if not isinstance(raw, dict):
            continue
        defect = dict(raw)
        defect["rule_code"] = rule_code
        stamped.append(defect)
    return stamped


def list_rules(
    db: Session,
    rule_set_id: uuid.UUID,
    doc_type: Optional[str] = None,
    check_category: Optional[str] = None,
    enabled_only: bool = False,
    defect_severity: Optional[str] = None,
    only_confirmed: bool = False,
) -> list[RuleOut]:
    """规则列表，支持按规则集 / 文件类型 / 检查项 / 启用状态 / 缺陷归桶 / 确认状态过滤。

    defect_severity 采用「归桶」语义（每个规则只属于一个桶，见 rule_defects.classify）：
    none(正常规则) / conflict / error / warning / info，只统计**未处理**缺陷。
    """
    stmt = (
        select(Rule)
        .where(Rule.rule_set_id == rule_set_id)
        .order_by(Rule.doc_type, Rule.check_category, Rule.priority, Rule.rule_no)
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

    # 缺陷归桶过滤在 Python 层做（JSONB 复杂查询）
    if defect_severity:
        result = [r for r in result if rule_defects.matches_bucket(r.defects, defect_severity)]

    return result


def get_rule(db: Session, rule_id: uuid.UUID) -> Optional[Rule]:
    return db.get(Rule, rule_id)


def _accept_defects(
    rule: Rule, *, by: str, note: str | None = rule_defects.CONFIRM_NOTE
) -> int:
    """方案 A：认可规则即认可其全部缺陷——把未处理缺陷标记为已处理。"""
    defects, count = rule_defects.resolve_defects(rule.defects, by=by, note=note)
    if count:
        rule.defects = defects
    return count


def create_rule(db: Session, rule_set_id: uuid.UUID, payload: RuleCreate) -> RuleOut:
    """创建规则（必须挂在指定规则集下）。"""
    try:
        data = payload.model_dump()
        rule_no = allocate_rule_no(db, rule_set_id)
        rule_code = format_rule_code(rule_no)
        data["rule_no"] = rule_no
        data["defects"] = _stamp_defect_rule_code(data.get("defects"), rule_code)
        # 批次 11：写时归一——手工创建/导入的规则同样归一文档类型与字段名
        if data.get("doc_type"):
            data["doc_type"] = normalize_doc_type(db, data["doc_type"]) or data["doc_type"]
        if data.get("scope"):
            data["scope"] = normalize_scope(db, data["scope"])
        if data.get("structure"):
            data["structure"] = normalize_structure(db, data["structure"], data.get("doc_type"))
        rule = Rule(rule_set_id=rule_set_id, **data)
        # 方案 A：创建时即为"已确认"的规则，缺陷一并视为已认可
        if rule.status == "confirmed":
            _accept_defects(rule, by=rule.confirmed_by or rule_defects.RESOLVED_BY_CONFIRM)
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return RuleOut.model_validate(rule)
    except Exception:
        db.rollback()
        raise


def update_rule(
    db: Session, rule_id: uuid.UUID, payload: RuleUpdate
) -> Optional[RuleOut]:
    from datetime import datetime

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
    # 方案 A：把规则置为"已确认"或显式启用（用户认可）时，未处理缺陷标记为已处理，
    # 规则随即进入「正常规则」。仅改文本/标签不会自动消解缺陷，需用户显式确认。
    accepts = data.get("status") == "confirmed" or data.get("enabled") is True
    if accepts and rule.status == "confirmed":
        if rule.confirmed_at is None:
            rule.confirmed_at = datetime.now()
        if not rule.confirmed_by:
            rule.confirmed_by = rule_defects.RESOLVED_BY_CONFIRM
        _accept_defects(rule, by=rule.confirmed_by)
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
    """批量确认规则：状态改为 confirmed 并启用，同时把未处理缺陷标记为已处理。

    提供 ids 时按 id 确认（**包含已 confirmed 但仍有未处理缺陷的规则**，需要重新确认才能进入正常规则）；
    不提供 ids 时确认该规则集下所有 pending 规则。

    Returns:
        实际确认的规则条数
    """
    from datetime import datetime

    stmt = select(Rule).where(Rule.rule_set_id == rule_set_id)
    if ids:
        stmt = stmt.where(Rule.id.in_(ids))
    else:
        stmt = stmt.where(Rule.status != "confirmed")
    rows = db.execute(stmt).scalars().all()
    now = datetime.now()
    for r in rows:
        r.status = "confirmed"
        r.confirmed_at = now
        r.confirmed_by = confirmed_by
        r.enabled = True
        # 方案 A：确认即认可，未处理缺陷标记为已处理 → 规则进入「正常规则」
        _accept_defects(r, by=confirmed_by)
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
        .order_by(Rule.doc_type, Rule.check_category, Rule.priority, Rule.rule_no)
    )
    return list(db.execute(stmt).scalars().all())


def get_defect_summary(db: Session, rule_set_id: uuid.UUID) -> dict:
    """获取规则集缺陷概览统计（**各页签均为规则条数**，互斥且穷尽）。

    每个规则按最严重的未处理缺陷归入唯一桶（冲突 > 错误 > 警告 > 提示 > 正常规则），
    因此 ``total_rules == healthy + conflict + error + warning + info``。

    Returns:
        {"total_rules": N, "healthy": N, "conflict": N, "error": N, "warning": N, "info": N}
    """
    rules = db.execute(
        select(Rule).where(Rule.rule_set_id == rule_set_id)
    ).scalars().all()

    summary = {
        "total_rules": len(rules),
        "healthy": 0,
        "conflict": 0,
        "error": 0,
        "warning": 0,
        "info": 0,
    }
    for r in rules:
        # classify 只会返回 healthy/conflict/error/warning/info
        summary[rule_defects.classify(r.defects)] += 1
    return summary
