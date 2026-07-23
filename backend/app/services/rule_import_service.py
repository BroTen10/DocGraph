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
from typing import Any

from sqlalchemy.orm import Session

from ..constants import ALL_DOC_TYPES, CHECK_CATEGORIES
from ..llm_client import LLMError, get_llm_client
from ..models import Rule
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
      "priority": 数字（越小越先，默认100）
    }
  ]
}

规则：
1. doc_type 必须从给定文件类型枚举中选择，不能编造
2. check_category 必须从给定检查项枚举中选择，不能编造
3. rule_text 用简洁中文描述，如"报关单数量应不大于委托单数量"
4. tolerance 只在规则涉及金额/重量/时间比对时填写，否则字段留 null
5. priority 默认 100；齐套性规则建议 10，基础判断建议 20，信息准确性建议 30，时间逻辑建议 40
6. 一条自然语言描述拆为一条规则；若用户文本含多条规则，全部解析
7. 忽略与单证审查无关的内容"""

_USER_PROMPT_TEMPLATE = """可用文件类型枚举：{doc_types}

可用检查项枚举：{check_categories}

请解析以下规则清单：

---
{raw_text}
---

请输出 JSON。"""


def import_rules_from_text(db: Session, raw_text: str) -> dict[str, Any]:
    """从自然语言规则清单文本批量导入规则。

    Args:
        db: 数据库会话
        raw_text: 自然语言规则清单文本

    Returns:
        {"total": 解析总数, "imported": 入库成功数, "skipped": 跳过数, "rules": [入库的规则], "errors": [跳过原因]}
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("规则清单文本为空")

    llm = get_llm_client()
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        doc_types="、".join(ALL_DOC_TYPES),
        check_categories="、".join(CHECK_CATEGORIES),
        raw_text=raw_text,
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
    except LLMError as e:
        logger.error("规则批量导入 LLM 解析失败: %s", e)
        raise ValueError(f"LLM 解析失败: {e}") from e

    raw_rules = resp.get("rules", [])
    if not isinstance(raw_rules, list) or len(raw_rules) == 0:
        raise ValueError("LLM 未解析出任何规则")

    doc_type_set = set(ALL_DOC_TYPES)
    category_set = set(CHECK_CATEGORIES)

    imported: list[dict] = []
    errors: list[str] = []
    valid_doc_types = ALL_DOC_TYPES
    valid_categories = CHECK_CATEGORIES

    for i, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 条：非合法对象，跳过")
            continue
        doc_type = str(item.get("doc_type", "")).strip()
        check_category = str(item.get("check_category", "")).strip()
        rule_text = str(item.get("rule_text", "")).strip()
        if not doc_type or doc_type not in doc_type_set:
            errors.append(f"第 {i} 条：文件类型 [{doc_type}] 不在合法枚举内，跳过")
            continue
        if not check_category or check_category not in category_set:
            errors.append(f"第 {i} 条：检查项 [{check_category}] 不在合法枚举内，跳过")
            continue
        if not rule_text:
            errors.append(f"第 {i} 条：规则文本为空，跳过")
            continue

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

        try:
            payload = RuleCreate(
                doc_type=doc_type,
                check_category=check_category,
                rule_text=rule_text,
                tolerance=tolerance,
                enabled=True,
                priority=priority,
            )
            rule_out = create_rule(db, payload)
            imported.append(rule_out.model_dump(mode="json"))
        except Exception as e:
            errors.append(f"第 {i} 条：入库失败 - {e}")

    return {
        "total": len(raw_rules),
        "imported": len(imported),
        "skipped": len(errors),
        "rules": imported,
        "errors": errors,
    }
