"""规则语义冲突检测服务。

检测策略：
1. 按 (doc_type, check_category) 将规则分组
2. 同一组内两两比对规则文本的语义矛盾关系
3. 使用 LLM 批量检测冲突（一个 group 一次调用）
4. 返回结构化冲突报告
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm_client import LLMError, get_llm_client
from ..models import Rule
from .rule_import_task import ImportProgress, update_task

logger = logging.getLogger(__name__)

# LLM 冲突检测 prompt
_CONFLICT_SYSTEM_PROMPT = """你是一个单证审查规则一致性检测专家。任务是判断同一(文件类型, 检查项)组合下，多条规则之间是否存在语义矛盾。

输出 JSON 格式（严格 JSON，不要输出任何其他内容）：
{
  "conflicts": [
    {
      "rule_indices": [0, 2],
      "type": "logical_contradiction",
      "severity": "error",
      "description": "规则1说'应不大于'，规则3说'应不小于'，两者直接矛盾"
    }
  ]
}

判断标准：
1. logical_contradiction：两条规则直接矛盾（如 A≤B vs A≥B，必须 vs 无需，应 vs 不应）
2. boundary_overlap：两条规则边界冲突（如 A 容差 5% vs B 容差 10%，同时满足时结果不同）
3. redundant：两条规则含义重复（不影响结果，但建议合并）
4. consistent：不冲突（忽略，不输出）

severity：
- error：逻辑矛盾，必须处理
- warning：边界冲突或潜在歧义
- info：冗余建议

注意：
- 只检查同组规则的矛盾关系
- 没有冲突则输出 {"conflicts": []}
"""


def detect_conflicts_in_rules(rules: list[Rule]) -> list[dict]:
    """对一组规则进行语义冲突检测。

    Args:
        rules: 同一 (doc_type, check_category) 组的 Rule 对象列表

    Returns:
        冲突列表：[{"rule_ids": [uuid, uuid], "type": "...", "severity": "...", "description": "..."}]
    """
    if len(rules) < 2:
        return []

    # 准备规则文本列表
    rule_texts = []
    for r in rules:
        tol = r.tolerance or {}
        tol_str_parts = []
        if tol.get("amount_percent") is not None:
            tol_str_parts.append(f"金额容差{tol['amount_percent']}%")
        if tol.get("weight_kg") is not None:
            tol_str_parts.append(f"重量容差{tol['weight_kg']}kg")
        if tol.get("allow_same_day") is not None:
            tol_str_parts.append(f"允许同日={'是' if tol['allow_same_day'] else '否'}")
        if tol.get("time_days") is not None:
            tol_str_parts.append(f"时间容差{tol['time_days']}天")
        tol_str = "，".join(tol_str_parts)
        text = r.rule_text
        if tol_str:
            text += f"（{tol_str}）"
        rule_texts.append(text)

    # 构造 user prompt
    lines = "\n".join(f"{i}. \"{t}\"" for i, t in enumerate(rule_texts))
    # 批次 10：标签可为空，展示时回退
    doc_label = rules[0].doc_type or "整批/全部"
    cat_label = rules[0].check_category or "未分类"
    user_prompt = f"""文件类型：{doc_label}
检查项：{cat_label}

规则列表：
{lines}

