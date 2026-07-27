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


def list_rules(
    db: Session,
    rule_set_id: uuid.UUID,
    doc_type: Optional[str] = None,
    check_category: Optional[str] = None,
    enabled_only: bool = False,
) -> list[RuleOut]:
    """规则列表，支持按规则集 / 文件类型 / 检查项 / 启用状态过滤。"""
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
    rows = db.execute(stmt).scalars().all()
    return [RuleOut.model_validate(r) for r in rows]


def get_rule(db: Session, rule_id: uuid.UUID) -> Optional[Rule]:
    return db.get(Rule, rule_id)


def create_rule(db: Session, rule_set_id: uuid.UUID, payload: RuleCreate) -> RuleOut:
    """创建规则（必须挂在指定规则集下）。"""
    rule = Rule(rule_set_id=rule_set_id, **payload.model_dump())
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


def get_enabled_rules_for_snapshot(
    db: Session, rule_set_id: uuid.UUID
) -> list[Rule]:
    """获取指定规则集下所有启用的规则（用于生成快照）。"""
    stmt = (
        select(Rule)
        .where(Rule.rule_set_id == rule_set_id)
        .where(Rule.enabled.is_(True))
        .order_by(Rule.doc_type, Rule.check_category, Rule.priority)
    )
    return list(db.execute(stmt).scalars().all())
