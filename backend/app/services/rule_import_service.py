"""规则批量导入服务：用 LLM 把自然语言规则清单解析为结构化规则并入库。

流程（批次 10 v2 契约）：
1. 接收一段自然语言规则清单文本（可含多条规则）
2. 调用 LLM 解析为 rules + ontology：
   - rules 每项含 rule_text / structure / scope（doc_types|ALL）/ intents / tolerance / priority / confidence；
     doc_type / check_category 为可选派生标签（缺失时由结构断言/scope/intents 推导）
   - ontology 登记新文件类型/字段/检查意图
3. 校验放宽：rule_text 与 structure.assertion 至少其一存在
4. 规则集内语义级去重（scope/condition/exceptions 边界 + 文本相似度 + 结构化断言签名），
   合并后批量写入 rules 表
5. 新文档类型注册为 pending_review（key_fields 由 ontology.fields 预填），返回导入结果
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import ALL_DOC_TYPES, CHECK_CATEGORIES
from ..llm_client import LLMError, get_llm_client
from ..models import DocumentType, Rule, RuleSet
from . import rule_defects
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
from .settings_service import get_prompt, get_setting
from .doc_normalizer import (
    normalize_doc_type as _norm_doc_type,
    normalize_field as _norm_field,
    normalize_scope as _norm_scope,
    normalize_structure as _norm_structure,
)

logger = logging.getLogger(__name__)


# ============ LLM 提示词 ============
# 生效提示词统一由 prompt_templates.py 的 RULE_IMPORT_SYSTEM / RULE_IMPORT_USER 提供，
# 经 settings_service.get_prompt 读取（租户可在设置页覆盖）。此处不再内联重复维护。

_TABLE_ROW_SYSTEM_ADDITION = """### 表格行解析约束（系统注入，优先于其他提示词）
- 输入中每行 `[SOURCE_ROW=...]` 代表一个独立的规则候选，必须逐行解析并原样回填 `source_ref`。
- 禁止因为多行规则描述相似而把不同行合并成一条规则。
- 行内的文件类型/单据类型/适用单据/适用对象字段写入 scope；业务条件/场景/模式/触发条件字段写入 structure.condition。
- 结构化模板列按语义直接映射：规则正文/规则描述→rule_text；适用单据或适用对象→scope.doc_types；触发条件→structure.condition；比较关系→structure.assertion；容差→tolerance；例外→structure.exceptions；模板已填的值不得改写或丢弃。
- 列名尾部的（必填）（建议填）（可留空）等档位标注只给填写人看，映射时忽略标注、按标注前的主名判断。
- 留空的列表示交由你从规则正文推断，不是信息缺失；不得因某列留空而报缺陷或降低 confidence。
- 合并单元格的值已经展开到每个数据行；描述相似但 scope、condition 或 exceptions 不同的规则必须分别输出。"""


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
                    # 表格行是带 source_ref 的 JSON，宁可单行超长也不要按分隔符拆坏。
                    if line.startswith("[SOURCE_ROW="):
                        if len(buf) + len(line) + 1 <= max_chars:
                            buf = (buf + "\n" + line) if buf else line
                        else:
                            flush()
                            buf = line
                        continue
                    if len(line) > max_chars:
                        # 批次 4-1：表格行超长时优先按单元格分隔符 '|' 切（不切断单元格内容）
                        cells = [s.strip() for s in line.split("|") if s.strip()]
                        if len(cells) > 1 and all(len(c) <= max_chars for c in cells):
                            for seg in cells:
                                if len(buf) + len(seg) + 1 <= max_chars:
                                    buf = (buf + "\n" + seg) if buf else seg
                                else:
                                    flush()
                                    buf = seg
                        else:
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


def _freeze_value(value: Any) -> Any:
    """把 scope/condition/exceptions 转为可比较的稳定结构。"""
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = " ".join(value.split()).strip().lower()
        return normalized or ()
    if isinstance(value, dict):
        items = []
        for key in sorted(value):
            frozen = _freeze_value(value.get(key))
            if frozen != ():
                items.append((str(key), frozen))
        return tuple(items)
    if isinstance(value, (list, tuple)):
        return tuple(
            frozen
            for item in value
            for frozen in (_freeze_value(item),)
            if frozen != ()
        )
    return value


def _scope_key(scope: dict | None) -> tuple:
    """提取 scope 的文档类型边界，不把 intents 纳入去重身份。"""
    if not isinstance(scope, dict):
        return ("__MISSING__",)
    doc_types = scope.get("doc_types")
    if doc_types == "ALL":
        return ("ALL",)
    if isinstance(doc_types, list):
        names = tuple(sorted({
            str(name).strip().lower()
            for name in doc_types
            if str(name).strip()
        }))
        return names or ("__EMPTY__",)
    if isinstance(doc_types, str):
        name = doc_types.strip().lower()
        if name in ("all", "整批", "全部"):
            return ("ALL",)
        return (name,) if name else ("__EMPTY__",)
    return ("__EMPTY__",)


_MISSING_SCOPE_KEYS = {("__MISSING__",), ("__EMPTY__",)}


def _is_missing_scope(scope_key: tuple) -> bool:
    """scope 未填写（模板里「适用单据」列留空）。"""
    return scope_key in _MISSING_SCOPE_KEYS


def _context_compatible(new_context: tuple, old_context: tuple) -> tuple[bool, bool]:
    """判断两条规则的语义边界是否可比。

    返回 (是否可比, scope 是否严格相等)。
    scope 一方未填写时视为"填充不一致"而非"业务边界不同"：只要 condition 与
    exceptions 完全一致，仍允许在极高文本相似度下合并，避免"同一规则一次填了
    适用单据、一次留空"被重复入库。
    """
    if new_context[1:] != old_context[1:]:
        return False, False
    same_scope = new_context[0] == old_context[0]
    if same_scope:
        return True, True
    if _is_missing_scope(new_context[0]) or _is_missing_scope(old_context[0]):
        return True, False
    return False, False


def _semantic_context_signature(
    structure: dict | None,
    scope: dict | None,
) -> tuple:
    """去重身份中的适用范围：scope + condition + exceptions。

    同一描述但文件类型/业务条件/例外不同的规则必须保留为独立规则，不能靠
    文本相似度直接合并。
    """
    structure = structure or {}
    return (
        _scope_key(scope),
        _freeze_value(structure.get("condition")),
        _freeze_value(structure.get("exceptions") or []),
    )


def _structure_signature(structure: dict | None) -> tuple | None:
    """提取结构化断言的特征签名（operator + source/target 的 doc_type/field）。
    用于跨标签识别同一条规则（批次 10：语义级去重）。"""
    if not structure:
        return None
    assertion = structure.get("assertion") or {}
    if not assertion:
        return None
    src = assertion.get("source") or {}
    tgt = assertion.get("target") or {}
    sig = (
        str(assertion.get("operator") or "").strip(),
        str(src.get("doc_type") or "").strip(),
        str(src.get("field") or "").strip(),
        str(tgt.get("doc_type") or "").strip(),
        str(tgt.get("field") or "").strip(),
    )
    return sig if any(sig) else None


def _assertion_subject(structure: dict | None) -> tuple | None:
    """断言主体签名：源/目标的对象类型与字段（不含 operator）。

    两条规则的主体不同（如"数量"与"金额"），即使句式高度相似也是不同规则，
    不允许被文本相似度合并掉。operator 不参与，避免同一约束的不同措辞被拆散。
    """
    sig = _structure_signature(structure)
    if not sig:
        return None
    subject = sig[1:]
    return subject if any(subject) else None


def _missing_assertion_fields(structure: dict | None) -> list[str]:
    """列出结构化断言中缺失的 source/target.field。"""
    if not isinstance(structure, dict):
        return []
    assertion = structure.get("assertion")
    if not isinstance(assertion, dict) or not assertion:
        return []
    missing: list[str] = []
    for side in ("source", "target"):
        value = assertion.get(side)
        field = value.get("field") if isinstance(value, dict) else None
        if not str(field or "").strip():
            missing.append(side)
    return missing


def _find_similar_rule(
    new_text: str,
    new_normed: str,
    existing_rules: list,
    threshold: float = 0.75,
    new_structure: dict | None = None,
    new_scope: dict | None = None,
):
    """在规则集内查找与新规则高度相似的规则（批次 10：不再按格子分组）。
    匹配策略：
    0. 先要求 condition + exceptions 的语义边界一致；scope 一方留空时按"填充不一致"
       处理，但需要极高的文本相似度（0.95）才允许合并；
    0.1 双方都有结构化断言且断言主体（对象类型/字段）不同 → 直接判定为不同规则，
        避免"数量不大于"与"金额不大于"这类同句式异字段规则被相似度误合并；
    1. 文本相似度 >= threshold 直接命中；
    2. 结构化断言签名一致（operator + 源/目标类型与字段）且文本相似度 >= 0.6 也命中。
    """
    new_sig = _structure_signature(new_structure)
    new_subject = _assertion_subject(new_structure)
    new_context = _semantic_context_signature(new_structure, new_scope)
    for rule in existing_rules:
        old_context = _semantic_context_signature(rule.structure, rule.scope)
        compatible, same_scope = _context_compatible(new_context, old_context)
        if not compatible:
            continue
        old_subject = _assertion_subject(rule.structure)
        if new_subject and old_subject and new_subject != old_subject:
            continue
        existing_normed = _normalize_text(rule.rule_text)
        sim = _text_similarity(new_normed, existing_normed)
        effective_threshold = threshold if same_scope else 0.95
        if sim >= effective_threshold:
            return rule
        if new_sig:
            old_sig = _structure_signature(rule.structure)
            if old_sig and old_sig == new_sig and sim >= 0.6:
                return rule
    return None


def _normalize_scope(raw) -> dict | None:
    """归一化 LLM 输出的 scope 字段为 {"doc_types": [...] | "ALL"} 与 {"intents": [...]}。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.upper() in ("ALL", "整批", "全部"):
            return {"doc_types": "ALL"}
        return {"doc_types": [s]}
    if isinstance(raw, dict):
        out: dict = {}
        dt = raw.get("doc_types")
        if isinstance(dt, list):
            raw_names = [str(x).strip() for x in dt if x is not None and str(x).strip()]
            non_all = [n for n in raw_names if n.upper() not in ("ALL", "整批", "全部")]
            if not non_all and raw_names:
                out["doc_types"] = "ALL"  # 整个列表都是全局语义
            elif non_all:
                out["doc_types"] = sorted(set(non_all))
        elif isinstance(dt, str) and dt.strip().upper() in ("ALL", "整批", "全部"):
            out["doc_types"] = "ALL"
        its = raw.get("intents")
        if isinstance(its, list):
            ints = sorted({str(x).strip() for x in its if x is not None and str(x).strip()})
            if ints:
                out["intents"] = ints
        return out or None
    return None


