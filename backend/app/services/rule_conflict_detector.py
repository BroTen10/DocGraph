"""规则语义冲突检测服务。

检测策略：
1. 按**适用范围（业务对象）**分组：规则在其涉及的每个对象分组内参与两两比对；
   未限定适用范围的规则单独成组（不再把 doc_type/check_category 都为空的所有规则塞进同一组）；
2. 单组规则数超过上限时截断并给出告警，避免提示词与输出被撑爆；
3. 用 LLM 批量检测冲突（一个 group 一次调用），并把适用范围/条件/断言/例外一并送进提示词；
4. 返回结构化冲突报告，并显式区分「检测完成」与「检测失败」——
   任何一组检测失败都不会清除历史冲突、不会改动规则状态。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm_client import LLMError, get_llm_client
from ..models import Rule
from ..tenant_scope import tenant_get
from . import rule_defects
from .settings_service import get_prompt
from .rule_import_task import ImportProgress, update_task
from .rule_service import format_rule_code, get_rule_set

logger = logging.getLogger(__name__)

_RULE_NUMBER_LIST_RE = re.compile(r"规则\s*(\d+(?:\s*[、,，和及]\s*\d+)*)")
_NUMBER_RE = re.compile(r"\d+")

# 「整批/全部」语义的 scope 取值：表示可能命中任意业务对象
_ALL_SCOPE_SENTINELS = {"all", "整批", "全部", "全部文件", "整批合同"}
# 单组参与检测的规则上限（超出则按优先级截断并告警，避免输出被截断/成本失控）
_MAX_GROUP_RULES = 60


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


@dataclass
class GroupOutcome:
    """单个适用范围分组的检测结果。"""

    key: str
    rule_count: int
    conflicts: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class DetectionResult:
    """一次完整冲突检测的结果（含完成度信息）。"""

    conflicts: list[dict] = field(default_factory=list)
    groups: list[GroupOutcome] = field(default_factory=list)
    scanned_rule_ids: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_groups(self) -> int:
        return len(self.groups)

    @property
    def failed_groups(self) -> int:
        return sum(1 for group in self.groups if not group.ok)

    @property
    def complete(self) -> bool:
        return self.failed_groups == 0


def _rule_doc_types(rule: Rule) -> tuple[str, ...]:
    """规则限定的业务对象集合（小写去重）；空元组表示未限定/整批。"""
    scope = rule.scope if isinstance(rule.scope, dict) else None
    raw = (scope or {}).get("doc_types")
    names: list[str] = []
    if isinstance(raw, str):
        if raw.strip().lower() in _ALL_SCOPE_SENTINELS:
            return ()
        names = [raw]
    elif isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if not text:
                continue
            if text.lower() in _ALL_SCOPE_SENTINELS:
                return ()
            names.append(text)
    if not names and rule.doc_type:
        names = [str(rule.doc_type)]
    return tuple(dict.fromkeys(name.strip().lower() for name in names if name.strip()))


def _group_rules(
    rules: list[Rule],
) -> tuple[list[tuple[str, list[Rule]]], list[str], set[str]]:
    """按适用范围把规则分组：每条规则进入其涉及的每个业务对象分组。

    未限定适用范围的规则单独成组（可能与任意对象冲突，但避免与全部规则两两比对）。
    单成员分组也会返回（无需调用 LLM，但视为已完整检测，用于回收历史冲突）。

    Returns:
        (分组列表, 告警, 因超过单组上限而未参与检测的规则 id 集合)
    """
    buckets: dict[str, list[Rule]] = {}
    unbound: list[Rule] = []
    for rule in rules:
        doc_types = _rule_doc_types(rule)
        if not doc_types:
            unbound.append(rule)
            continue
        for doc_type in doc_types:
            buckets.setdefault(doc_type, []).append(rule)

    groups: list[tuple[str, list[Rule]]] = [
        (f"适用范围：{key}", buckets[key]) for key in sorted(buckets)
    ]
    if unbound:
        groups.append(("适用范围：未限定（整批/全部）", unbound))

    warnings: list[str] = []
    truncated_ids: set[str] = set()
    prepared: list[tuple[str, list[Rule]]] = []
    for key, group in groups:
        if len(group) > _MAX_GROUP_RULES:
            ordered = sorted(group, key=lambda r: (r.priority or 100, r.rule_no or 0))
            truncated_ids |= {str(r.id) for r in ordered[_MAX_GROUP_RULES:]}
            warnings.append(
                f"{key} 共 {len(group)} 条规则，超过单组上限 {_MAX_GROUP_RULES} 条，"
                f"本次仅检测前 {_MAX_GROUP_RULES} 条；其余规则的既有冲突保持不变"
            )
            group = ordered[:_MAX_GROUP_RULES]
        prepared.append((key, group))
    return prepared, warnings, truncated_ids


def _describe_condition(condition: Any) -> str:
    if not isinstance(condition, dict):
        return ""
    text = str(condition.get("text") or "").strip()
    parts = [
        str(condition.get("field") or "").strip(),
        str(condition.get("operator") or "").strip(),
        str(condition.get("value") if condition.get("value") is not None else "").strip(),
    ]
    expr = " ".join(p for p in parts if p)
    if text and expr:
        return f"{text}（{expr}）"
    return text or expr


def _describe_assertion(assertion: Any) -> str:
    if not isinstance(assertion, dict):
        return ""
    src = assertion.get("source") or {}
    tgt = assertion.get("target") or {}

    def side(value: dict) -> str:
        doc = str(value.get("doc_type") or "").strip()
        fld = str(value.get("field") or "").strip()
        name = ".".join(x for x in (doc, fld) if x)
        aggregate = str(value.get("aggregate") or "").strip()
        return f"{name}[{aggregate}]" if aggregate and name else (name or aggregate)

    left, right, op = side(src), side(tgt), str(assertion.get("operator") or "").strip()
    extras = [
        str(assertion.get("currency") or "").strip(),
        str(assertion.get("unit") or "").strip(),
    ]
    expr = " ".join(x for x in (left, op, right) if x)
    extras_text = "/".join(x for x in extras if x)
    return f"{expr}（{extras_text}）" if expr and extras_text else expr


def _describe_tolerance(rule: Rule) -> str:
    tol = rule.tolerance or {}
    parts = []
    if tol.get("amount_percent") is not None:
        parts.append(f"金额容差{tol['amount_percent']}%")
    if tol.get("quantity") is not None:
        parts.append(f"数量容差{tol['quantity']}")
    if tol.get("weight_kg") is not None:
        parts.append(f"重量容差{tol['weight_kg']}kg")
    if tol.get("time_days") is not None:
        parts.append(f"时间容差{tol['time_days']}天")
    if tol.get("allow_same_day") is not None:
        parts.append(f"允许同日={'是' if tol['allow_same_day'] else '否'}")
    return "，".join(parts)


def _describe_exceptions(rule: Rule) -> str:
    structure = rule.structure if isinstance(rule.structure, dict) else {}
    exceptions = structure.get("exceptions") or []
    texts = [
        str(item.get("text") or "").strip()
        for item in exceptions
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    return "；".join(texts)


def render_rule_line(index: int, rule: Rule) -> str:
    """把一条规则渲染成冲突检测提示词中的一行（含范围/条件/断言/容差/例外）。"""
    doc_types = _rule_doc_types(rule)
    structure = rule.structure if isinstance(rule.structure, dict) else {}
    segments = [
        f'{index}. {rule.rule_code} "{rule.rule_text}"',
        "适用=" + ("、".join(doc_types) if doc_types else "未限定"),
    ]
    condition = _describe_condition(structure.get("condition"))
    if condition:
        segments.append(f"条件={condition}")
    assertion = _describe_assertion(structure.get("assertion"))
    if assertion:
        segments.append(f"断言={assertion}")
    tolerance = _describe_tolerance(rule)
    if tolerance:
        segments.append(f"容差={tolerance}")
    exceptions = _describe_exceptions(rule)
    if exceptions:
        segments.append(f"例外={exceptions}")
    return " | ".join(segments)


def detect_conflicts_in_rules(
    rules: list[Rule],
    tenant_id: uuid.UUID,
    db=None,
    group_label: str = "",
) -> list[dict]:
    """对一组规则进行语义冲突检测（失败时返回空列表）。

    需要区分「无冲突」与「检测失败」时请使用 :func:`detect_group`。
    """
    conflicts, _error = detect_group(rules, tenant_id, db, group_label)
    return conflicts


def detect_group(
    rules: list[Rule],
    tenant_id: uuid.UUID,
    db=None,
    group_label: str = "",
) -> tuple[list[dict], str | None]:
    """对一组规则调用 LLM 检测冲突。

    Returns:
        (冲突列表, 错误信息)。错误信息不为 None 表示本组检测**未完成**，
        调用方不得据此清除历史冲突。
    """
    if len(rules) < 2:
        return [], None

    rule_codes = [r.rule_code for r in rules]
    lines = "\n".join(render_rule_line(i, rule) for i, rule in enumerate(rules))
    user_prompt = f"""适用范围分组：{group_label or '未标注'}
