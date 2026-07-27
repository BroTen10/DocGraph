"""规则批量导入服务：用 LLM 把自然语言规则清单解析为结构化规则并入库。

流程：
1. 接收一段自然语言规则清单文本（可含多条规则）
2. 调用 DeepSeek LLM 解析为 JSON 数组，每项含 doc_type / check_category / rule_text / tolerance / priority
3. 校验 doc_type / check_category 在合法枚举内
4. 批量写入 rules 表，返回导入结果
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..constants import ALL_DOC_TYPES, CHECK_CATEGORIES
from ..llm_client import LLMError, get_llm_client
from ..models import Rule, RuleSet
from .rule_service import create_rule
from ..schemas.rule import RuleCreate

logger = logging.getLogger(__name__)


# ============ LLM 提示词 ============
_SYSTEM_PROMPT = """你是单证审查规则解析助手。任务：把用户提供的自然语言规则清单，解析为结构化的规则列表。

输出契约（严格 JSON，不要输出任何其他内容）：
{
  "rules": [
    {
      "doc_type": "文件类型（必须是给定枚举之一）",
      "check_category": "检查项（必须是给定枚举之一）",
      "rule_text": "规则文本（简洁、可执行的自然语言描述）",
      "tolerance": {"amount_percent": 数字或null, "weight_kg": 数字或null, "time_days": 数字或null, "allow_same_day": 布尔或null},
      "priority": 数字（越小越先，默认100）,
      "confidence": 0-1之间的浮点数，代表你对这条规则理解的确定程度
    }
  ]
}

规则：
1. doc_type **优先**从下方文件类型枚举选择；若规则确实涉及该枚举未覆盖的新文件类型（如特定客户/业务的专用文件），可以提出合理的文件类型名称，保持简洁、无歧义
2. check_category **优先**从下方检查项枚举选择；若确实需要新的检查类别（如"合规性""格式规范"），可以提出合理的新类别名
3. rule_text 用简洁中文描述，如"报关单数量应不大于委托单数量"
4. tolerance 只在规则涉及金额/重量/时间比对时填写，否则字段留 null
5. priority 默认 100；齐套性规则建议 10，基础判断建议 20，信息准确性建议 30，时间逻辑建议 40
6. 一条自然语言描述拆为一条规则；若用户文本含多条规则，全部解析
7. 忽略与单证审查无关的内容
8. confidence 反映你对这条规则确信程度：规则描述非常清楚、无歧义则接近 1.0；含模糊表述（如"部分情况""一般""可能"）则适当降低；完全不确定或不合理则为 0.0"""

_USER_PROMPT_TEMPLATE = """可用文件类型枚举：{doc_types}

可用检查项枚举：{check_categories}

请解析以下规则清单：

---
{raw_text}
---

请输出 JSON。"""


# 单次 LLM 调用允许的最大输入文本长度（字符）。超过则分段解析，避免输出截断。
_MAX_CHUNK_CHARS = 2000


def _split_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """把长文本切成 ≤ max_chars 的块，尽量按段落/行边界切，避免切断一条规则。

    返回至少包含一个元素的列表；若整体超短则整段返回。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    # 先按空行分段
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 2 <= max_chars:
            buf = (buf + "\n\n" + para) if buf else para
        else:
            flush()
            if len(para) <= max_chars:
                buf = para
            else:
                # 单段超长，退化为按行切；行仍超长则按字符硬切
                for line in para.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if len(line) > max_chars:
                        for i in range(0, len(line), max_chars):
                            seg = line[i : i + max_chars]
                            if len(buf) + len(seg) + 1 <= max_chars:
                                buf = (buf + "\n" + seg) if buf else seg
                            else:
                                flush()
                                buf = seg
                        continue
                    if len(buf) + len(line) + 1 <= max_chars:
                        buf = (buf + "\n" + line) if buf else line
                    else:
                        flush()
                        buf = line
    flush()
    return chunks or [text]


