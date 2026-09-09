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
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm_client import LLMError, get_llm_client
from ..models import Rule
from .settings_service import get_prompt
from .rule_import_task import ImportProgress, update_task
from .rule_service import format_rule_code

logger = logging.getLogger(__name__)

# LLM 冲突检测 prompt
_CONFLICT_SYSTEM_PROMPT = """你是一个单证审查规则一致性检测专家。任务是判断同一(文件类型, 检查项)组合下，多条规则之间是否存在语义矛盾。

输出 JSON 格式（严格 JSON，不要输出任何其他内容）：
{
  "conflicts": [
    {
      "rule_codes": ["R0001", "R0003"],
      "type": "logical_contradiction",
      "severity": "error",
      "description": "规则 R0001 说'应不大于'，规则 R0003 说'应不小于'，两者直接矛盾"
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
- 规则必须用输入的规则流水号（R0001 形式）指代，禁止使用"规则0/规则1"等数组下标
"""

_RULE_NUMBER_LIST_RE = re.compile(r"规则\s*(\d+(?:\s*[、,，和及]\s*\d+)*)")
_NUMBER_RE = re.compile(r"\d+")


def _normalize_rule_code(value: Any) -> str | None:
    """把 LLM 返回的 R1/1/规则R0001 归一为 R0001。"""
    text = str(value or "").strip().upper()
    if not text:
        return None
    match = re.search(r"R\s*(\d+)", text)
    if match:
        return format_rule_code(int(match.group(1)))
    if text.isdigit():
        return format_rule_code(int(text))
    return text


def _canonicalize_conflict_description(
    description: str,
    rules: list[Rule],
    referenced_indices: list[int],
    rule_codes: list[str],
) -> str:
    """把描述中的数组下标替换为稳定的规则流水号。"""
    text = str(description or "").strip()
    codes = list(dict.fromkeys(rule_codes))
    if not text:
        return f"涉及规则 {'、'.join(codes)}" if codes else ""

    matches = list(_RULE_NUMBER_LIST_RE.finditer(text))
    numbers = [
        int(number)
        for match in matches
        for number in _NUMBER_RE.findall(match.group(1))
    ]
    if numbers:
        ref_set = set(referenced_indices)

        def _score(offset: int) -> tuple[int, int, int]:
            mapped = [
                number - offset
                for number in numbers
                if 0 <= number - offset < len(rules)
            ]
            valid = int(len(mapped) == len(numbers))
            exact = int(bool(mapped) and set(mapped) == ref_set)
            hit = len(set(mapped) & ref_set)
            return valid, exact, hit

        # 兼容两种历史输出：0 基数组下标，以及人类习惯的 1 基序号。
        offset = max((0, 1), key=_score)

        def _replace(match: re.Match[str]) -> str:
            mapped_codes: list[str] = []
            for raw_number in _NUMBER_RE.findall(match.group(1)):
                index = int(raw_number) - offset
                if 0 <= index < len(rules):
                    mapped_codes.append(rules[index].rule_code)
            return f"规则 {'、'.join(mapped_codes)}" if mapped_codes else match.group(0)

        text = _RULE_NUMBER_LIST_RE.sub(_replace, text)
        text = re.sub(r"[（(]\s*索引\s*\d+\s*[)）]", "", text)

    if codes and not any(code in text for code in codes):
        text = f"涉及规则 {'、'.join(codes)}：{text}"
    return text


def detect_conflicts_in_rules(rules: list[Rule], db=None) -> list[dict]:
    """对一组规则进行语义冲突检测。

    Args:
        rules: 同一 (doc_type, check_category) 组的 Rule 对象列表

    Returns:
        冲突列表：[{"rule_ids": [uuid, uuid], "rule_codes": ["R0001", "R0002"],
                   "type": "...", "severity": "...", "description": "..."}]
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
    rule_codes = [r.rule_code for r in rules]
    lines = "\n".join(
        f"{i}. {code} \"{t}\""
        for i, (code, t) in enumerate(zip(rule_codes, rule_texts))
    )
    # 批次 10：标签可为空，展示时回退
    doc_label = rules[0].doc_type or "整批/全部"
    cat_label = rules[0].check_category or "未分类"
    user_prompt = f"""文件类型：{doc_label}
检查项：{cat_label}

