# -*- coding: utf-8 -*-
"""人工修正账本：把"人改了什么"变成模型下次能读到的数据。

为什么需要它（见 docs/项目基线-智能优先原则.md）：
- 人工只该处理 **LLM 判不了**的部分，但修正不能"改完就丢"——不然同一类问题会反复找人；
- 修正只作为**数据**回灌给模型（提示词上下文），不在代码里为某次修正加分支；
- 同一类修正按签名累加命中次数：命中越高，回灌时越优先，也越能说明模型上次没学会。

已有相邻机制：`rule_parse_skill_service.learn_from_correction` 把修正写成 Skill 指令
（只覆盖规则文本/检查项/文件类型三个字段，且入口只有前端弹窗）。本服务是更底层的
通用账本：任何阶段、任何字段的人工修正都先记账，回灌时统一按数据预算裁剪。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LlmCorrection

logger = logging.getLogger(__name__)

# 单条修正存的前后值上限（长文本截断，账本不做全文仓库）
MAX_VALUE_CHARS = 1500
# 回灌提示词的预算：条数与字符数都要小，避免稀释解析注意力
DEFAULT_DIGEST_LIMIT = 15
DEFAULT_DIGEST_MAX_CHARS = 1200
# 值在清单里的展示长度
DIGEST_VALUE_CHARS = 60
# 回灌白名单：只回放能提升"拆分规则"准确率的语义字段
REPLAY_FIELDS: tuple[str, ...] = (
    "doc_type",
    "check_category",
    "scenario",
    "tolerance",
    "structure",
    "scope",
    "intents",
    "rule_text",
    # 图谱/规则结构里的人工修正（点号 = structure 内的路径）
    "structure.condition",
    "structure.exceptions",
    "structure.assertion.operator",
)

_FIELD_LABELS = {
    "doc_type": "文件类型",
    "check_category": "检查项",
    "scenario": "业务场景",
    "tolerance": "容差",
    "structure": "规则结构",
    "scope": "适用范围",
    "intents": "检查意图",
    "rule_text": "规则文本",
    "structure.condition": "规则条件",
    "structure.exceptions": "规则例外",
    "structure.assertion.operator": "比较算子",
}

# 判定结论的中文名（人工纠正清单用）
_VERDICT_LABELS = {
    "pass": "通过",
    "fail": "不通过",
    "unverifiable": "无法核验",
}


def _text(value: Any) -> str:
    """把任意值稳定地序列化成可比较、可存储的文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raw = str(value)
    return raw.strip()[:MAX_VALUE_CHARS]