def _extract_intents(item: dict, scope: dict | None) -> list[str]:
    """提取检查意图标签：scope.intents + item.intents + check_category（去重保序）。"""
    intents: list[str] = []
    scope_intents = (scope or {}).get("intents")
    if isinstance(scope_intents, list):
        intents.extend(scope_intents)
    item_intents = item.get("intents")
    if isinstance(item_intents, list):
        for x in item_intents:
            s = str(x).strip() if x is not None else ""
            if s and s not in intents:
                intents.append(s)
    item_cat = str(item.get("check_category") or "").strip()
    if item_cat and item_cat not in intents:
        intents.insert(0, item_cat)
    return intents


def _assertion_field_refs(structure: dict | None) -> list[tuple[str, str]]:
    """提取结构化断言引用的 (doc_type, field) 对（source / target）。"""
    if not isinstance(structure, dict):
        return []
    assertion = structure.get("assertion")
    if not isinstance(assertion, dict):
        return []
    refs: list[tuple[str, str]] = []
    for side in ("source", "target"):
        node = assertion.get(side)
        if isinstance(node, dict):
            refs.append(
                (
                    str(node.get("doc_type") or "").strip(),
                    str(node.get("field") or "").strip(),
                )
            )
    return refs


def _derive_doc_type(structure: dict | None, scope: dict | None) -> str:
    """doc_type 缺失时从结构化断言 source 或 scope.doc_types 推导（派生标签兜底）。"""
    if structure and structure.get("assertion"):
        src = structure["assertion"].get("source") or {}
        sdt = str(src.get("doc_type") or "").strip()
        if sdt:
            return sdt
    scope_dt = (scope or {}).get("doc_types")
    if isinstance(scope_dt, list) and scope_dt:
        return "" if scope_dt[0].upper() in ("ALL", "整批", "全部") else scope_dt[0]
    if isinstance(scope_dt, str):
        return "" if scope_dt.upper() in ("ALL", "整批", "全部") else scope_dt
    return ""