规则列表（每条含适用范围/条件/断言/容差/例外）：
{lines}

规则流水号与数组索引对照：
{chr(10).join(f"{i}: {code}" for i, code in enumerate(rule_codes))}

判定口径：
- 只判定「适用范围可能同时命中」且「条件可以同时成立」的规则之间的冲突；
- 适用范围不相交、或条件互斥（不可能同时成立）的规则不算冲突；
- 条件不同但可以同时成立的，才比较断言是否矛盾；断言主体（对象/字段）不同则不算冲突；
- 没有足够信息判断为矛盾时，不要输出该条。

输出要求：
- 优先使用 "rule_codes": ["R0001", "R0003"] 指代冲突规则；
- description 只能写规则流水号（如 R0001），禁止写"规则0/规则1"等数组下标。

请输出 JSON。"""

    llm = get_llm_client()
    try:
        resp = llm.chat_json(
            messages=[
                {
                    "role": "system",
                    "content": get_prompt(
                        db,
                        tenant_id,
                        "conflict_detection.system",
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
            disable_thinking=True,
        )
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning(
            "冲突检测 LLM 调用失败 [%s]（%d 条规则）: %s",
            group_label or "未标注",
            len(rules),
            e,
        )
        return [], f"{type(e).__name__}: {e}"

    raw_conflicts = resp.get("conflicts", [])
    if not isinstance(raw_conflicts, list):
        logger.warning("冲突检测返回结构异常 [%s]: %r", group_label, type(raw_conflicts))
        return [], "LLM 返回结构异常：conflicts 不是数组"

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

    return results, None


def _dedupe_conflicts(conflicts: list[dict]) -> list[dict]:
    """同一对规则可能因多对象范围在多个分组中被检出，按 (规则对, 类型) 去重。"""
    seen: set[tuple[frozenset[str], str]] = set()
    unique: list[dict] = []
    for conflict in conflicts:
        key = (
            frozenset(str(rid) for rid in conflict.get("rule_ids") or []),
            str(conflict.get("type") or ""),
        )
        if len(key[0]) < 2 or key in seen:
            continue
        seen.add(key)
        unique.append(conflict)
    return unique


def detect_all_conflicts(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: str,
    progress: ImportProgress | None = None,
) -> DetectionResult:
    """检测指定规则集内所有规则的语义冲突。

    按适用范围（业务对象）分组后逐组检测；一条规则涉及的多个对象各成一组。
    包含已禁用规则，避免冲突规则被默认禁用后无法再次检测和清除冲突。

    Returns:
        DetectionResult（含完成度：failed_groups 表示未完成的分组数）
    """
    if get_rule_set(db, tenant_id, uuid.UUID(str(rule_set_id))) is None:
        raise ValueError("规则集不存在")
    rules = db.execute(
        select(Rule)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.rule_set_id == rule_set_id,
        )
        .order_by(Rule.priority, Rule.rule_no)
    ).scalars().all()

    groups, warnings, truncated_ids = _group_rules(list(rules))

    if progress is not None:
        update_task(progress, conflict_total=len(groups), conflict_done=0,
                    message=f"正在检测规则冲突（共 {len(groups)} 组）…")

    result = DetectionResult(groups=[], warnings=list(warnings))
    collected: list[dict] = []
    for index, (label, group) in enumerate(groups, start=1):
        if len(group) < 2:
            # 单成员分组无需比对，视为已完整检测（可回收历史冲突）
            result.groups.append(GroupOutcome(key=label, rule_count=len(group)))
        else:
            conflicts, error = detect_group(group, tenant_id, db, label)
            result.groups.append(GroupOutcome(
                key=label,
                rule_count=len(group),
                conflicts=conflicts,
                error=error,
            ))
            if error is None:
                collected.extend(conflicts)
            else:
                logger.warning("冲突检测未完成 [%s]: %s", label, error)
        if progress is not None:
            update_task(progress, conflict_done=index,
                        message=f"冲突检测 {index}/{len(groups)} 组")

    result.conflicts = _dedupe_conflicts(collected)

    # 只有完整检测过的规则才允许参与"冲突已消除"的状态回收：
    # 失败分组中的规则、以及因超限被截断的规则一律排除，避免误清。
    failed_rule_ids: set[str] = {
        str(rule.id)
        for (label, group), outcome in zip(groups, result.groups)
        if not outcome.ok
        for rule in group
    }
    result.scanned_rule_ids = (
        {str(rule.id) for rule in rules} - failed_rule_ids - truncated_ids
    )

    if not result.complete:
        result.warnings.append(
            f"冲突检测有 {result.failed_groups}/{result.total_groups} 组未完成，"
            "这些分组的既有冲突与规则状态保持不变"
        )
    return result


def apply_conflicts_as_defects(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: str,
    conflicts: list[dict],
) -> int:
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
                for rule in (tenant_get(db, Rule, uuid.UUID(str(rid)), tenant_id),)
                if rule is not None
            ]
        for rid in rule_ids:
            affected.add(rid)
            rule = tenant_get(db, Rule, uuid.UUID(str(rid)), tenant_id)
            if rule is None:
                continue
            if str(rule.rule_set_id) != str(rule_set_id):
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
            else:
                # 同一冲突再次出现：即使此前被用户标记为"已处理"，也要重新打开，
                # 否则已处理的旧缺陷会掩盖新检测出的冲突。
                reopened, count = rule_defects.reopen_defects(
                    existing, types=(defect["type"],)
                )
                if count:
                    rule.defects = reopened

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


def clear_old_conflicts(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: str,
    current_conflict_ids: set[str],
    scanned_rule_ids: set[str] | None = None,
) -> int:
    """清除不再有效的冲突缺陷（从规则的 defects 中移除）。

    Args:
        current_conflict_ids: 本轮仍然成立的冲突描述集合。
        scanned_rule_ids: 本轮**完整检测过**的规则 id 集合；
            None 表示全部规则都检测过；传入集合时，未包含的规则一律不动
            （检测失败/被截断的分组保留既有冲突与状态）。
    """
    rules = db.execute(
        select(Rule).where(
            Rule.tenant_id == tenant_id,
            Rule.rule_set_id == rule_set_id,
        )
    ).scalars().all()

    cleared = 0
    for rule in rules:
        if scanned_rule_ids is not None and str(rule.id) not in scanned_rule_ids:
            continue
        defects = rule.defects or []
        new_defects = [
            d for d in defects
            if not rule_defects.is_conflict_defect(d)
            or d.get("description") in current_conflict_ids
        ]
        if len(new_defects) != len(defects):
            rule.defects = new_defects
            cleared += 1
            # 冲突已消除且无其他未处理实质缺陷时，恢复健康规则默认状态：
            # 自动确认的同时把残留（提示类）缺陷一并标记为已处理，保证
            # "已确认 ⇒ 无未处理缺陷"，规则进入「正常规则」。
            if not rule_defects.has_open_blocking_defect(new_defects) and rule.status != "confirmed":
                rule.status = "confirmed"
                rule.enabled = True
                resolved, count = rule_defects.resolve_defects(
                    new_defects,
                    by=rule_defects.RESOLVED_BY_CONFLICT_CLEARED,
                    note=rule_defects.CONFLICT_CLEARED_NOTE,
                )
                if count:
                    rule.defects = resolved

    if cleared:
        db.commit()
    return cleared


def run_conflict_detection(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: str,
    progress: ImportProgress | None = None,
) -> dict[str, Any]:
    """运行完整的冲突检测流程：检测 → 写入 → 清理。

    完成度语义：只要有一组检测失败，就**不会**把"没检出冲突"当成"冲突已消除"，
    未完整检测的规则保留既有冲突与状态（避免 LLM 故障时误启用规则）。

    Returns:
        {"total_conflicts", "affected_rules", "conflicts",
         "total_groups", "failed_groups", "detection_incomplete",
         "cleared_rules", "warnings"}
    """
    if get_rule_set(db, tenant_id, uuid.UUID(str(rule_set_id))) is None:
        raise ValueError("规则集不存在")
    detection = detect_all_conflicts(
        db,
        tenant_id,
        rule_set_id,
        progress=progress,
    )
    conflicts = detection.conflicts

    # 当前冲突的描述作为去重标识
    current_descriptions = {c.get("description", "") for c in conflicts if c.get("description")}

    # 清除旧冲突：仅限本轮完整检测过的规则
    cleared = clear_old_conflicts(
        db,
        tenant_id,
        rule_set_id,
        current_descriptions,
        scanned_rule_ids=detection.scanned_rule_ids,
    )

    # 写入新冲突
    affected = apply_conflicts_as_defects(db, tenant_id, rule_set_id, conflicts)

    return {
        "total_conflicts": len(conflicts),
        "affected_rules": affected,
        "conflicts": conflicts,
        "total_groups": detection.total_groups,
        "failed_groups": detection.failed_groups,
        "detection_incomplete": not detection.complete,
        "cleared_rules": cleared,
        "warnings": detection.warnings,
    }