def _signature(scope: str, rule_set_id: Any, field: str, before: str, after: str) -> str:
    raw = "|".join([scope, str(rule_set_id or ""), field, before, after])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _short(value: str, limit: int = DIGEST_VALUE_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def record_correction(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    scope: str,
    field: str,
    before: Any,
    after: Any,
    rule_set_id: Optional[uuid.UUID] = None,
    target_type: Optional[str] = None,
    target_id: Optional[uuid.UUID] = None,
    doc_type: Optional[str] = None,
    context: Any = None,
    note: Optional[str] = None,
    by_user_id: Optional[uuid.UUID] = None,
) -> Optional[LlmCorrection]:
    """记录一条人工修正；同类修正累加 hit_count。无实际变化时返回 None。

    只 flush 不 commit：跟随调用方的事务（规则更新失败时不应留下半条账）。
    """
    before_text, after_text = _text(before), _text(after)
    if before_text == after_text:
        return None
    signature = _signature(scope, rule_set_id, field, before_text, after_text)
    now = datetime.now()
    row = db.execute(
        select(LlmCorrection).where(
            LlmCorrection.tenant_id == tenant_id,
            LlmCorrection.signature == signature,
        )
    ).scalars().first()
    if row is not None:
        row.hit_count += 1
        row.last_seen = now
        if context and not row.context:
            row.context = _text(context)
        if note:
            row.note = _text(note)
        db.flush()
        return row

    row = LlmCorrection(
        tenant_id=tenant_id,
        scope=scope,
        signature=signature,
        rule_set_id=rule_set_id,
        target_type=target_type,
        target_id=target_id,
        doc_type=doc_type,
        field=field,
        before=before_text,
        after=after_text,
        context=_text(context) if context else None,
        note=_text(note) if note else None,
        hit_count=1,
        created_by=by_user_id,
        first_seen=now,
        last_seen=now,
    )
    db.add(row)
    db.flush()
    return row


def record_field_changes(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    scope: str,
    changes: dict[str, tuple[Any, Any]],
    exclude: Iterable[str] = ("defects", "provenance"),
    **kwargs: Any,
) -> int:
    """按"字段 → (旧值, 新值)"批量记录，返回真正记下的条数。

    `exclude` 里的字段不记账（缺陷清单、溯源等大块结构化数据与解析质量无关）。
    """
    skip = set(exclude or ())
    saved = 0
    for field, (before, after) in changes.items():
        if field in skip:
            continue
        if record_correction(
            db,
            tenant_id,
            scope=scope,
            field=field,
            before=before,
            after=after,
            **kwargs,
        ) is not None:
            saved += 1
    return saved


def corrections_digest(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: Optional[uuid.UUID] = None,
    *,
    limit: int = DEFAULT_DIGEST_LIMIT,
    max_chars: int = DEFAULT_DIGEST_MAX_CHARS,
    fields: Iterable[str] = REPLAY_FIELDS,
) -> str:
    """生成给"规则解析"提示词用的紧凑修正清单（空字符串 = 没有可回灌的修正）。

    排序：本规则集的修正优先 → 命中次数高 → 最近发生；按字符预算截断。
    """
    rows = db.execute(
        select(LlmCorrection)
        .where(
            LlmCorrection.tenant_id == tenant_id,
            LlmCorrection.field.in_(tuple(fields)),
        )
        .order_by(LlmCorrection.hit_count.desc(), LlmCorrection.last_seen.desc())
        .limit(max(limit * 3, limit))
    ).scalars().all()
    if not rows:
        return ""

    def _rank(row: LlmCorrection) -> tuple[int, int, float]:
        same_set = 0 if (rule_set_id and row.rule_set_id == rule_set_id) else 1
        last_seen = row.last_seen.timestamp() if row.last_seen else 0.0
        return (same_set, -int(row.hit_count or 0), -last_seen)

    lines: list[str] = []
    used = 0
    for row in sorted(rows, key=_rank)[:limit]:
        label = _FIELD_LABELS.get(row.field, row.field)
        prefix = f"[{row.doc_type}] " if row.doc_type else ""
        line = (
            f"- {prefix}{label}：把「{_short(row.before)}」改为「{_short(row.after)}」"
            f"（人工修正 {row.hit_count} 次"
        )
        # 带上适用范围（规则原文摘要）：只说"把 A 改成 B"模型无法判断这条口径适用于哪类规则
        context = _short(row.context or "", 50)
        if context:
            line += f"；适用示例：{context}"
        line += "）"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def judge_corrections_digest(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int = 8,
    max_chars: int = 900,
    context_chars: int = 60,
) -> str:
    """生成给"统一判定"提示词用的人工纠正清单（空字符串 = 没有）。

    一行一条："人工把「不通过」纠正为「通过」：<规则/问题摘要>（人工纠正 N 次）"。
    模型据此理解本租户的口径，而不是让 Python 为每种情形写一条判定规则。
    """
    rows = db.execute(
        select(LlmCorrection)
        .where(
            LlmCorrection.tenant_id == tenant_id,
            LlmCorrection.scope == "review_judge",
            LlmCorrection.field == "result",
        )
        .order_by(LlmCorrection.hit_count.desc(), LlmCorrection.last_seen.desc())
        .limit(max(limit * 3, limit))
    ).scalars().all()
    if not rows:
        return ""

    lines: list[str] = []
    used = 0
    for row in rows[:limit]:
        before = _VERDICT_LABELS.get(row.before, row.before)
        after = _VERDICT_LABELS.get(row.after, row.after)
        context = _short(row.context, context_chars)
        line = f"- 人工把「{before}」纠正为「{after}」"
        if context:
            line += f"：{context}"
        line += f"（人工纠正 {row.hit_count} 次）"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