def import_rules_from_text(
    db: Session, rule_set_id: uuid.UUID, raw_text: str
) -> dict[str, Any]:
    """从自然语言规则清单文本批量导入规则。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（导入规则归到该规则集下）
        raw_text: 自然语言规则清单文本

    Returns:
        {"total": 解析总数, "imported": 入库成功数, "skipped": 跳过数, "rules": [入库的规则], "errors": [跳过原因]}
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("规则清单文本为空")

    llm = get_llm_client()
    doc_types_str = "、".join(ALL_DOC_TYPES)
    check_categories_str = "、".join(CHECK_CATEGORIES)

    # 长文本分段解析：避免单次输出超 max_tokens 被截断（JSON 不完整）
    chunks = _split_text(raw_text)
    logger.info("规则导入分段: 共 %d 段, 各段长度=%s", len(chunks), [len(c) for c in chunks])
    raw_rules: list[dict] = []
    chunk_errors: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            doc_types=doc_types_str,
            check_categories=check_categories_str,
            raw_text=chunk,
        )
        try:
            resp = llm.chat_json(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
        except (LLMError, ValueError) as e:
            logger.error("规则导入 第 %d/%d 段 LLM 解析失败: %s", idx, len(chunks), e)
            chunk_errors.append(f"第 {idx} 段解析失败: {e}")
            continue
        rules = resp.get("rules", [])
        if isinstance(rules, list):
            raw_rules.extend(r for r in rules if isinstance(r, dict))
        else:
            chunk_errors.append(f"第 {idx} 段：LLM 返回结构异常，已跳过")

    if not raw_rules:
        detail = f"（共 {len(chunks)} 段，{len(chunk_errors)} 段失败）" if chunk_errors else ""
        raise ValueError(f"LLM 未解析出任何规则{detail}")

    imported: list[dict] = []
    errors: list[str] = []
    # 收集新发现的类型，用于后续更新 rule_set.doc_types / check_categories
    new_doc_types: set[str] = set()
    new_check_categories: set[str] = set()

    for i, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 条：非合法对象，跳过")
            continue
        doc_type = str(item.get("doc_type", "")).strip()
        check_category = str(item.get("check_category", "")).strip()
        rule_text = str(item.get("rule_text", "")).strip()
        # 不再做硬枚举校验，仅检查非空；同时收集新类型
        if not doc_type:
            errors.append(f"第 {i} 条：文件类型为空，跳过")
            continue
        if not check_category:
            errors.append(f"第 {i} 条：检查项为空，跳过")
            continue
        if not rule_text:
            errors.append(f"第 {i} 条：规则文本为空，跳过")
            continue
        new_doc_types.add(doc_type)
        new_check_categories.add(check_category)

        # ----- 同规则集去重：相同 (doc_type, check_category, 归一化 rule_text) 跳过 -----
        normed = "".join(ch for ch in rule_text if ch.isalnum()).lower()
        dup = db.execute(
            select(Rule).where(
                Rule.rule_set_id == rule_set_id,
                Rule.doc_type == doc_type,
                Rule.check_category == check_category,
                # 简单归一化匹配
                Rule.rule_text.ilike(f"%{normed[:30]}%"),
            )
        ).scalars().first()
        if dup:
            logger.info("同集去重跳过: [%s/%s] %s...", doc_type, check_category, rule_text[:40])
            skipped_detail = f"第 {i} 条：与已有规则 [{doc_type}/{check_category}] 重复（{rule_text[:30]}...），跳过"
            errors.append(skipped_detail)
            continue
        # -----

        # 组装容差
        tol_raw = item.get("tolerance") or {}
        tolerance: dict[str, Any] = {}
        if isinstance(tol_raw, dict):
            if tol_raw.get("amount_percent") is not None:
                tolerance["amount_percent"] = tol_raw["amount_percent"]
            if tol_raw.get("weight_kg") is not None:
                tolerance["weight_kg"] = tol_raw["weight_kg"]
            if tol_raw.get("time_days") is not None:
                tolerance["time_days"] = tol_raw["time_days"]
            if tol_raw.get("allow_same_day") is not None:
                tolerance["allow_same_day"] = tol_raw["allow_same_day"]

        priority = item.get("priority", 100)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = 100

        # 置信度与状态
        confidence = item.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            status = "confirmed" if confidence >= settings.llm_confidence_threshold else "pending"
        else:
            status = "pending"  # 旧格式无置信度，直接待确认

        try:
            payload = RuleCreate(
                doc_type=doc_type,
                check_category=check_category,
                rule_text=rule_text,
                tolerance=tolerance,
                enabled=True,
                priority=priority,
                confidence=confidence,
                status=status,
            )
            rule_out = create_rule(db, rule_set_id, payload)
            imported.append(rule_out.model_dump(mode="json"))
        except Exception as e:
            errors.append(f"第 {i} 条：入库失败 - {e}")

    errors.extend(chunk_errors)

    # 更新 rule_set.doc_types / check_categories，合并新发现的类型
    if imported:
        try:
            rs = db.execute(select(RuleSet).where(RuleSet.id == rule_set_id)).scalars().first()
            if rs:
                cur_docs = set(rs.doc_types or [])
                cur_cats = set(rs.check_categories or [])
                merged_docs = sorted(cur_docs | new_doc_types)
                merged_cats = sorted(cur_cats | new_check_categories)
                if merged_docs != rs.doc_types or merged_cats != rs.check_categories:
                    rs.doc_types = merged_docs
                    rs.check_categories = merged_cats
                    db.commit()
                    logger.info(
                        "已更新规则集 %s 类型: doc_types=%d, check_categories=%d",
                        rule_set_id, len(merged_docs), len(merged_cats)
                    )
        except Exception:
            logger.warning("更新规则集类型失败（不影响已入库规则）", exc_info=True)

    return {
        "total": len(raw_rules),
        "imported": len(imported),
        "skipped": len(errors),
        "rules": imported,
        "errors": errors,
    }
