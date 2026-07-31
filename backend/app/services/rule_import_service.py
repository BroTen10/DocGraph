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
from ..schemas.rule import RuleCreate, ConflictReport
from .rule_parse_engine import (
    RuleParseDirective,
    apply_defaults,
    apply_field_mappings,
    apply_term_normalization,
    apply_text_preprocessing,
)
from .rule_import_task import ImportProgress, update_task

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
  ],
  "defects": [
    {
      "type": "缺陷类型",
      "severity": "error|warning|info",
      "description": "问题描述",
      "rule_index": 该缺陷对应的规则在 rules 数组中的索引（若无对应规则则填null）
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
8. confidence 反映你对这条规则确信程度：规则描述非常清楚、无歧义则接近 1.0；含模糊表述（如"部分情况""一般""可能"）则适当降低；完全不确定或不合理则为 0.0
9. 为减少输出体积，值为 null 的字段省略不输出（客户端自动补全），confidence 字段值为 1.0 时同理省略

### 缺陷检测指令
对每条被解析的规则，执行以下检查，将结果填入 `defects` 数组：

1. `ambiguous_reference`：规则中是否包含"相关""相应""有关""等"等模糊引用，导致执行者无法确定具体指代？
2. `incomplete_condition`：规则的条件是否不完整？例如提到金额比对但没有说和什么比、缺少比较对象？
3. `missing_value`：规则涉及金额/重量/时间的比对，但原文没有给出任何容差或阈值？
4. `contradiction`：这条规则是否和同一批次中已解析的其他规则存在明显的语义矛盾？
5. `uncertainty`：你对这条规则的理解是否有任何不确定的地方？比如术语含义模糊、多种可能的解读？

缺陷类型说明：
- ambiguous_reference：涉及模糊引用，让用户确认具体指代
- incomplete_condition：条件不完整，需要用户补充信息
- missing_value：缺少关键数值参数
- contradiction：规则间存在矛盾
- uncertainty：存在理解上的不确定

severity 说明：
- error：大概率有问题的规则，需要用户处理
- warning：可能存在问题的规则，建议用户检查
- info：仅供参考，不影响规则执行"""

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


def _normalize_text(text: str) -> str:
    """归一化规则文本：去标点、去空格、小写，用于相似度比对。"""
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _text_similarity(a: str, b: str) -> float:
    """计算两个归一化文本的相似度（0-1）。使用字符级 Jaccard + 包含关系。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _find_similar_rule(
    new_text: str,
    new_normed: str,
    existing_rules: list,
    threshold: float = 0.75,
):
    """在已有规则列表中查找与新规则高度相似的规则。返回第一个相似度 >= threshold 的规则或 None。"""
    for rule in existing_rules:
        existing_normed = _normalize_text(rule.rule_text)
        sim = _text_similarity(new_normed, existing_normed)
        if sim >= threshold:
            return rule
    return None


def _merge_into_existing(
    db,
    existing_rule,
    new_rule_text: str,
    new_confidence: float | None,
    new_tolerance: dict,
    new_priority: int,
    new_defects: list[dict],
) -> int:
    """将新规则合并到已有规则中。返回合并的字段数。

    合并策略：
    - confidence: 取较高值
    - tolerance: 合并（新值覆盖旧值为空的字段）
    - defects: 去重合并（按 type + description 去重）
    - rule_text: 保留更长的（通常更完整）
    - priority: 保留较小的（更优先）
    """
    changes = 0

    # confidence: 取较高值
    if new_confidence is not None:
        old_conf = existing_rule.confidence
        if old_conf is None or new_confidence > old_conf:
            existing_rule.confidence = new_confidence
            changes += 1

    # tolerance: 合并
    old_tol = existing_rule.tolerance or {}
    for k, v in (new_tolerance or {}).items():
        if v is not None and old_tol.get(k) is None:
            old_tol[k] = v
            changes += 1
    if changes > 0:
        existing_rule.tolerance = dict(old_tol)

    # priority: 取较小值
    if new_priority < (existing_rule.priority or 100):
        existing_rule.priority = new_priority
        changes += 1

    # rule_text: 保留更长的
    if len(new_rule_text) > len(existing_rule.rule_text):
        existing_rule.rule_text = new_rule_text
        changes += 1

    # defects: 去重合并
    old_defects = existing_rule.defects or []
    old_keys = {(d.get("type"), d.get("description")) for d in old_defects}
    for d in (new_defects or []):
        key = (d.get("type"), d.get("description"))
        if key not in old_keys:
            old_defects.append(d)
            old_keys.add(key)
            changes += 1
    if changes > 0:
        existing_rule.defects = old_defects
        # 重新评估规则健康状态：合并后如果仍有缺陷，保持 pending/disabled
        has_defects = any(
            d.get("severity") in ("error", "warning")
            for d in old_defects
        )
        if not has_defects and existing_rule.status != "confirmed":
            existing_rule.status = "confirmed"
            existing_rule.enabled = True
            changes += 1

    db.commit()
    return changes


def import_rules_from_text(
    db: Session, rule_set_id: uuid.UUID, raw_text: str,
    directive: RuleParseDirective | None = None,
    progress: ImportProgress | None = None,
) -> dict[str, Any]:
    """从自然语言规则清单文本批量导入规则。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（导入规则归到该规则集下）
        raw_text: 自然语言规则清单文本
        directive: Skill 编译后的解析指令（可选），指定后应用预处理/字段映射/默认值等

    Returns:
        {"total": 解析总数, "imported": 入库成功数, "skipped": 跳过数, "rules": [入库的规则],
         "errors": [跳过原因], "conflict_report": 冲突与缺陷报告}
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("规则清单文本为空")

    # 应用文本预处理（如果指定了 directive）
    if directive:
        raw_text = apply_text_preprocessing(raw_text, directive.text_preprocessing)

    llm = get_llm_client()
    doc_types_str = "、".join(ALL_DOC_TYPES)
    check_categories_str = "、".join(CHECK_CATEGORIES)

    # 长文本分段解析：避免单次输出超 max_tokens 被截断（JSON 不完整）
    chunks = _split_text(raw_text)
    logger.info("规则导入分段: 共 %d 段, 各段长度=%s", len(chunks), [len(c) for c in chunks])
    if progress is not None:
        update_task(progress, status="parsing", total_chunks=len(chunks), parsed_chunks=0,
                    message=f"正在解析规则（共 {len(chunks)} 段）…")
    raw_rules: list[dict] = []
    all_defects: list[dict] = []  # 收集所有段的 defects
    chunk_errors: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            doc_types=doc_types_str,
            check_categories=check_categories_str,
            raw_text=chunk,
        )
        # 构建动态 System Prompt（附加 Skill 指令 + 领域上下文）
        system_content = _SYSTEM_PROMPT
        if directive:
            if directive.prompt_additions:
                system_content += "\n\n### 用户自定义解析指令\n" + "\n".join(f"- {a}" for a in directive.prompt_additions)
            ctx = directive.domain_context
            if ctx:
                glossary = ctx.get("glossary") or {}
                patterns = ctx.get("common_patterns") or []
                parts = []
                if glossary:
                    parts.append("### 领域术语定义\n" + "\n".join(f"- {k}: {v}" for k, v in glossary.items()))
                if patterns:
                    parts.append("### 常见规则模式\n" + "\n".join(f"- {p}" for p in patterns))
                if parts:
                    system_content += "\n\n" + "\n\n".join(parts)

        try:
            resp = llm.chat_json(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=8192,
            )
        except (LLMError, ValueError) as e:
            logger.error("规则导入 第 %d/%d 段 LLM 解析失败: %s", idx, len(chunks), e)
            chunk_errors.append(f"第 {idx} 段解析失败: {e}")
            if progress is not None:
                update_task(progress, parsed_chunks=idx,
                            message=f"第 {idx}/{len(chunks)} 段解析失败，已跳过")
            continue

        if progress is not None:
            update_task(progress, parsed_chunks=idx,
                        message=f"已解析 {idx}/{len(chunks)} 段")

        # 提取 rules
        rules = resp.get("rules", [])
        if isinstance(rules, list):
            offset = len(raw_rules)
            raw_rules.extend(r for r in rules if isinstance(r, dict))
            # 提取 defects，修正 rule_index 为全局索引
            defects = resp.get("defects", [])
            if isinstance(defects, list):
                for d in defects:
                    if isinstance(d, dict):
                        ri = d.get("rule_index")
                        if ri is not None:
                            d = dict(d)
                            d["rule_index"] = ri + offset
                        all_defects.append(d)
        else:
            chunk_errors.append(f"第 {idx} 段：LLM 返回结构异常，已跳过")

    if not raw_rules:
        detail = f"（共 {len(chunks)} 段，{len(chunk_errors)} 段失败）" if chunk_errors else ""
        raise ValueError(f"LLM 未解析出任何规则{detail}")

    if progress is not None:
        update_task(progress, status="importing", total_rules=len(raw_rules),
                    imported_rules=0, import_errors=0,
                    message=f"正在入库 {len(raw_rules)} 条规则…")

    # 应用 Skill 后处理：字段映射、默认值、术语归一化
    if directive:
        raw_rules = apply_field_mappings(raw_rules, directive.field_mappings)
        raw_rules = apply_defaults(raw_rules, directive.defaults)
        raw_rules = apply_term_normalization(raw_rules, directive.term_normalization)

    # 按全局 rule_index 建立 defects 索引
    defects_by_rule: dict[int, list[dict]] = {}
    for d in all_defects:
        ri = d.get("rule_index")
        if ri is not None and isinstance(ri, int):
            defects_by_rule.setdefault(ri, []).append(d)

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

        # ----- 1. 先提取元数据（包容差/置信度/缺陷）---------
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

        confidence = item.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        # 关联该规则的 defects（rule_index 从 0 开始）
        rule_defects = defects_by_rule.get(i - 1, [])
        clean_defects = []
        for d in rule_defects:
            clean_defects.append({
                "type": d.get("type", "unknown"),
                "severity": d.get("severity", "info"),
                "description": d.get("description", ""),
            })

        # ----- 2. 同规则集去重+智能合并 -----
        normed = _normalize_text(rule_text)
        existing_rules = db.execute(
            select(Rule).where(
                Rule.rule_set_id == rule_set_id,
                Rule.doc_type == doc_type,
                Rule.check_category == check_category,
            )
        ).scalars().all()
        dup_rule = _find_similar_rule(rule_text, normed, existing_rules)
        if dup_rule:
            merged_count = _merge_into_existing(db, dup_rule, rule_text, confidence, tolerance, priority, clean_defects)
            logger.info("同集合并: [%s/%s] %s... -> %s (合并数=%d)",
                        doc_type, check_category, rule_text[:40], dup_rule.id, merged_count)
            skipped_detail = f"第 {i} 条：与已有规则 [{doc_type}/{check_category}] 相似，已自动合并（{rule_text[:30]}...）"
            errors.append(skipped_detail)
            continue

        # ----- 3. 规则健康状态分类 -----
        # 无实质缺陷(error/warning) -> 自动确认+启用
        # 有实质缺陷 -> 待确认+禁用
        has_real_defect = any(
            d.get("severity") in ("error", "warning")
            for d in clean_defects
        )
        if has_real_defect:
            status = "pending"
            enabled = False
        else:
            status = "confirmed"
            enabled = True

        try:
            payload = RuleCreate(
                doc_type=doc_type,
                check_category=check_category,
                rule_text=rule_text,
                tolerance=tolerance,
                enabled=enabled,
                priority=priority,
                confidence=confidence,
                status=status,
                defects=clean_defects,
            )
            rule_out = create_rule(db, rule_set_id, payload)
            imported.append(rule_out.model_dump(mode="json"))
            if progress is not None:
                update_task(progress, imported_rules=len(imported),
                            message=f"已入库 {len(imported)}/{len(raw_rules)} 条")
        except Exception as e:
            errors.append(f"第 {i} 条：入库失败 - {e}")
            if progress is not None:
                update_task(progress, import_errors=len(errors))

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

    # 检测新文档类型：将不在document_types表中的类型注册为pending_review
    new_doc_type_names = set()
    if new_doc_types:
        try:
            from ..models import DocumentType
            # 查询已有活跃类型名称
            existing = db.execute(
                select(DocumentType.name).where(DocumentType.status == "active")
            ).scalars().all()
            existing_set = set(existing)

            for name in sorted(new_doc_types):
                if name in existing_set:
                    continue
                # 检查是否已有 pending 记录
                pending = db.execute(
                    select(DocumentType).where(
                        DocumentType.name == name,
                        DocumentType.status == "pending_review",
                    )
                ).scalars().first()
                if pending:
                    new_doc_type_names.add(name)
                    continue
                # 创建新类型
                dt = DocumentType(
                    name=name,
                    category="other",
                    source="rule_import",
                    status="pending_review",
                )
                db.add(dt)
                db.commit()
                new_doc_type_names.add(name)
                logger.info("规则导入发现新文档类型（pending_review）: %s", name)
        except Exception:
            logger.warning("注册新文档类型失败（不影响已导入规则）", exc_info=True)

    # 构建冲突报告
    all_clean_defects = []
    for d in all_defects:
        all_clean_defects.append({
            "type": d.get("type", "unknown"),
            "severity": d.get("severity", "info"),
            "description": d.get("description", ""),
            "rule_index": d.get("rule_index"),
        })

    by_severity = {"error": 0, "warning": 0, "info": 0}
    for d in all_clean_defects:
        sev = d.get("severity", "info")
        if sev in by_severity:
            by_severity[sev] += 1

    conflict_report = ConflictReport(
        total_defects=len(all_clean_defects),
        by_severity=by_severity,
        defects=all_clean_defects,
    )

    return {
        "total": len(raw_rules),
        "imported": len(imported),
        "skipped": len(errors),
        "rules": imported,
        "errors": errors,
        "conflict_report": conflict_report.model_dump(mode="json") if conflict_report.total_defects > 0 else None,
        "new_doc_types": list(new_doc_types),
    }


def import_rules_with_skills(
    db: Session,
    rule_set_id: uuid.UUID,
    raw_text: str,
    skill_ids: list[uuid.UUID] | None = None,
    progress: ImportProgress | None = None,
) -> dict[str, Any]:
    """从文本导入规则，自动加载并应用 Skill。

    流程：编译 Skill directive → 应用预处理 → LLM ��析 → 应用后处理 → 入库

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID
        raw_text: 规则文本
        skill_ids: 指定应用的 Skill ID，不传则使用该规则集所有已启用的 Skill

    Returns:
        同 import_rules_from_text 的返回
    """
    if skill_ids:
        # 指定了具体的 Skill 列表，只加载这些
        from ..models import RuleParseSkill
        from sqlalchemy import select
        skills = db.execute(
            select(RuleParseSkill).where(
                RuleParseSkill.id.in_(skill_ids),
                RuleParseSkill.enabled.is_(True),
            )
        ).scalars().all()

        directive = RuleParseDirective()
        from .rule_parse_engine import _merge_content
        for s in skills:
            _merge_content(directive, s.content or {})
    else:
        # 未指定，从数据库编译（内置默认 + 自定义）
        from .rule_parse_engine import compile_directive
        directive = compile_directive(db, rule_set_id)

    result = import_rules_from_text(db, rule_set_id, raw_text, directive=directive, progress=progress)

    # 导入完成后自动触发冲突检测
    if result.get("imported", 0) > 0:
        try:
            from .rule_conflict_detector import run_conflict_detection
            if progress is not None:
                update_task(progress, status="conflict", message="正在检测规则冲突…", conflict_found=0)
            conflict_result = run_conflict_detection(db, str(rule_set_id), progress=progress)
            if progress is not None:
                update_task(progress, conflict_found=conflict_result.get("total_conflicts", 0))
            if conflict_result.get("total_conflicts", 0) > 0:
                logger.info(
                    "导入后冲突检测: %d 个冲突, %d 条规则受影响",
                    conflict_result["total_conflicts"],
                    conflict_result["affected_rules"],
                )
                # 将冲突信息合并到返回结果中
                result["conflict_detected"] = conflict_result["total_conflicts"]
        except Exception:
            logger.warning("导入后冲突检测失败（不影响已导入规则）", exc_info=True)

    return result
