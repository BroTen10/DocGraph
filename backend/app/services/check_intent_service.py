"""检查项（检查意图）注册表服务。

设计目标（与 doc_normalizer / DocumentType 对称）：
- **写时归一**：规则导入时把 LLM 写出的检查项名称映射到规范名（别名表）；
- **未知即登记**：未命中的名称登记为 pending_review，人工确认后才参与矩阵分列与执行分派；
- **可治理**：支持改名（同步改写存量规则）、合并、启停、删除。

生产端只在这里改「检查项」口径，避免"同一件事多个名字"在规则列表里繁殖。
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import constants as C
from ..models import CheckIntent, Rule, RuleSet

logger = logging.getLogger(__name__)

#: 内置四类检查项的默认排序
_BUILTIN_SORT = {
    C.CHECK_COMPLETENESS: 10,
    C.CHECK_STAMP: 20,
    C.CHECK_ACCURACY: 30,
    C.CHECK_TIME_LOGIC: 40,
}

#: 内置四类检查项的说明（展示用）
_BUILTIN_DESCRIPTION = {
    C.CHECK_COMPLETENESS: "必备文件与材料是否齐全（程序化校验）",
    C.CHECK_STAMP: "印章、签章、版本、格式等形式要件（程序化校验）",
    C.CHECK_ACCURACY: "字段比较与内容一致性（结构化断言或语义审查）",
    C.CHECK_TIME_LOGIC: "日期先后与时间区间（结构化断言或语义审查）",
}


def seed_builtin_intents(db: Session, tenant_id: uuid.UUID) -> int:
    """幂等写入内置四类检查项（已存在则跳过，不覆盖用户改名/别名）。"""
    existing = set(
        db.execute(
            select(CheckIntent.name).where(CheckIntent.tenant_id == tenant_id)
        ).scalars()
    )
    added = 0
    for name in C.CHECK_CATEGORIES:
        if name in existing:
            continue
        db.add(
            CheckIntent(
                tenant_id=tenant_id,
                name=name,
                description=_BUILTIN_DESCRIPTION.get(name),
                aliases=[],
                sort_order=_BUILTIN_SORT.get(name, 100),
                source="seed",
                status="active",
            )
        )
        added += 1
    db.flush()
    return added


def list_intents(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    status: str | None = None,
) -> list[CheckIntent]:
    """列出检查项（按 sort_order、name 排序）。"""
    stmt = select(CheckIntent).where(CheckIntent.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(CheckIntent.status == status)
    return list(
        db.execute(
            stmt.order_by(CheckIntent.sort_order, CheckIntent.name)
        ).scalars().all()
    )


def active_intent_names(db: Session, tenant_id: uuid.UUID) -> list[str]:
    """已确认（active）的检查项名称，按展示顺序。"""
    return [
        row.name
        for row in list_intents(db, tenant_id, status="active")
    ]


def build_intent_alias_index(db: Session, tenant_id: uuid.UUID) -> dict[str, str]:
    """别名 → 规范名索引（只取 active 与 pending_review，rejected 不参与归一）。"""
    index: dict[str, str] = {}
    for row in list_intents(db, tenant_id):
        if row.status == "rejected":
            continue
        index.setdefault(row.name, row.name)
        for alias in row.aliases or []:
            alias = str(alias).strip()
            if alias and alias != row.name:
                index[alias] = row.name
    return index


def normalize_intent_name(
    db: Session,
    name: object,
    tenant_id: uuid.UUID | None = None,
) -> str:
    """检查项名称 → 规范名。未命中任何规范名/别名时原样返回（交由登记流程处理）。"""
    s = str(name).strip() if name is not None else ""
    if not s or tenant_id is None:
        return s
    return build_intent_alias_index(db, tenant_id).get(s, s)


def find_intent(
    db: Session, tenant_id: uuid.UUID, name: str
) -> Optional[CheckIntent]:
    return db.execute(
        select(CheckIntent).where(
            CheckIntent.tenant_id == tenant_id,
            CheckIntent.name == name,
        )
    ).scalars().first()


def resolve_or_register(
    db: Session,
    tenant_id: uuid.UUID,
    name: object,
    *,
    auto_confirm: bool = False,
) -> tuple[str, bool]:
    """把 LLM 写出的检查项名称落地为规范名。

    Returns:
        (规范名, 是否为本次新登记)。空名称原样返回 (\"\", False)。
    """
    canonical = normalize_intent_name(db, name, tenant_id)
    if not canonical:
        return "", False
    if find_intent(db, tenant_id, canonical) is not None:
        return canonical, False
    db.add(
        CheckIntent(
            tenant_id=tenant_id,
            name=canonical,
            description=None,
            aliases=[],
            sort_order=100,
            source="rule_import",
            status="active" if auto_confirm else "pending_review",
        )
    )
    db.flush()
    logger.info("规则导入发现新检查项（%s）: %s", "active" if auto_confirm else "pending_review", canonical)
    return canonical, True


def count_rules_by_intent(db: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """统计每个检查项被多少条规则使用（改名/删除前的风险提示）。"""
    rows = db.execute(
        select(Rule.check_category, func.count(Rule.id))
        .where(Rule.tenant_id == tenant_id, Rule.check_category.is_not(None))
        .group_by(Rule.check_category)
    ).all()
    return {str(k): int(v) for k, v in rows if k}


def create_intent(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    name: str,
    description: str | None = None,
    aliases: Iterable[str] | None = None,
    sort_order: int = 100,
    status: str = "active",
) -> CheckIntent:
    canonical = str(name).strip()
    if not canonical:
        raise ValueError("检查项名称不能为空")
    if find_intent(db, tenant_id, canonical) is not None:
        raise ValueError(f"检查项「{canonical}」已存在")
    row = CheckIntent(
        tenant_id=tenant_id,
        name=canonical,
        description=description,
        aliases=_clean_aliases(aliases, canonical),
        sort_order=sort_order,
        source="manual",
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_intent(
    db: Session,
    tenant_id: uuid.UUID,
    intent_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    aliases: Iterable[str] | None = None,
    sort_order: int | None = None,
    status: str | None = None,
) -> Optional[CheckIntent]:
    """更新检查项。改名会同步改写存量规则的 check_category / intents / 规则集清单。"""
    row = db.execute(
        select(CheckIntent).where(
            CheckIntent.id == intent_id,
            CheckIntent.tenant_id == tenant_id,
        )
    ).scalars().first()
    if row is None:
        return None

    old_name = row.name
    new_name = str(name).strip() if name is not None else None
    if new_name and new_name != old_name:
        if find_intent(db, tenant_id, new_name) is not None:
            raise ValueError(f"检查项「{new_name}」已存在，如需合并请在原检查项里删掉另一个")
        row.name = new_name
    if description is not None:
        row.description = description
    if aliases is not None:
        row.aliases = _clean_aliases(aliases, row.name)
    if sort_order is not None:
        row.sort_order = int(sort_order)
    if status is not None:
        if status not in ("active", "pending_review", "rejected"):
            raise ValueError(f"未知状态: {status}")
        row.status = status
    db.flush()

    if new_name and new_name != old_name:
        moved = rewrite_intent_refs(db, tenant_id, old_name, new_name)
        logger.info("检查项改名: %s → %s（改写 %d 条引用）", old_name, new_name, moved)

    db.commit()
    db.refresh(row)
    return row


def rewrite_intent_refs(
    db: Session, tenant_id: uuid.UUID, old_name: str, new_name: str
) -> int:
    """把存量规则里的旧检查项名改写为新名（check_category / intents / scope.intents）。"""
    changed = 0
    rules = db.execute(
        select(Rule).where(Rule.tenant_id == tenant_id)
    ).scalars().all()
    for rule in rules:
        touched = False
        if rule.check_category == old_name:
            rule.check_category = new_name
            touched = True
        for field in ("intents",):
            values = list(getattr(rule, field) or [])
            if old_name in values:
                setattr(rule, field, [new_name if v == old_name else v for v in values])
                touched = True
        scope = dict(rule.scope or {})
        scope_intents = list(scope.get("intents") or [])
        if old_name in scope_intents:
            scope["intents"] = [new_name if v == old_name else v for v in scope_intents]
            rule.scope = scope
            touched = True
        if touched:
            changed += 1

    # 规则集声明清单同步
    for rs in db.execute(
        select(RuleSet).where(RuleSet.tenant_id == tenant_id)
    ).scalars().all():
        cats = list(rs.check_categories or [])
        if old_name in cats:
            merged: list[str] = []
            for item in cats:
                value = new_name if item == old_name else item
                if value not in merged:
                    merged.append(value)
            rs.check_categories = merged
    db.flush()
    return changed


def merge_intent(
    db: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> Optional[CheckIntent]:
    """把 source 检查项合并进 target：改写存量引用，source 标记为 rejected 并留作别名。"""
    if source_id == target_id:
        raise ValueError("不能合并到自身")
    source = db.execute(
        select(CheckIntent).where(
            CheckIntent.id == source_id, CheckIntent.tenant_id == tenant_id
        )
    ).scalars().first()
    target = db.execute(
        select(CheckIntent).where(
            CheckIntent.id == target_id, CheckIntent.tenant_id == tenant_id
        )
    ).scalars().first()
    if source is None or target is None:
        return None

    rewrite_intent_refs(db, tenant_id, source.name, target.name)
    # 旧名留作别名，保证后续导入写时归一到 target
    aliases = list(target.aliases or [])
    for alias in [source.name, *(source.aliases or [])]:
        alias = str(alias).strip()
        if alias and alias != target.name and alias not in aliases:
            aliases.append(alias)
    target.aliases = aliases
    source.status = "rejected"
    source.aliases = []
    db.commit()
    db.refresh(target)
    return target


def delete_intent(
    db: Session, tenant_id: uuid.UUID, intent_id: uuid.UUID
) -> bool:
    """删除检查项。仍被规则引用时拒绝删除（避免规则变成无主检查项）。"""
    row = db.execute(
        select(CheckIntent).where(
            CheckIntent.id == intent_id, CheckIntent.tenant_id == tenant_id
        )
    ).scalars().first()
    if row is None:
        return False
    used = count_rules_by_intent(db, tenant_id).get(row.name, 0)
    if used:
        raise ValueError(f"检查项「{row.name}」仍被 {used} 条规则使用，请先改名或合并")
    db.delete(row)
    db.commit()
    return True


def _clean_aliases(aliases: Iterable[str] | None, canonical: str) -> list[str]:
    out: list[str] = []
    for alias in aliases or []:
        value = str(alias).strip()
        if value and value != canonical and value not in out:
            out.append(value)
    return out