规则列表：
{lines}

规则流水号与数组索引对照：
{chr(10).join(f"{i}: {code}" for i, code in enumerate(rule_codes))}

输出要求：
- 优先使用 "rule_codes": ["R0001", "R0003"] 指代冲突规则；
- description 只能写规则流水号（如 R0001），禁止写"规则0/规则1"等数组下标。

请输出 JSON。"""

    llm = get_llm_client()
    try:
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": get_prompt(db, "conflict_detection.system")},
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
        code_to_index = {rule.rule_code: i for i, rule in enumerate(rules)}
        indices: list[int] = []
        raw_codes = c.get("rule_codes", [])
        if isinstance(raw_codes, list):
            for raw_code in raw_codes:
                code = _normalize_rule_code(raw_code)
                if code in code_to_index:
                    indices.append(code_to_index[code])
        if len(indices) < 2:
            raw_indices = c.get("rule_indices", [])
            if isinstance(raw_indices, list):
                for raw_index in raw_indices:
                    try:
                        idx = int(raw_index)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= idx < len(rules):
                        indices.append(idx)
        indices = list(dict.fromkeys(indices))
        if len(indices) < 2:
            continue

        # 将 index 映射回 rule.id
        rule_ids = [str(rules[idx].id) for idx in indices]
        canonical_codes = [rules[idx].rule_code for idx in indices]
        if len(rule_ids) < 2:
            continue
        description = _canonicalize_conflict_description(
            str(c.get("description") or ""),
            rules,
            indices,
            canonical_codes,
        )
        results.append({
            "rule_ids": rule_ids,
            "rule_codes": canonical_codes,
            "type": c.get("type", "logical_contradiction"),
            "severity": c.get("severity", "error"),
            "description": description,
        })

    return results


def detect_all_conflicts(db: Session, rule_set_id: str, progress: ImportProgress | None = None) -> list[dict]:
    """检测指定规则集内所有规则的语义冲突。

    按 (doc_type, check_category) 分组后逐组检测。
    包含已禁用规则，避免冲突规则被默认禁用后无法再次检测和清除冲突。

    Returns:
        合并的冲突列表
    """
    rules = db.execute(
        select(Rule).where(
            Rule.rule_set_id == rule_set_id,
        ).order_by(Rule.doc_type, Rule.check_category, Rule.priority, Rule.rule_no)
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
        conflicts = detect_conflicts_in_rules(group, db)
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
    语义冲突规则统一回落到“待确认 + 禁用”，与页面提示保持一致；
    重复检测也执行该状态同步，确保存量已带冲突缺陷的规则被纠正。

    Returns:
        受影响的规则数
    """
    affected: set[str] = set()
    for c in conflicts:
        rule_ids = c.get("rule_ids", [])
        conflict_codes = list(c.get("rule_codes") or [])
        if not conflict_codes:
            conflict_codes = [
                rule.rule_code
                for rid in rule_ids
                for rule in (db.get(Rule, rid),)
                if rule is not None
            ]
        for rid in rule_ids:
            affected.add(rid)
            rule = db.get(Rule, rid)
            if rule is None:
                continue
            related_ids = [r for r in rule_ids if r != rid]
            related_codes = [
                code
                for rid_, code in zip(rule_ids, conflict_codes)
                if rid_ != rid
            ]
            # 构建缺陷项
            defect = {
                "type": c.get("type", "logical_contradiction"),
                "severity": c.get("severity", "error"),
                "description": c.get("description", ""),
                "rule_code": rule.rule_code,
                "related_rule_ids": related_ids,
                "related_rule_codes": related_codes,
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

            # 语义冲突属于需要人工确认的问题，统一回到安全默认状态。
            if (
                rule.status != "pending"
                or rule.enabled
                or rule.confirmed_at is not None
                or rule.confirmed_by is not None
            ):
                rule.status = "pending"
                rule.enabled = False
                rule.confirmed_at = None
                rule.confirmed_by = None

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
            # 冲突已消除且无其他实质缺陷时，恢复健康规则默认状态。
            has_real_defect = any(
                d.get("severity") in ("error", "warning")
                for d in new_defects
            )
            if not has_real_defect and rule.status != "confirmed":
                rule.status = "confirmed"
                rule.enabled = True

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