请输出 JSON。"""

    llm = get_llm_client()
    try:
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": _CONFLICT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning("冲突检测 LLM 解析失败 [%s/%s]: %s", doc_label, cat_label, e)
        return []

    raw_conflicts = resp.get("conflicts", [])
    if not isinstance(raw_conflicts, list):
        return []

    results = []
    for c in raw_conflicts:
        if not isinstance(c, dict):
            continue
        indices = c.get("rule_indices", [])
        if not isinstance(indices, list) or len(indices) < 2:
            continue
        # 将 index 映射回 rule.id
        rule_ids = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(rules):
                rule_ids.append(str(rules[idx].id))
        if len(rule_ids) < 2:
            continue
        results.append({
            "rule_ids": rule_ids,
            "type": c.get("type", "logical_contradiction"),
            "severity": c.get("severity", "error"),
            "description": c.get("description", ""),
        })

    return results


def detect_all_conflicts(db: Session, rule_set_id: str, progress: ImportProgress | None = None) -> list[dict]:
    """检测指定规则集内所有规则的语义冲突。

    按 (doc_type, check_category) 分组后逐组检测。

    Returns:
        合并的冲突列表
    """
    rules = db.execute(
        select(Rule).where(
            Rule.rule_set_id == rule_set_id,
            Rule.enabled.is_(True),
        ).order_by(Rule.doc_type, Rule.check_category, Rule.priority)
    ).scalars().all()

    # 分组
    groups: dict[tuple[str, str], list[Rule]] = {}
    for r in rules:
        key = (r.doc_type, r.check_category)
        groups.setdefault(key, []).append(r)

    if progress is not None:
        update_task(progress, conflict_total=len(groups), conflict_done=0,
                    message=f"正在检测规则冲突（共 {len(groups)} 组）…")

    all_conflicts: list[dict] = []
    for _i, ((_doc_type, _check_category), group) in enumerate(groups.items(), start=1):
        conflicts = detect_conflicts_in_rules(group)
        all_conflicts.extend(conflicts)
        if conflicts:
            logger.info(
                "冲突检测 [%s/%s]: %d 条规则中发现 %d 个冲突",
                _doc_type, _check_category, len(group), len(conflicts)
            )
        if progress is not None:
            update_task(progress, conflict_done=_i,
                        message=f"冲突检测 {_i}/{len(groups)} 组")

    return all_conflicts


def apply_conflicts_as_defects(db: Session, rule_set_id: str, conflicts: list[dict]) -> int:
    """将检测到的冲突写入关联规则的 defects 字段。

    每条冲突记录会附加到涉及的所有规则的 defects 中。

    Returns:
        受影响的规则数
    """
    affected: set[str] = set()
    for c in conflicts:
        rule_ids = c.get("rule_ids", [])
        for rid in rule_ids:
            affected.add(rid)
            rule = db.get(Rule, rid)
            if rule is None:
                continue
            # 构建缺陷项
            defect = {
                "type": c.get("type", "logical_contradiction"),
                "severity": c.get("severity", "error"),
                "description": c.get("description", ""),
                "related_rule_ids": [r for r in rule_ids if r != rid],
            }
            # 去重：避免重复添加相同描述的同类型缺陷
            existing = rule.defects or []
            is_dup = any(
                d.get("type") == defect["type"]
                and d.get("description") == defect["description"]
                for d in existing
            )
            if not is_dup:
                existing.append(defect)
                rule.defects = existing

    if affected:
        db.commit()
        logger.info("已更新 %d 条规则的 defects（冲突信息）", len(affected))

    return len(affected)


def clear_old_conflicts(db: Session, rule_set_id: str, current_conflict_ids: set[str]) -> int:
    """清除不再有效的冲突缺陷（从规则的 defects 中移除）。"""
    rules = db.execute(
        select(Rule).where(
            Rule.rule_set_id == rule_set_id,
        )
    ).scalars().all()

    cleared = 0
    for rule in rules:
        defects = rule.defects or []
        new_defects = [
            d for d in defects
            if d.get("type") not in ("logical_contradiction", "boundary_overlap", "redundant")
            or d.get("description") in current_conflict_ids
        ]
        if len(new_defects) != len(defects):
            rule.defects = new_defects
            cleared += 1

    if cleared:
        db.commit()
    return cleared


def run_conflict_detection(db: Session, rule_set_id: str, progress: ImportProgress | None = None) -> dict[str, Any]:
    """运行完整的冲突检测流程：检测 → 写入 → 清理。

    Returns:
        {"total_conflicts": N, "affected_rules": N, "conflicts": [...]}
    """
    conflicts = detect_all_conflicts(db, rule_set_id, progress=progress)

    # 当前冲突的描述作为去重标识
    current_descriptions = {c.get("description", "") for c in conflicts if c.get("description")}

    # 清除旧冲突
    clear_old_conflicts(db, rule_set_id, current_descriptions)

    # 写入新冲突
    affected = apply_conflicts_as_defects(db, rule_set_id, conflicts)

    return {
        "total_conflicts": len(conflicts),
        "affected_rules": affected,
        "conflicts": conflicts,
    }
