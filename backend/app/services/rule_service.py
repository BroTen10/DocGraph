"""规则管理服务：CRUD + 快照查询。"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Rule, RuleSnapshot
from ..schemas.rule import RuleCreate, RuleOut, RuleUpdate


def list_rules(
    db: Session,
    doc_type: Optional[str] = None,
    check_category: Optional[str] = None,
    enabled_only: bool = False,
) -> list[RuleOut]:
    """规则列表，支持按文件类型 / 检查项 / 启用状态过滤。"""
    stmt = select(Rule).order_by(Rule.doc_type, Rule.check_category, Rule.priority)
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


def create_rule(db: Session, payload: RuleCreate) -> RuleOut:
    rule = Rule(**payload.model_dump())
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


def list_snapshots(db: Session) -> list[RuleSnapshot]:
    """规则快照列表（按时间倒序）。"""
    stmt = select(RuleSnapshot).order_by(RuleSnapshot.snapshot_time.desc())
    return list(db.execute(stmt).scalars().all())


def get_snapshot(db: Session, snapshot_id: uuid.UUID) -> Optional[RuleSnapshot]:
    return db.get(RuleSnapshot, snapshot_id)


def get_latest_snapshot(db: Session) -> Optional[RuleSnapshot]:
    stmt = (
        select(RuleSnapshot)
        .order_by(RuleSnapshot.snapshot_time.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def get_enabled_rules_for_snapshot(db: Session) -> list[Rule]:
    """获取当前所有启用的规则（用于生成快照）。"""
    stmt = (
        select(Rule)
        .where(Rule.enabled.is_(True))
        .order_by(Rule.doc_type, Rule.check_category, Rule.priority)
    )
    return list(db.execute(stmt).scalars().all())