def _merge_ontology(target: dict, source: dict) -> None:
    """合并各分段的 ontology（doc_types/fields/check_intents 按关键字段去重）。"""
    if not isinstance(source, dict):
        return
    for key, merge_key in (("doc_types", "name"), ("fields", "name"), ("check_intents", "name")):
        items = source.get(key)
        if not isinstance(items, list):
            continue
        seen = {str(x.get(merge_key)) for x in target.setdefault(key, []) if isinstance(x, dict)}
        for x in items:
            if not isinstance(x, dict):
                continue
            k = str(x.get(merge_key) or "").strip()
            if not k or k in seen:
                continue
            seen.add(k)
            target[key].append(x)


def _merge_missing_structure(base: dict | None, incoming: dict | None) -> dict:
    """递归合并结构字段：保留已有值，仅用新值补齐缺失项。"""
    merged = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(incoming, dict):
        return merged
    for key, value in incoming.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_missing_structure(merged[key], value)
    return merged


def _merge_into_existing(
    db,
    existing_rule,
    new_rule_text: str,
    new_confidence: float | None,
    new_tolerance: dict,
    new_priority: int,
    new_defects: list[dict],
    new_scope: dict | None = None,
    new_intents: list[str] | None = None,
    new_doc_type: str | None = None,
    new_check_category: str | None = None,
    new_structure: dict | None = None,
) -> int:
    """将新规则合并到已有规则中。返回合并的字段数。

    合并策略：
    - confidence: 取较高值
    - tolerance: 合并（新值覆盖旧值为空的字段）
    - defects: 去重合并（按 type + description 去重）
    - rule_text: 保留更长的（通常更完整）
    - priority: 保留较小的（更优先）
    - scope/intents: 并集合并
    - doc_type/check_category: 旧值为空时补全（批次 10）
    - structure: 仅在语义边界一致时补齐缺失字段（由调用方保证）
    """
    changes = 0

    # 标签补全：旧值为空时用新值
    if new_doc_type and not existing_rule.doc_type:
        existing_rule.doc_type = new_doc_type
        changes += 1
    if new_check_category and not existing_rule.check_category:
        existing_rule.check_category = new_check_category
        changes += 1

    # scope 合并（doc_types 并集 / ALL 优先；intents 并集）
    if new_scope:
        old_scope = dict(existing_rule.scope or {})
        merged_scope = dict(old_scope)
        new_dt = new_scope.get("doc_types")
        old_dt = old_scope.get("doc_types")
        if new_dt == "ALL":
            merged_scope["doc_types"] = "ALL"
        elif isinstance(new_dt, list):
            old_names = old_dt if isinstance(old_dt, list) else []
            merged_names = sorted(set(old_names) | set(new_dt))
            if merged_names:
                merged_scope["doc_types"] = merged_names
        merged_ints = sorted(
            set(old_scope.get("intents") or []) | set(new_scope.get("intents") or [])
        )
        if merged_ints:
            merged_scope["intents"] = merged_ints
        if merged_scope != old_scope:
            existing_rule.scope = merged_scope
            changes += 1

    # intents 合并（去重保序）
    if new_intents:
        old_ints = list(existing_rule.intents or [])
        merged_ints = list(old_ints)
        for x in new_intents:
            if x and x not in merged_ints:
                merged_ints.append(x)
        if merged_ints != old_ints:
            existing_rule.intents = merged_ints
            changes += 1

    # structure：补齐已有规则缺失的条件/断言/例外字段。
    if new_structure:
        old_structure = existing_rule.structure or {}
        merged_structure = _merge_missing_structure(old_structure, new_structure)
        if merged_structure != old_structure:
            existing_rule.structure = merged_structure
            changes += 1

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
    added_real_defect = False
    for d in (new_defects or []):
        d = dict(d)
        d["rule_code"] = getattr(existing_rule, "rule_code", None)
        # 同一缺陷再次并入时，若此前已被标记为"已处理"，需重新打开
        d.pop("resolution", None)
        d.pop("resolved_at", None)
        d.pop("resolved_by", None)
        d.pop("resolution_note", None)
        key = (d.get("type"), d.get("description"))
        if key not in old_keys:
            old_defects.append(d)
            old_keys.add(key)
            changes += 1
            if d.get("severity") in rule_defects.BLOCKING_SEVERITIES:
                added_real_defect = True
    if changes > 0:
        existing_rule.defects = old_defects
        # 重新评估规则健康状态：新并入实质缺陷时回落到待确认/禁用。
        if added_real_defect:
            existing_rule.status = "pending"
            existing_rule.enabled = False
            existing_rule.confirmed_at = None
            existing_rule.confirmed_by = None
        elif not rule_defects.has_open_blocking_defect(old_defects) and existing_rule.status != "confirmed":
            existing_rule.status = "confirmed"
            existing_rule.enabled = True
            # 自动确认 = 系统认可，残留（提示类）缺陷一并标记为已处理
            resolved, count = rule_defects.resolve_defects(
                existing_rule.defects,
                by=rule_defects.RESOLVED_BY_IMPORT,
                note=rule_defects.AUTO_CONFIRM_NOTE,
            )
            if count:
                existing_rule.defects = resolved
            changes += 1

    db.commit()
    return changes


