# -*- coding: utf-8 -*-
"""字段绑定沉淀（Field Binding）——让审查运行时自己校准两个词表。

背景（判定架构 P2）：

规则侧词表（"报销金额"、"部门负责人签字"）与单据侧词表（"报销金额（元）"、
"审批签字"）由两次独立的 LLM 调用生成，过去靠人工登记别名（document_types.field_aliases）
来对齐；每遇到一个新场景就要改代码/改配置。

本模块把"大模型在判定时已经做出的正确绑定"沉淀成数据：

1. 每次**验证通过**的判定，其 citations/bindings 里的等价映射记入 field_bindings
   （doc_type, rule_field, doc_key, kind），命中次数累加；
2. 命中次数达到阈值且未冲突 → 自动升级为该文档类型的弱别名（写进 document_types.field_aliases）；
   高风险或冲突的绑定停在 observed，等人工在「文档类型」页确认；
3. role_signature 类绑定（"部门负责人签字" ← "审批签字" 值里的角色）作为**学到的角色映射**
   供审查引擎读取——同一个新场景第二次出现时，系统已经认识它。

这样"打补丁"变成"沉淀数据"：代码不再随场景增长，知识随使用增长。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DocumentType, FieldBinding
from .doc_normalizer import normalize_field_name

logger = logging.getLogger(__name__)

# 绑定类型：字段别名 / 签字角色映射
BINDING_KINDS = ("alias", "role_signature")
# 自动升级为别名的默认命中阈值（可由设置覆盖）
DEFAULT_PROMOTE_THRESHOLD = 3


def _norm(value: Any) -> str:
    return normalize_field_name(value)


def record_bindings(
    db: Optional[Session],
    tenant_id: Optional[uuid.UUID],
    doc_type: Any,
    bindings: Iterable[dict],
    *,
    rule_id: Optional[str] = None,
    doc_name: Optional[str] = None,
) -> int:
    """记录/累加绑定。Returns 新增或更新的条数。

    bindings 形如 [{"rule_field": "报销金额", "doc_key": "报销金额（元）", "kind": "alias"}]
    """
    if db is None or tenant_id is None:
        return 0
    doc_type_name = _norm(doc_type)
    if not doc_type_name:
        return 0
    touched = 0
    now = datetime.now()
    for item in bindings:
        if not isinstance(item, dict):
            continue
        rule_field = _norm(item.get("rule_field"))
        doc_key = _norm(item.get("doc_key"))
        kind = str(item.get("kind") or "alias").strip()
        if kind not in BINDING_KINDS:
            kind = "alias"
        if not rule_field or not doc_key or rule_field == doc_key:
            continue  # 同名不是绑定（没有新知识）
        row = db.execute(
            select(FieldBinding).where(
                FieldBinding.tenant_id == tenant_id,
                FieldBinding.doc_type == doc_type_name,
                FieldBinding.rule_field == rule_field,
                FieldBinding.doc_key == doc_key,
                FieldBinding.kind == kind,
            )
        ).scalars().first()
        if row is None:
            row = FieldBinding(
                tenant_id=tenant_id,
                doc_type=doc_type_name,
                rule_field=rule_field,
                doc_key=doc_key,
                kind=kind,
                evidence_count=1,
                status="observed",
                sample_doc=doc_name,
                sample_value=str(item.get("value") or "")[:200] or None,
                rule_ids=[rule_id] if rule_id else [],
            )
            db.add(row)
        else:
            row.evidence_count = int(row.evidence_count or 0) + 1
            row.last_seen = now
            if rule_id and rule_id not in list(row.rule_ids or []):
                row.rule_ids = list(row.rule_ids or []) + [rule_id]
        touched += 1
    if touched:
        db.flush()
    return touched


def find_conflicts(db: Session, tenant_id: uuid.UUID) -> list[FieldBinding]:
    """找出冲突绑定：同一个 (doc_type, doc_key) 被映射到多个 rule_field。"""
    rows = list(
        db.execute(
            select(FieldBinding).where(
                FieldBinding.tenant_id == tenant_id,
                FieldBinding.kind == "alias",
                FieldBinding.status != "rejected",
            )
        ).scalars()
    )
    by_key: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        by_key.setdefault((row.doc_type, row.doc_key), set()).add(row.rule_field)
    conflicted = {k for k, v in by_key.items() if len(v) > 1}
    return [r for r in rows if (r.doc_type, r.doc_key) in conflicted]


def promote_bindings(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    threshold: Optional[int] = None,
) -> list[dict]:
    """把达到阈值的绑定升级为该文档类型的别名。Returns 升级明细。"""
    if db is None or tenant_id is None:
        return []
    threshold = int(threshold or DEFAULT_PROMOTE_THRESHOLD)
    conflicted = {(r.doc_type, r.doc_key) for r in find_conflicts(db, tenant_id)}
    rows = list(
        db.execute(
            select(FieldBinding).where(
                FieldBinding.tenant_id == tenant_id,
                FieldBinding.kind == "alias",
                FieldBinding.status == "observed",
                FieldBinding.evidence_count >= threshold,
            )
        ).scalars()
    )
    promoted: list[dict] = []
    touched_types: dict[str, DocumentType] = {}
    for row in rows:
        if (row.doc_type, row.doc_key) in conflicted:
            logger.info(
                "字段绑定冲突，暂不自动升级：%s.%s（%s 命中 %d 次）",
                row.doc_type, row.doc_key, row.rule_field, row.evidence_count,
            )
            continue
        dt = touched_types.get(row.doc_type)
        if dt is None:
            dt = db.execute(
                select(DocumentType).where(
                    DocumentType.tenant_id == tenant_id,
                    DocumentType.name == row.doc_type,
                )
            ).scalars().first()
            if dt is None:
                continue
            touched_types[row.doc_type] = dt
        aliases = dict(dt.field_aliases or {})
        existing = normalize_field_name(aliases.get(row.doc_key))
        if existing and existing != row.rule_field:
            continue  # 已有不同映射，交给人工
        if existing == row.rule_field:
            row.status = "confirmed"
            row.promoted_at = datetime.now()
            continue
        aliases[row.doc_key] = row.rule_field
        dt.field_aliases = aliases
        row.status = "confirmed"
        row.promoted_at = datetime.now()
        promoted.append(
            {
                "doc_type": row.doc_type,
                "doc_key": row.doc_key,
                "rule_field": row.rule_field,
                "evidence_count": row.evidence_count,
            }
        )
        logger.info(
            "字段绑定自动升级为别名：%s「%s」→「%s」（命中 %d 次）",
            row.doc_type, row.doc_key, row.rule_field, row.evidence_count,
        )
    if promoted:
        db.flush()
    return promoted


def learned_role_fields(db: Session, tenant_id: uuid.UUID, doc_type: Any) -> dict[str, str]:
    """学到的签字角色映射：{角色: 单据字段名}（供审查引擎解析角色签字）。"""
    if db is None or tenant_id is None:
        return {}
    name = _norm(doc_type)
    if not name:
        return {}
    rows = db.execute(
        select(FieldBinding).where(
            FieldBinding.tenant_id == tenant_id,
            FieldBinding.doc_type == name,
            FieldBinding.kind == "role_signature",
            FieldBinding.status != "rejected",
        )
    ).scalars()
    out: dict[str, str] = {}
    for row in rows:
        # rule_field 形如 "部门负责人签字"，doc_key 形如 "审批签字"
        role = row.rule_field
        for suffix in ("签字", "签名", "签署", "签批", "审批", "盖章", "用印"):
            if role.endswith(suffix) and len(role) > len(suffix):
                role = role[: -len(suffix)]
                break
        if role:
            out.setdefault(role, row.doc_key)
    return out


def list_bindings(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[FieldBinding]:
    stmt = select(FieldBinding).where(FieldBinding.tenant_id == tenant_id)
    if doc_type:
        stmt = stmt.where(FieldBinding.doc_type == normalize_field_name(doc_type))
    if status:
        stmt = stmt.where(FieldBinding.status == status)
    stmt = stmt.order_by(FieldBinding.last_seen.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def binding_metrics(db: Session, tenant_id: uuid.UUID) -> dict:
    rows = list(
        db.execute(
            select(FieldBinding).where(FieldBinding.tenant_id == tenant_id)
        ).scalars()
    )
    return {
        "total": len(rows),
        "confirmed": sum(1 for r in rows if r.status == "confirmed"),
        "observed": sum(1 for r in rows if r.status == "observed"),
        "role_signature": sum(1 for r in rows if r.kind == "role_signature"),
    }