def _known_doc_types(
    db: Session,
    rule_set_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> list[str]:
    """构造规则导入提示词中的"已知文件类型"清单。

    来源优先级：
    1. 当前规则集声明的适用文件类型（rule_set.doc_types，保持声明顺序）
    2. 全局 DocumentType 注册表（active + pending_review，按名称排序）

    两者去重合并；若均无内容（如注册表尚未初始化），回退到 constants 内置清单，
    保证导入流程在任何情况下都能拿到候选类型。
    """
    names: list[str] = []
    seen: set[str] = set()

    rs = db.execute(
        select(RuleSet).where(
            RuleSet.id == rule_set_id,
            RuleSet.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if rs is not None:
        for name in rs.doc_types or []:
            name = str(name).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    rows = db.execute(
        select(DocumentType.name)
        .where(
            DocumentType.tenant_id == tenant_id,
            DocumentType.status.in_(("active", "pending_review")),
        )
        .order_by(DocumentType.name)
    ).scalars().all()
    for name in rows:
        name = str(name).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    if not names:
        return list(ALL_DOC_TYPES)
    return names


def _known_check_categories(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
) -> list[str]:
    """构造规则导入提示词中的"已知检查项"清单。

    与 _known_doc_types 对称，但**只喂已确认（active）的检查项**：
    未确认的新检查项如果回灌进提示词，会让大模型反复复用同一个自造名称，
    形成"自我强化"的双轨繁殖。注册表为空时回退到内置四类常量。
    """
    from .check_intent_service import active_intent_names

    names: list[str] = []
    seen: set[str] = set()
    for name in active_intent_names(db, tenant_id):
        name = str(name).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    if not names:
        return list(CHECK_CATEGORIES)
    return names


_SOURCE_REF_RE = re.compile(r"\[SOURCE_ROW=([^\]\n]+)\]")


def _source_refs_in_text(text: str) -> list[str]:
    """提取一个 chunk 中出现的来源行引用（用于 LLM 未回填 source_ref 时兜底）。"""
    return [match.group(1).strip() for match in _SOURCE_REF_RE.finditer(text or "")]


def _resolve_source_ref(raw_ref: Any, source_row_map: dict[str, dict]) -> str:
    """把 LLM 返回的 source_ref/source_row 归一为 source_ref。"""
    raw = str(raw_ref or "").strip().strip("[]")
    if not raw:
        return ""
    if raw.upper().startswith("SOURCE_ROW="):
        raw = raw.split("=", 1)[1].strip()
    if raw in source_row_map:
        return raw
    if raw.isdigit():
        matches = [
            ref
            for ref, row in source_row_map.items()
            if str(row.get("row")) == raw
        ]
        if len(matches) == 1:
            return matches[0]
    return raw


def _compute_source_coverage(
    source_rows: list[dict[str, Any]],
    covered_refs: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """计算源表行覆盖率，返回 (coverage, warnings)。"""
    expected_refs = {
        str(row.get("source_ref") or "").strip()
        for row in source_rows
        if str(row.get("source_ref") or "").strip()
    }
    if not expected_refs:
        return None, []

    missing = sorted(expected_refs - covered_refs)
    unexpected = sorted(covered_refs - expected_refs)
    warnings: list[str] = []
    if missing:
        preview = "、".join(missing[:10])
        suffix = " 等" if len(missing) > 10 else ""
        warnings.append(
            f"源表有 {len(missing)} 行未解析出规则（{preview}{suffix}），可能存在漏行或错误合并"
        )
    if unexpected:
        preview = "、".join(unexpected[:10])
        suffix = " 等" if len(unexpected) > 10 else ""
        warnings.append(
            f"LLM 返回了 {len(unexpected)} 个不存在的来源行（{preview}{suffix}）"
        )
    if not covered_refs:
        warnings.append("LLM 未返回任何来源行引用，无法校验规则行覆盖率")

    coverage = {
        "expected_rows": len(expected_refs),
        "covered_rows": len(expected_refs & covered_refs),
        "missing_rows": missing,
        "unexpected_rows": unexpected,
    }
    return coverage, warnings


def _with_correction_digest(
    system_content: str,
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
) -> str:
    """把本租户的历史人工修正追加到解析提示词（数据化回灌）。

    人工只处理 LLM 判不了的部分；同一条修正被重复确认时（hit_count 高），
    说明模型上次没学会——这里把它作为**数据**再喂一次，而不是在代码里为它加分支。
    账本读取失败不影响导入。
    """
    try:
        from .correction_ledger import corrections_digest

        digest = corrections_digest(db, tenant_id, rule_set_id)
    except Exception:  # pragma: no cover - 账本异常不应阻断规则导入
        logger.warning("读取人工修正账本失败（不影响规则解析）", exc_info=True)
        return system_content
    if not digest:
        return system_content
    return (
        system_content
        + "\n\n### 本租户人工裁定（人工修正过的解析口径，优先级高于你的默认习惯）\n"
        + "以下是本租户对**同类规则**解析结果的人工修正。遇到与示例同类的规则时，直接采用"
        + "修正后的结果；只有当它与规则原文直接冲突时才不采纳，并在 defects 里说明原因。\n"
        + digest
    )


def import_rules_from_text(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    raw_text: str,
    directive: RuleParseDirective | None = None,
    progress: ImportProgress | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    source_meta: dict[str, Any] | None = None,
    import_context: str | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """从自然语言规则清单文本批量导入规则。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（导入规则归到该规则集下）
        raw_text: 自然语言规则清单文本
        directive: Skill 编译后的解析指令（可选），指定后应用预处理/字段映射/默认值等
        source_rows: 结构化来源行（Excel 等表格格式），用于来源追溯与覆盖率校验
        source_meta: 来源文件元数据（文件名、提取器信息等）
        import_context: 文档级声明（业务场景、术语与别名等），注入用户提示词头部
        scenario: 本次实际使用的业务场景键，写入每条规则的 scenario 标签
            （自定义场景通道为 None）

    Returns:
        {"total": 解析总数, "imported": 入库成功数, "skipped": 跳过数, "rules": [入库的规则],
         "errors": [跳过原因], "conflict_report": 冲突与缺陷报告}
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("规则清单文本为空")
    rule_set = db.execute(
        select(RuleSet).where(
            RuleSet.id == rule_set_id,
            RuleSet.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if rule_set is None:
        raise ValueError("规则集不存在")

    source_rows = source_rows or []
    source_row_map = {
        str(row.get("source_ref") or "").strip(): row
        for row in source_rows
        if str(row.get("source_ref") or "").strip()
    }
    covered_source_refs: set[str] = set()
    source_warnings: list[str] = []

    # 应用文本预处理（如果指定了 directive）
    if directive:
        raw_text = apply_text_preprocessing(raw_text, directive.text_preprocessing)

    llm = get_llm_client()
    doc_types_str = "、".join(
        _known_doc_types(db, rule_set_id, tenant_id)
    )
    check_categories_str = "、".join(
        _known_check_categories(db, tenant_id, rule_set_id)
    )

    # 长文本分段解析：避免单次输出超 max_tokens 被截断（JSON 不完整）
    chunks = _split_text(raw_text)
    logger.info("规则导入分段: 共 %d 段, 各段长度=%s", len(chunks), [len(c) for c in chunks])
    if progress is not None:
        update_task(progress, status="parsing", total_chunks=len(chunks), parsed_chunks=0,
                    message=f"正在解析规则（共 {len(chunks)} 段）…")
    raw_rules: list[dict] = []
    all_defects: list[dict] = []  # 收集所有段的 defects
    chunk_errors: list[str] = []
    rule_chunks: list[int] = []  # 每条 raw_rule 所属分段（用于 provenance）
    rule_chunk_source_refs: list[list[str]] = []  # 每条 raw_rule 所属分段中的来源行引用
    ontology: dict[str, list] = {"doc_types": [], "fields": [], "check_intents": []}
    for idx, chunk in enumerate(chunks, start=1):
        chunk_source_refs = _source_refs_in_text(chunk)
        try:
            user_prompt = get_prompt(db, tenant_id, "rule_import.user").format(
                doc_types=doc_types_str,
                check_categories=check_categories_str,
                raw_text=chunk,
                import_context=import_context or "",
            )
        except KeyError:
            # 租户自定义模板使用了未知占位符：退化为只填充基础占位符
            user_prompt = get_prompt(db, tenant_id, "rule_import.user").format(
                doc_types=doc_types_str,
                check_categories=check_categories_str,
                raw_text=chunk,
            )
            if import_context:
                user_prompt = f"{import_context}\n\n{user_prompt}"
        # 构建动态 System Prompt（附加 Skill 指令 + 领域上下文）
        system_content = get_prompt(db, tenant_id, "rule_import.system")
        if source_rows:
            system_content += "\n\n" + _TABLE_ROW_SYSTEM_ADDITION
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

        # 人工修正回灌：把本租户"人改过什么"作为数据带进解析提示词
        system_content = _with_correction_digest(
            system_content, db, tenant_id, rule_set_id
        )
        try:
            resp = llm.chat_json(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=8192,
                disable_thinking=True,
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
            for r in rules:
                if isinstance(r, dict):
                    raw_rules.append(r)
                    rule_chunks.append(idx)
                    rule_chunk_source_refs.append(list(chunk_source_refs))
            # 收集 ontology（新文件类型/字段/检查意图）
            ont = resp.get("ontology")
            if isinstance(ont, dict):
                _merge_ontology(ontology, ont)
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
    # ② 检查项写时归一：别名索引在循环前建一次；未命中规范名的新名称登记为待确认
    from .check_intent_service import (
        build_intent_alias_index,
        resolve_or_register as register_check_intent,
    )

    intent_alias_index = build_intent_alias_index(db, tenant_id)
    # 惰性读取：只在真的发现未登记检查项时才查设置，避免无谓的额外查询
    _auto_confirm_cache: bool | None = None

    def _intent_auto_confirm() -> bool:
        nonlocal _auto_confirm_cache
        if _auto_confirm_cache is None:
            try:
                _auto_confirm_cache = bool(
                    get_setting(db, tenant_id, "rules.auto_confirm_new_check_intents")
                )
            except Exception:
                logger.warning(
                    "读取 rules.auto_confirm_new_check_intents 失败，按未开启处理",
                    exc_info=True,
                )
                _auto_confirm_cache = False
        return _auto_confirm_cache
    unknown_field_refs: dict[str, set[str]] = {}
    rule_unknown_fields: dict[int, list[str]] = {}

    # ③ 候选字段集：内置模板 + 租户登记类型的 key_fields + 本次 ontology 声明
    # 只有"类型已登记且字段不在候选集里"才判为未登记字段（新类型本次无法判断，故跳过）
    candidate_fields: dict[str, set[str]] = {}

    def _add_candidate(doc_type_name: str, fields) -> None:
        doc_type_name = str(doc_type_name or "").strip()
        if not doc_type_name:
            return
        bucket = candidate_fields.setdefault(doc_type_name, set())
        for field_name in fields or []:
            field_name = str(field_name or "").strip()
            if field_name:
                bucket.add(field_name)

    from ..constants import FIELD_TEMPLATES as _FIELD_TEMPLATES

    for _dt, _fields in _FIELD_TEMPLATES.items():
        _add_candidate(_dt, _fields)
    for _row in db.execute(
        select(DocumentType.name, DocumentType.key_fields).where(
            DocumentType.tenant_id == tenant_id
        )
    ).all():
        _add_candidate(_row[0], _row[1])
    for _f in ontology.get("fields") or []:
        if isinstance(_f, dict):
            _fdt = _norm_doc_type(
                db, str(_f.get("doc_type") or "").strip(), tenant_id=tenant_id
            )
            _fname, _ = _norm_field(
                db, _fdt, str(_f.get("name") or "").strip(), tenant_id=tenant_id
            )
            _add_candidate(_fdt, [_fname])

    for i, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 条：非合法对象，跳过")
            continue
        # ----- 0. 派生标签与规则自描述（批次 10）-----
        # doc_type / check_category 均为可选派生标签；scope/intents 承载真正的适用范围
        doc_type_raw = item.get("doc_type")
        doc_type = str(doc_type_raw).strip() if doc_type_raw is not None else ""
        check_category_raw = item.get("check_category")
        check_category = str(check_category_raw).strip() if check_category_raw is not None else ""
        rule_text = str(item.get("rule_text", "")).strip()

        # 结构化审查意图（批次 7）：LLM 可选输出，清洗为 dict 或 None
        structure: dict[str, Any] | None = None
        structure_raw = item.get("structure")
        if isinstance(structure_raw, dict) and structure_raw.get("assertion"):
            structure = {
                "condition": structure_raw.get("condition") or None,
                "assertion": structure_raw.get("assertion"),
                "exceptions": structure_raw.get("exceptions") or [],
            }
        missing_assertion_fields = _missing_assertion_fields(structure)

        # 规则自描述：scope + intents（批次 10）
        scope = _normalize_scope(item.get("scope"))
        intents = _extract_intents(item, scope)

        # 校验放宽（批次 10）：不再强制 doc_type/check_category，只要求有可执行内容
        if not rule_text and not (structure and structure.get("assertion")):
            errors.append(f"第 {i} 条：规则文本与结构化断言均为空，跳过")
            continue
        # rule_text 缺失时用结构化断言生成可读文本（RuleCreate.rule_text 必填）
        if not rule_text and structure and structure.get("assertion"):
            a = structure["assertion"]
            src = a.get("source") or {}
            tgt = a.get("target") or {}
            rule_text = (
                f"{src.get('doc_type') or ''}.{src.get('field') or ''} "
                f"{a.get('operator') or '等于'} "
                f"{tgt.get('doc_type') or ''}.{tgt.get('field') or ''}"
            ).strip()

        # 派生标签兜底：doc_type 缺失时从结构化断言/scope 推导；check_category 缺失时取首个意图
        if not doc_type:
            doc_type = _derive_doc_type(structure, scope)
        if not check_category and intents:
            check_category = intents[0]

        # 不完整断言不能作为可执行结构自动确认；保留 condition/exceptions，
        # 让规则文本走语义审查或旧版 LLM 转图兜底。
        if missing_assertion_fields:
            assert isinstance(structure, dict)
            structure.pop("assertion", None)
            if (
                not structure.get("condition")
                and not structure.get("exceptions")
                and not structure.get("requirement")
            ):
                structure = None

        # 批次 11：写时归一——文档类型/字段名映射到规范名（显式别名表，防双轨繁殖）。
        # 未命中规范名/别名的新名称原样保留，由下方"新类型检测"注册为 pending_review。
        if doc_type:
            doc_type = _norm_doc_type(db, doc_type, tenant_id=tenant_id)
        if scope:
            scope = _norm_scope(db, scope, tenant_id=tenant_id)
        if structure:
            structure = _norm_structure(
                db,
                structure,
                doc_type or None,
                tenant_id=tenant_id,
            )

        # ② 检查项写时归一：先按别名表映射到规范名，未登记的交给下方登记流程
        if check_category:
            check_category = intent_alias_index.get(check_category, check_category)
        if intents:
            intents = [intent_alias_index.get(x, x) for x in intents]
        if scope and isinstance(scope.get("intents"), list):
            scope = dict(scope)
            scope["intents"] = [intent_alias_index.get(x, x) for x in scope["intents"]]

        # ③ 未登记字段检测：结构化断言引用的字段若不在该类型的抽取模板里，
        # 审查时必然得不到值 → 导入期就提示，而不是等审查时给一句"无法核验"
        for ref_doc_type, ref_field in _assertion_field_refs(structure):
            if not ref_doc_type or not ref_field:
                continue
            if ref_doc_type not in candidate_fields:
                continue  # 类型本身没登记（例如本次新发现），无从判断字段
            if ref_field not in candidate_fields[ref_doc_type]:
                ref_key = f"{ref_doc_type}.{ref_field}"
                unknown_field_refs.setdefault(ref_key, set()).add(str(i))
                rule_unknown_fields.setdefault(i, []).append(ref_key)

        # 收集新发现的类型/意图（含 scope 声明；ontology 声明在循环后并入）
        if doc_type:
            new_doc_types.add(doc_type)
        if check_category:
            new_check_categories.add(check_category)
        scope_dt = (scope or {}).get("doc_types")
        if isinstance(scope_dt, list):
            new_doc_types.update(x for x in scope_dt if x)

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
        # 注意：变量名不能叫 rule_defects，否则会遮蔽同名的缺陷语义模块
        rule_defect_items = defects_by_rule.get(i - 1, [])
        clean_defects = []
        for d in rule_defect_items:
            clean_defects.append({
                "type": d.get("type", "unknown"),
                "severity": d.get("severity", "info"),
                "description": d.get("description", ""),
            })
        for ref_key in rule_unknown_fields.get(i, []):
            clean_defects.append({
                "type": "unknown_field",
                "severity": "info",
                "description": (
                    f"规则引用了未登记字段 {ref_key}，该字段不在其文件类型的抽取模板中，"
                    "审查时可能无法核验；可在「文档类型」页补充字段或改由语义审查处理"
                ),
            })
        if missing_assertion_fields:
            clean_defects.append({
                "type": "incomplete_condition",
                "severity": "warning",
                "description": (
                    "structure.assertion 缺少 "
                    f"{'/'.join(missing_assertion_fields)} 字段，"
                    "已退回按规则原文审查"
                ),
            })
        # 判定架构 P3：契约体检——把"规则形状装不进结构契约"的问题在导入期显性化，
        # 而不是等审查时由引擎猜（历史上这类静默丢弃导致"金额与签字比大小"的错误建图）。
        from .review_intent import model_warnings as _model_warnings

        _st = structure if isinstance(structure, dict) else {}
        _cond = _st.get("condition") if isinstance(_st.get("condition"), dict) else None
        _assertion = _st.get("assertion") if isinstance(_st.get("assertion"), dict) else {}
        _src = (_assertion.get("source") or {}).get("field") if isinstance(_assertion.get("source"), dict) else None
        _tgt = (_assertion.get("target") or {}).get("field") if isinstance(_assertion.get("target"), dict) else None
        for _warn in _model_warnings(
            rule_text,
            _cond,
            str(_assertion.get("operator") or ""),
            str(_src or ""),
            str(_tgt or ""),
        ):
            clean_defects.append({
                "type": "modeling_risk",
                "severity": "warning",
                "description": _warn + "；请核对 structure.condition / structure.requirement",
            })

        # 来源追溯（批次 10 / 表格行级）
        raw_source_ref = item.get("source_ref") or item.get("source_row")
        source_ref = _resolve_source_ref(raw_source_ref, source_row_map)
        chunk_refs = (
            rule_chunk_source_refs[i - 1]
            if i - 1 < len(rule_chunk_source_refs)
            else []
        )
        if not source_ref and len(chunk_refs) == 1:
            source_ref = chunk_refs[0]
        source_row = source_row_map.get(source_ref) if source_ref else None
        if source_ref:
            covered_source_refs.add(source_ref)
            if source_row_map and source_row is None:
                source_warnings.append(
                    f"第 {i} 条规则返回了不存在的来源行 {source_ref}"
                )

        provenance: dict[str, Any] | None = {
            "chunk_index": rule_chunks[i - 1] if i - 1 < len(rule_chunks) else None,
            "text": rule_text[:200],
            "source_ref": source_ref or None,
            "source_file": (source_meta or {}).get("filename"),
            "sheet": (source_row or {}).get("sheet"),
            "row": (source_row or {}).get("row"),
            "row_values": (source_row or {}).get("values"),
            "merged_from": (source_row or {}).get("merged_from"),
        }

        # ----- 2. 规则集内语义去重+智能合并（批次 10：不再按 (doc_type, check_category) 格子分组）-----
        normed = _normalize_text(rule_text)
        existing_rules = db.execute(
            select(Rule).where(
                Rule.tenant_id == tenant_id,
                Rule.rule_set_id == rule_set_id,
            )
        ).scalars().all()
        dup_rule = _find_similar_rule(
            rule_text,
            normed,
            existing_rules,
            new_structure=structure,
            new_scope=scope,
        )
        if dup_rule:
            merged_count = _merge_into_existing(
                db, dup_rule, rule_text, confidence, tolerance, priority, clean_defects,
                new_scope=scope, new_intents=intents,
                new_doc_type=doc_type or None, new_check_category=check_category or None,
                new_structure=structure,
            )
            label = f"{doc_type or '整批/全部'}/{check_category or '未分类'}"
            logger.info("同集合并: [%s] %s... -> %s (合并数=%d)",
                        label, rule_text[:40], dup_rule.id, merged_count)
            skipped_detail = (
                f"第 {i} 条：与已有规则 {dup_rule.rule_code} [{label}] 相似，"
                f"已自动合并（{rule_text[:30]}...）"
            )
            errors.append(skipped_detail)
            continue

        # ----- 3. 规则健康状态分类 -----
        # 无实质缺陷(error/warning) -> 自动确认+启用（提示类缺陷一并视为已认可）
        # 有实质缺陷 -> 待确认+禁用，缺陷保持未处理，规则进入「错误/警告」页签
        has_real_defect = any(
            d.get("severity") in rule_defects.BLOCKING_SEVERITIES
            for d in clean_defects
        )
        if has_real_defect:
            status = "pending"
            enabled = False
        else:
            status = "confirmed"
            enabled = True
            clean_defects, _resolved = rule_defects.resolve_defects(
                clean_defects,
                by=rule_defects.RESOLVED_BY_IMPORT,
                note=rule_defects.AUTO_CONFIRM_NOTE,
            )

        try:
            payload = RuleCreate(
                doc_type=doc_type or None,
                check_category=check_category or None,
                scenario=scenario,
                rule_text=rule_text,
                tolerance=tolerance,
                structure=structure,
                scope=scope,
                intents=intents,
                provenance=provenance,
                enabled=enabled,
                priority=priority,
                confidence=confidence,
                status=status,
                defects=clean_defects,
            )
            rule_out = create_rule(db, tenant_id, rule_set_id, payload)
            rule_dict = rule_out.model_dump(mode="json")
            rule_dict["source_ref"] = source_ref or None
            rule_dict["source_row"] = (source_row or {}).get("row")
            # 原文对照：记录该规则来源的分段原文，供前端"原文 ↔ 解析结果"视图
            ci = rule_chunks[i - 1] if i - 1 < len(rule_chunks) else None
            if isinstance(ci, int) and 0 <= ci - 1 < len(chunks):
                rule_dict["source_text"] = chunks[ci - 1]
            imported.append(rule_dict)
            if progress is not None:
                update_task(progress, imported_rules=len(imported),
                            message=f"已入库 {len(imported)}/{len(raw_rules)} 条")
        except Exception as e:
            errors.append(f"第 {i} 条：入库失败 - {e}")
            if progress is not None:
                update_task(progress, import_errors=len(errors))

    errors.extend(chunk_errors)

    # 将 LLM ontology 声明的新文件类型/检查意图并入发现集合（批次 10）
    for x in ontology.get("doc_types") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            new_doc_types.add(
                _norm_doc_type(
                    db,
                    str(x["name"]).strip(),
                    tenant_id=tenant_id,
                )
                or str(x["name"]).strip()
            )
    for x in ontology.get("check_intents") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            raw_intent = str(x["name"]).strip()
            new_check_categories.add(intent_alias_index.get(raw_intent, raw_intent))

    # 更新 rule_set.doc_types / check_categories，合并新发现的类型
    if imported:
        try:
            rs = db.execute(
                select(RuleSet).where(
                    RuleSet.id == rule_set_id,
                    RuleSet.tenant_id == tenant_id,
                )
            ).scalars().first()
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

    # ② 新检查项登记：pending_review（人工确认后才参与矩阵分列与执行分派）
    registered_intents: list[str] = []
    if new_check_categories:
        for name in sorted(new_check_categories):
            canonical, is_new = register_check_intent(
                db, tenant_id, name, auto_confirm=_intent_auto_confirm()
            )
            if is_new and canonical:
                registered_intents.append(canonical)
        if registered_intents:
            db.commit()
            logger.info(
                "规则导入发现 %d 个新检查项（%s）: %s",
                len(registered_intents),
                "已自动确认" if _intent_auto_confirm() else "待确认",
                "、".join(registered_intents),
            )

    # 从 ontology.fields 建立 类型→字段 映射，用于预填新类型的 key_fields（批次 10）
    field_map: dict[str, list[str]] = {}
    for f in ontology.get("fields") or []:
        if not isinstance(f, dict):
            continue
        fdt = (
            _norm_doc_type(
                db,
                str(f.get("doc_type") or "").strip(),
                tenant_id=tenant_id,
            )
            or ""
        )
        fname, _ = _norm_field(
            db,
            fdt,
            str(f.get("name") or "").strip(),
            tenant_id=tenant_id,
        )
        fname = str(fname or "").strip()
        if fdt and fname and fname not in field_map.setdefault(fdt, []):
            field_map[fdt].append(fname)

    # 检测新文档类型：将不在document_types表中的类型注册为pending_review
    new_doc_type_names = set()
    if new_doc_types:
        try:
            # 查询已有活跃类型名称
            existing = db.execute(
                select(DocumentType.name).where(
                    DocumentType.tenant_id == tenant_id,
                    DocumentType.status == "active",
                )
            ).scalars().all()
            existing_set = set(existing)

            for name in sorted(new_doc_types):
                if name in existing_set:
                    continue
                # 检查是否已有 pending 记录
                pending = db.execute(
                    select(DocumentType).where(
                        DocumentType.tenant_id == tenant_id,
                        DocumentType.name == name,
                        DocumentType.status == "pending_review",
                    )
                ).scalars().first()
                if pending:
                    # 已有 pending 记录：合并补充 key_fields（避免重复创建）
                    new_fields = field_map.get(name) or []
                    if new_fields:
                        cur = list(pending.key_fields or [])
                        merged = cur + [x for x in new_fields if x not in cur]
                        if merged != cur:
                            pending.key_fields = merged
                            db.commit()
                    new_doc_type_names.add(name)
                    continue
                # 创建新类型
                dt = DocumentType(
                    tenant_id=tenant_id,
                    name=name,
                    source="rule_import",
                    status=(
                        "active"
                        if get_setting(
                            db,
                            tenant_id,
                            "rules.auto_confirm_new_types",
                        )
                        else "pending_review"
                    ),
                    key_fields=field_map.get(name, []),
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

    source_coverage, coverage_warnings = _compute_source_coverage(
        source_rows, covered_source_refs
    )
    import_warnings = list(dict.fromkeys(source_warnings + coverage_warnings))

    # ③ 未登记字段：汇总为导入告警（明细已作为 info 缺陷挂在对应规则上）
    if unknown_field_refs:
        detail = "；".join(
            f"{ref}（第 {'/'.join(sorted(rows, key=int))} 条）"
            for ref, rows in sorted(unknown_field_refs.items())
        )
        import_warnings = list(dict.fromkeys(
            import_warnings
            + [
                f"以下字段未登记在对应文件类型的抽取模板中，审查时可能无法核验：{detail}。"
                "可在「文档类型」页补充字段，或把该判定交给语义审查"
            ]
        ))

    return {
        "total": len(raw_rules),
        "imported": len(imported),
        "skipped": len(errors),
        "rules": imported,
        "errors": errors,
        "conflict_report": conflict_report.model_dump(mode="json") if conflict_report.total_defects > 0 else None,
        "new_doc_types": list(new_doc_types),
        "new_check_categories": registered_intents,
        "import_warnings": import_warnings,
        "source_coverage": source_coverage,
    }


def import_rules_with_skills(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    raw_text: str,
    skill_ids: list[uuid.UUID] | None = None,
    progress: ImportProgress | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    source_meta: dict[str, Any] | None = None,
    scenario: str | None = None,
    import_context: str | None = None,
) -> dict[str, Any]:
    """从文本导入规则，自动加载并应用 Skill。

    流程：编译 Skill directive → 应用预处理 → LLM ��析 → 应用后处理 → 入库

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID
        raw_text: 规则文本
        skill_ids: 「自定义场景」通道：勾选的 Skill（与基线之上的叠加）。传了就**不再加载
            场景知识包**，生效 = 系统预设（通用契约包）+ 勾选的 Skill；不传则走「出厂场景」
            通道（通用契约 + 场景包 + 规则集自定义 Skill）
        source_rows: 结构化来源行（Excel 等表格格式）
        source_meta: 来源文件元数据
        scenario: 本次导入声明的业务场景键（优先级高于规则集默认场景）
        import_context: 文档级声明（场景、术语与别名），注入用户提示词头部

    Returns:
        同 import_rules_from_text 的返回
    """
    rule_set = db.execute(
        select(RuleSet).where(
            RuleSet.id == rule_set_id,
            RuleSet.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if rule_set is None:
        raise ValueError("规则集不存在")

    # 两条互斥通道（与导入弹窗的两个下拉一一对应）：
    #   A. 出厂场景（未勾选 Skill）：通用契约 + 本次场景的场景包 + 规则集自定义 Skill，
    #      场景优先级 = 导入参数 > 文件声明 > 规则集默认 > 兜底 trade；
    #   B. 自定义场景（勾选了 Skill）：系统预设（通用契约）+ 勾选的 Skill；
    #      此时不加载任何场景包，避免出厂领域知识与客户自定义场景混用。
    from .rule_parse_engine import _merge_content, compile_directive_with_skills

    if skill_ids:
        directive, preset_skills = compile_directive_with_skills(
            db, tenant_id, rule_set_id, preset_only=True
        )
    else:
        directive, preset_skills = compile_directive_with_skills(
            db, tenant_id, rule_set_id, scenario=scenario
        )
    baseline_ids = {s.id for s in preset_skills}
    applied_skills = [s.name for s in preset_skills]

    if skill_ids:
        # 自定义场景通道：勾选的 Skill 叠加在系统预设之上（后写覆盖先写）。
        # 停用/不存在的 Skill 仍显式报错，杜绝静默丢弃。
        from ..models import RuleParseSkill

        requested = list(dict.fromkeys(skill_ids))
        skills = db.execute(
            select(RuleParseSkill).where(
                RuleParseSkill.id.in_(requested),
                (
                    (
                        (RuleParseSkill.tenant_id == tenant_id)
                        & (RuleParseSkill.rule_set_id == rule_set_id)
                    )
                    | (
                        RuleParseSkill.is_builtin.is_(True)
                        & RuleParseSkill.tenant_id.is_(None)
                    )
                ),
            ).order_by(
                # 固定合并顺序，避免同名键覆盖关系随查询计划漂移（与 compile_directive 一致）
                RuleParseSkill.is_builtin.desc(),
                RuleParseSkill.priority,
                RuleParseSkill.name,
            )
        ).scalars().all()
        by_id = {s.id for s in skills}
        missing = [sid for sid in requested if sid not in by_id]
        if missing:
            raise ValueError(f"指定的 Skill 不存在: {missing}")
        disabled = [s.name for s in skills if not s.enabled]
        if disabled:
            raise ValueError(f"以下 Skill 已停用，无法用于本次导入: {'、'.join(disabled)}")

        for s in skills:
            if s.id in baseline_ids:
                # 系统预设已包含（例如通用解析契约），不重复合并
                continue
            _merge_content(directive, s.content or {})
            applied_skills.append(s.name)
            logger.info("本次导入应用自定义 Skill: %s（priority=%s）", s.name, s.priority)

    # 场景解析（先算出来，供每条规则的 scenario 标签使用）
    from .rule_parse_engine import resolve_scenario

    if skill_ids:
        # 自定义场景通道：本次没有加载任何场景包，回显空场景
        # （前端据此不再显示场景标签，改为显示实际应用的 Skill 清单）
        resolved_scenario = None
    else:
        resolved_scenario = resolve_scenario(rule_set, scenario)

    result = import_rules_from_text(
        db,
        tenant_id,
        rule_set_id,
        raw_text,
        directive=directive,
        progress=progress,
        source_rows=source_rows,
        source_meta=source_meta,
        import_context=import_context,
        scenario=resolved_scenario,
    )

    if not skill_ids:
        from ..default_rule_parse_skill import known_scenarios

        if rule_set.use_default_skill and resolved_scenario not in known_scenarios():
            result["import_warnings"] = list(dict.fromkeys(
                list(result.get("import_warnings") or [])
                + [
                    f"业务场景「{resolved_scenario}」未登记场景知识包，"
                    "本次仅使用通用解析契约；如需领域口径请在导入文件声明已登记的场景键，"
                    "或由管理员新增场景知识包"
                ]
            ))
    result["scenario"] = resolved_scenario
    result["applied_skills"] = applied_skills

    # 导入完成后自动触发冲突检测
    if result.get("imported", 0) > 0:
        try:
            from .rule_conflict_detector import run_conflict_detection
            if progress is not None:
                update_task(progress, status="conflict", message="正在检测规则冲突…", conflict_found=0)
            conflict_result = run_conflict_detection(
                db,
                tenant_id,
                str(rule_set_id),
                progress=progress,
            )
            if progress is not None:
                update_task(progress, conflict_found=conflict_result.get("total_conflicts", 0))
            if conflict_result.get("detection_incomplete"):
                # 检测未跑完：不能把"没检出冲突"当成"冲突已消除"，明确告知用户
                logger.warning(
                    "导入后冲突检测未完成: %s 组失败 / 共 %s 组",
                    conflict_result.get("failed_groups"),
                    conflict_result.get("total_groups"),
                )
                result["import_warnings"] = list(dict.fromkeys(
                    list(result.get("import_warnings") or [])
                    + list(conflict_result.get("warnings") or [])
                    + ["冲突检测未完整完成，相关规则的既有冲突与状态保持不变，请稍后重试检测"]
                ))
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
