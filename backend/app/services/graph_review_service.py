"""图谱驱动的审查执行服务。

链路：
1. 从最新规则快照取 graph_id
2. 调用 Neo4j 查询 3 类关系（REQUIRED / MUST_STAMP / COMPARE_TO）
3. 对每条关系派发到对应 checker，比对文档 extracted_fields
4. 返回 ReviewResult 列表（与 review_service 兼容）

设计：
- 图谱是审查规则的"单一来源真相"
- 节点类型：Field / RequiredDoc / StampRequirement / CheckRoot
- 关系类型：
  - REQUIRED：source=CheckRoot("齐套性检查"), target=RequiredDoc(doc_type)
  - MUST_STAMP：source=StampRequirement(doc_type), target=CheckRoot("印章要求")
  - COMPARE_TO：source=Field("文件类型.字段名"), target=Field("文件类型.字段名")
                attributes.operator: 等于|不大于|不小于|时间早于|时间不晚于|总额等于|包含于
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Contract, Document, ReviewResult, RuleSnapshot
from ..neo4j_client import Neo4jClient, get_neo4j_client
from ..tenant_scope import tenant_get
from .contract_normalizer import extract_contract_numbers, normalize_contract_no
from .doc_normalizer import (
    is_currency_field,
    is_party_field,
    normalize_currency_value,
    normalize_field_name,
    normalize_party_name,
    strip_unit_qualifier,
)
from . import result_meta

logger = logging.getLogger(__name__)

# 签字/用印类字段标识词（P1：角色签字解析）
_SIGNATURE_FIELD_HINTS: tuple[str, ...] = (
    "签字", "签名", "签署", "签批", "盖章", "用印", "审批", "审核", "核准", "批准",
)
# 角色后缀：从「部门负责人签字」里取出角色词「部门负责人」
_SIGNATURE_ROLE_SUFFIXES: tuple[str, ...] = (
    "签字", "签名", "签署", "签批", "盖章", "用印", "审批", "审核", "核准",
)
# 签字栏里的否定表述：命中即视为"该角色未签字"，不得当作已具备
_SIGNATURE_NEGATIVE_HINTS: tuple[str, ...] = (
    "未签字", "无签字", "未签署", "未签", "缺签字", "待签",
    "未审批", "未审核", "未核准", "未批准",
    "未盖章", "无盖章", "未用印", "无印章", "未盖章", "缺章",
)
# 一个签字栏里可能并列多个角色（如「部门负责人 李强；财务负责人 未签字」）
_SIGNATURE_SEGMENT_RE = re.compile(r"[；;，,、\n\r]+")


# ============ 辅助：复用 review_service 的工具函数（避免循环依赖，就地实现） ============

def _docs_by_type(docs: list[Document], doc_type: str) -> list[Document]:
    return [d for d in docs if d.doc_type == doc_type]


def _first_doc_for_type(docs: list[Document], doc_type: str | None) -> Optional[Document]:
    """返回指定业务类型下的第一份文档，用于给审查结果绑定可打开原件。"""
    if not doc_type:
        return None
    typed = _docs_by_type(docs, doc_type)
    return typed[0] if typed else None


def _is_signature_field(field: str) -> bool:
    return any(h in field for h in _SIGNATURE_FIELD_HINTS)


def _signature_role(field: str) -> Optional[str]:
    """从签字类字段名提取角色词：部门负责人签字 → 部门负责人；财务负责人审批 → 财务负责人。"""
    s = normalize_field_name(field)
    for suffix in _SIGNATURE_ROLE_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s if _is_signature_field(s) and len(s) > 2 else None


def _role_segment(text: str, role: str) -> Optional[str]:
    """在签字栏原文里定位该角色所在的片段。

    「部门负责人 李强；财务负责人 未签字」+「财务负责人」→「财务负责人 未签字」
    """
    for seg in _SIGNATURE_SEGMENT_RE.split(text):
        if role in seg:
            return seg.strip()
    return None


def _segment_negative(seg: str) -> bool:
    """片段是否明确表述"未签字/未审批"（否定表述不得当作已具备）。"""
    return any(h in seg for h in _SIGNATURE_NEGATIVE_HINTS)


def iter_signature_roles(fields: dict) -> list[tuple[str, str]]:
    """从单据字段里解析"签字角色 → 签字原文"（忽略否定表述，如"财务负责人 未签字"）。

    与 _resolve_role_signature 共用同一套角色/片段/否定判定，保证"引擎认定已签"
    与"给大模型看的证据"永远一致。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in (fields or {}).items():
        name = normalize_field_name(key)
        if not name or name.startswith("__"):
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if not _is_signature_field(name):
            continue
        # 先看值里写明的角色（"部门负责人 李强；财务负责人 未签字" 才是真实签字角色），
        # 只有值里没写角色时才退回"从字段名推角色"（如「财务负责人签字」= 王芳）。
        found_in_value = False
        for seg in _SIGNATURE_SEGMENT_RE.split(text):
            seg = seg.strip()
            if not seg or _segment_negative(seg):
                continue
            for candidate_role in _signature_roles_in_segment(seg):
                found_in_value = True
                if candidate_role in seen:
                    continue
                seen.add(candidate_role)
                out.append((candidate_role, seg))
        if found_in_value:
            continue
        role = _signature_role(name)
        if role and role not in seen and not _segment_negative(text):
            seen.add(role)
            out.append((role, text))
    return out


_ROLE_SUFFIX_WORDS = ("签字", "签名", "签署", "审批", "审核", "盖章", "用印")
# 角色词特征：出现在片段首词的职务/身份后缀
_ROLE_WORD_HINTS = (
    "负责人", "经理", "主管", "总监", "主任", "董事", "专员", "会计", "出纳",
    "部长", "科长", "处长", "总裁", "法人", "代表", "经办人", "审批人", "审核人",
    "申请人", "签收人", "领款人", "经办", "验收",
)


def _signature_roles_in_segment(segment: str) -> list[str]:
    """片段形如"部门负责人 李强" → ["部门负责人"]；纯姓名片段（"王芳"）不算角色。

    判据：片段至少两段（角色 姓名），或首词本身带职务特征词。
    "审批签字=王芳" 这种值里没写角色的，由调用方回退到"从字段名推角色"。
    """
    tokens = [t for t in re.split(r"[\s　:：]+", segment.strip()) if t]
    if not tokens:
        return []
    head = tokens[0].strip()
    for word in _ROLE_SUFFIX_WORDS:
        if head.endswith(word) and len(head) > len(word):
            head = head[: -len(word)]
            break
    if not head or len(head) < 2:
        return []
    looks_like_role = len(tokens) >= 2 or any(
        hint in head or head.endswith(hint) for hint in _ROLE_WORD_HINTS
    )
    return [head] if looks_like_role else []


def _resolve_role_signature(doc: Document, field: str) -> Optional[str]:
    """签字类字段的语义解析（P1）：单据把角色写在签字栏的值里时按角色取到签字。

    背景（验收 BX-2026-0312）：报销单只有「审批签字=部门负责人 李强」一个签字栏，
    规则要求的是「部门负责人签字」。字面字段名不同 → 确定性引擎记 field_missing，
    规则停在「无法核验」。但"谁签了字"这一事实在单据里是确凿的：签字栏的值里
    写明了角色。此处只在**签字/用印类语义**内做角色匹配，不做通用模糊匹配：
    - 字段名含角色词（如「部门负责人」= 李强）→ 直接取值；
    - 字段名是签字/审批类且值里含角色词（如「审批签字」=「部门负责人 李强」）→ 取值。
    否定表述（「财务负责人 未签字」）不视为已签字——否则比"判不了"更糟（假通过）。
    取到的是原文片段，证据链可回溯。
    """
    s = normalize_field_name(field)
    if not _is_signature_field(s):
        return None
    role = _signature_role(s)
    if not role:
        return None
    fields = doc.extracted_fields or {}
    # P2：先用"学到的签字栏映射"直接取值（同一个新场景第二次出现即命中）
    learned_key = _learned_roles_for(doc.doc_type).get(role)
    if learned_key and learned_key in fields:
        text = str(fields.get(learned_key) or "").strip()
        if text:
            seg = _role_segment(text, role) or text
            if not _segment_negative(seg):
                return seg
    for other_key, other_val in fields.items():
        ok = normalize_field_name(other_key)
        if not ok or ok == s or ok.startswith("__"):
            continue
        if role in ok:  # 字段名本身就是角色（如「部门负责人」= 李强）
            text = str(other_val or "").strip()
            if not text:
                continue
            seg = _role_segment(text, role) or text
            if not _segment_negative(seg):
                return seg
    for found_role, signer in iter_signature_roles(fields):
        if found_role == role:
            return signer
    return None


def _get_field(doc: Document, key: str, aliases: list[str] | None = None):
    """从文档的 extracted_fields 取字段值，支持别名、单位限定后缀与复合算术表达式。

    取值顺序：精确键 → 显式别名 → 单位限定后缀（报销金额（元）→ 报销金额，
    存量文档/未登记字段的读取侧兜底）→ 角色签字解析 → 复合表达式求值。
    """
    fields = doc.extracted_fields or {}
    if key in fields and fields[key] is not None and str(fields[key]).strip():
        return fields[key]
    for alias in (aliases or []):
        if alias in fields and fields[alias] is not None and str(fields[alias]).strip():
            return fields[alias]
    # 单位限定后缀（批次 13）：规则写「报销金额」，单据抽到「报销金额（元）」
    key_base = strip_unit_qualifier(key)
    if key_base and key_base != normalize_field_name(key):
        if key_base in fields and fields[key_base] is not None and str(fields[key_base]).strip():
            return fields[key_base]
    for field_key, value in fields.items():
        if value is None or not str(value).strip():
            continue
        if key_base and strip_unit_qualifier(field_key) == key_base:
            return value
    # 签字类字段：按角色词在其它签字栏里找回签字事实（P1）
    signature = _resolve_role_signature(doc, key)
    if signature is not None:
        return signature
    # 复合字段表达式（如 "件数*数量"）：OCR 字段里不存在该字面名，
    # 按同一份文档的原子字段求值。取不到值时保持 None（由调用方记缺失）。
    value, _ = _eval_field_expression(doc, key)
    if value is not None:
        return value
    return None


# ---------- 复合字段表达式（A：图谱引擎表达式支持） ----------
# 规则导入侧会产出算术表达式断言字段（如「报关单数量 = 件数*数量」）。这类字段名
# 在单据 OCR 结果里不存在，只能按原子字段求值，否则永远判「字段数据缺失」。
# 拆词规则：`*`/`×` 无条件视为乘号（真实单据字段名不含这两个字符）；
# `+`/`-`/`/` 仅在两侧带空白时视为运算符，避免拆坏「订单号/合同号」这类字段名。
_EXPR_MUL_RE = re.compile(r"\s*[*×]\s*")
_EXPR_WS_RE = re.compile(r"\s+([+\-/－])\s+")
_EXPR_OP_ALIAS = {"×": "*", "－": "-"}
_EXPR_OPS = ("*", "/", "+", "-")
_NUMERIC_CURRENCY_PREFIX = "$¥￥€£"


def _to_number(value) -> Optional[float]:
    """宽容数值解析：OCR 常见写法 "1"、"1,234.5"、"￥1,234.50"、"12.5%" 都能落成 float。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace(" ", "").replace("％", "%")
    text = text.rstrip("%").lstrip(_NUMERIC_CURRENCY_PREFIX)
    try:
        return float(text)
    except ValueError:
        return None


def _split_field_expr(expr) -> Optional[list[str]]:
    """把复合字段表达式拆成 token 列表 [原子, 运算符, 原子, ...]；非表达式返回 None。"""
    if not isinstance(expr, str) or not expr:
        return None
    if not any(op in expr for op in ("*", "×", "+", "-", "－", "/")):
        return None
    tokens: list[str] = []
    for part in _EXPR_MUL_RE.split(expr):
        if tokens:
            tokens.append("*")
        segments = _EXPR_WS_RE.split(part)
        for idx, seg in enumerate(segments):
            seg = seg.strip()
            if not seg:
                return None
            if idx % 2:
                tokens.append(_EXPR_OP_ALIAS.get(seg, seg))
            else:
                tokens.append(seg)
    # 合法表达式：原子/运算符交替，至少两个原子
    if len(tokens) < 3 or len(tokens) % 2 == 0:
        return None
    if any(token in _EXPR_OPS for token in tokens[0::2]):
        return None
    if any(token not in _EXPR_OPS for token in tokens[1::2]):
        return None
    return tokens


def _eval_field_expression(doc: Document, expr) -> tuple[Optional[float], list[str]]:
    """对单份文档求复合字段表达式（先乘除后加减，不支持括号）。

    Returns (值 | None, 缺失或非数值的原子字段列表)。
    - 非表达式：返回 (None, [])
    - 表达式但原子字段缺失/非数值：返回 (None, [缺失原子...])
    """
    tokens = _split_field_expr(expr)
    if tokens is None:
        return None, []
    fields = doc.extracted_fields or {}
    missing: list[str] = []
    operands: list = []
    for token in tokens:
        if token in _EXPR_OPS:
            operands.append(token)
            continue
        num = _to_number(fields.get(token))
        if num is None:
            missing.append(token)
            operands.append(0.0)
        else:
            operands.append(num)
    if missing:
        return None, missing

    # 第一遍：乘除
    reduced: list = [operands[0]]
    idx = 1
    while idx < len(operands) - 1:
        op, right = operands[idx], operands[idx + 1]
        if op in ("*", "/"):
            left = reduced.pop()
            if op == "/" and right == 0:
                return None, []
            reduced.append(left * right if op == "*" else left / right)
        else:
            reduced.append(op)
            reduced.append(right)
        idx += 2

    # 第二遍：加减
    total = reduced[0]
    idx = 1
    while idx < len(reduced) - 1:
        op, right = reduced[idx], reduced[idx + 1]
        total = total + right if op == "+" else total - right
        idx += 2
    return float(total), []


def _field_missing_atoms(doc: Document, field: str) -> list[str]:
    """复合表达式里缺失/非数值的原子字段；非表达式或可求值 → []。"""
    _, missing = _eval_field_expression(doc, field)
    return missing


def _amount_diff_pct(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else 100.0
    return abs(actual - expected) / expected * 100.0


def _compare_dates(
    earlier: Optional[str],
    later: Optional[str],
    allow_same_day: bool = True,
) -> str:
    if earlier is None or later is None:
        return "unverifiable"
    try:
        d1 = datetime.fromisoformat(str(earlier)).date()
        d2 = datetime.fromisoformat(str(later)).date()
    except Exception:
        return "unverifiable"
    if d1 < d2:
        return "pass"
    if d1 == d2:
        return "pass" if allow_same_day else "fail"
    return "fail"


# ============ 结果构造（不依赖 Rule 对象，仅靠图谱属性） ============

def _make_result_from_graph(
    rel_props: dict,
    doc: Optional[Document],
    result: str,
    issue_desc: str = "",
    detail: Optional[dict] = None,
    suggestion: str = "",
    source_node: Optional[str] = None,
    target_node: Optional[str] = None,
) -> ReviewResult:
    """根据图谱关系属性构造 ReviewResult。

    rel_props 包含 rule_id, rule_text, doc_type, check_category。
    批次 9：结果带问题状态（C1）、严重度/偏离度（C2）、图谱实体关联（C1）。
    """
    status = result_meta.default_status(result)
    detail = dict(detail or {})
    # rule_id 落进证据（统一判定的意图/初判按 (rule_id, 节点对) 对齐，不能只藏在 evidence 里）
    if rel_props.get("rule_id") and not detail.get("rule_id"):
        detail["rule_id"] = rel_props.get("rule_id")
    severity, deviation = result_meta.compute_severity(
        result, rel_props.get("check_category"), detail
    )
    return ReviewResult(
        rule_id=None,  # 图谱不再回写 rule_id（规则文本只是元数据）
        rule_text=rel_props.get("rule_text"),
        doc_type=rel_props.get("doc_type"),
        check_category=rel_props.get("check_category"),
        doc_id=doc.id if doc else None,
        result=result,
        status=status,
        status_history=[
            {
                "status": status,
                "at": datetime.now().isoformat(timespec="seconds"),
                "by": "system",
                "note": "审查生成",
            }
        ],
        severity=severity,
        deviation=deviation,
        graph_source=source_node,
        graph_target=target_node,
        # 批次 10 Phase C：双引擎结果来源标记
        source="graph",
        issue_desc=issue_desc or None,
        detail=detail,
        suggestion=suggestion or None,
    )


# ============ 主入口 ============

def run_graph_review(
    db: Session,
    tenant_id: uuid.UUID,
    contract: Contract,
    docs: list[Document],
    neo4j: Optional[Neo4jClient] = None,
    snapshot_id: Optional[uuid.UUID] = None,
) -> list[ReviewResult]:
    """图谱驱动审查主入口。

    Args:
        db: 数据库会话（用于查最新快照）
        contract: 合同对象
        docs: 合同下所有文档
        neo4j: Neo4j 客户端（不传则用全局单例）

    Returns:
        ReviewResult 列表

    Raises:
        ValueError: 没有可用图谱时抛出（调用方可 fallback）
    """
    neo4j = neo4j or get_neo4j_client()
    if contract.tenant_id != tenant_id:
        raise ValueError("合同不属于当前租户")

    # 1. 取快照的 graph_id：
    #    - 传入 snapshot_id 时优先使用（并校验属于当前规则集，防止跨规则集用错图）
    #    - 未传时按 contract.rule_set_id 取最新快照
    from sqlalchemy import select
    if snapshot_id is not None:
        snapshot = tenant_get(db, RuleSnapshot, snapshot_id, tenant_id)
        if snapshot is None or not snapshot.graph_id:
            raise ValueError(f"图谱快照不存在或未构建: {snapshot_id}")
        if snapshot.rule_set_id != contract.rule_set_id:
            raise ValueError("快照不属于当前合同所属规则集")
    else:
        stmt = (
            select(RuleSnapshot)
            .where(
                RuleSnapshot.tenant_id == tenant_id,
                RuleSnapshot.rule_set_id == contract.rule_set_id,
            )
            .order_by(RuleSnapshot.snapshot_time.desc())
            .limit(1)
        )
        snapshot = db.execute(stmt).scalars().first()
        if snapshot is None or not snapshot.graph_id:
            raise ValueError("无可用图谱快照，请先构建规则图谱")
    graph_id = snapshot.graph_id
    logger.info(
        "图谱驱动审查: graph_id=%s, rule_set_id=%s, 合同=%s, 文档数=%d",
        graph_id, contract.rule_set_id, contract.contract_no, len(docs),
    )

    results: list[ReviewResult] = []

    # 2. REQUIRED 关系 → 齐套性检查
    try:
        required_rels = neo4j.get_required_docs(graph_id, tenant_id)
        results.extend(_check_required_docs(required_rels, docs))
    except Exception as e:
        logger.error("齐套性图谱查询失败: %s", e, exc_info=True)

    # 2.5 MUST_EXIST 关系 → 存在性/齐备要求（P3：签字、盖章、必备材料）
    try:
        exist_rels = neo4j.get_exist_requirements(graph_id, tenant_id)
        results.extend(_check_exist_requirements(exist_rels, docs))
    except Exception as e:
        logger.error("存在性要求图谱查询失败: %s", e, exc_info=True)

    # 3. MUST_STAMP 关系 → 印章检查
    try:
        stamp_rels = neo4j.get_stamp_requirements(graph_id, tenant_id)
        results.extend(_check_stamp_requirements(stamp_rels, docs))
    except Exception as e:
        logger.error("印章图谱查询失败: %s", e, exc_info=True)

    # 4. COMPARE_TO 关系 → 字段比对（信息准确性 / 时间逻辑）
    try:
        compare_rels = neo4j.get_compare_relationships(graph_id, tenant_id)
        results.extend(_check_compare_relationships(compare_rels, docs, contract))
    except Exception as e:
        logger.error("字段比对图谱查询失败: %s", e, exc_info=True)

    logger.info("图谱审查完成: 共生成 %d 条结果", len(results))
    return results


# ============ 检查1：齐套性（REQUIRED 关系） ============

def _check_required_docs(
    rels: list[dict],
    docs: list[Document],
) -> list[ReviewResult]:
    """齐套性检查：对每条 REQUIRED 关系，检查 target(doc_type) 是否在 docs 中。

    rels 形如:
      [{"source": "齐套性检查", "target": "代理协议",
        "rel_props": {"rule_id": "R001", "rule_text": "...", "doc_type": "代理协议", "check_category": "齐套性"}, ...}]
    """
    results: list[ReviewResult] = []
    present_types = {d.doc_type for d in docs}

    # 去重：同一 doc_type 可能有多条 REQUIRED 关系，只取第一条
    seen: set[str] = set()
    for rec in rels:
        rel_props = rec.get("rel_props", {}) or {}
        doc_type = rel_props.get("doc_type") or rec.get("target")
        if not doc_type or doc_type in seen:
            continue
        seen.add(doc_type)

        if doc_type in present_types:
            results.append(_make_result_from_graph(
                rel_props,
                _first_doc_for_type(docs, doc_type),
                "pass",
                detail={"doc_type": doc_type},
                source_node=rec.get("source"), target_node=rec.get("target"),
            ))
        else:
            results.append(_make_result_from_graph(
                rel_props, None, "fail",
                issue_desc=f"缺失必备文件：{doc_type}",
                detail={"missing_doc_type": doc_type},
                source_node=rec.get("source"), target_node=rec.get("target"),
            ))
    return results


# ============ 检查1.5：存在性/齐备要求（MUST_EXIST 关系，P3） ============

def _check_exist_requirements(
    rels: list[dict],
    docs: list[Document],
) -> list[ReviewResult]:
    """存在性要求检查：规则形如"…应同时具备 A 与 B 签字/盖章/材料"。

    rels 形如:
      [{"source": "存在性检查", "target": "报销单.部门负责人签字",
        "rel_props": {"rule_id": "R0016", "doc_type": "报销单", "must": "signed",
                      "condition": {...}, "rule_text": "..."}}]

    判定顺序（与 COMPARE_TO 的意图链一致）：
    1. 触发条件预检：not_met → pass + skip（规则不适用）；unknown → unverifiable；
    2. 存在性判定：任一该类型单据具备该事实 → pass；全部缺失 → fail（要求未满足）；
       该类型单据本身不存在 → 跳过（齐套性负责）。
    """
    results: list[ReviewResult] = []
    for rec in rels:
        rel_props = _props_dict(rec.get("rel_props") or {})
        target_node = str(rec.get("target") or "")
        doc_type, field, _aggregate = _parse_field_node(target_node)
        doc_type = doc_type or str(rel_props.get("doc_type") or "")
        if not doc_type or not field:
            continue
        typed = _docs_by_type(docs, doc_type)
        if not typed:
            continue  # 缺单据由齐套性规则负责，这里不重复报

        detail: dict = {
            "reason": "field_data_missing",
            "required": {"doc_type": doc_type, "field": field, "must": rel_props.get("must") or "present"},
            "intent_chain": ["existence_check"],
        }

        # 1) 触发条件预检
        condition = _props_dict(rel_props.get("condition") or {}) or None
        if condition:
            cond_status, cond_detail = _evaluate_condition(condition, docs, doc_type)
            detail["condition"] = cond_detail
            if cond_status == "not_met":
                results.append(_make_result_from_graph(
                    rel_props, typed[0], "pass",
                    issue_desc=f"条件未满足，跳过存在性要求：{cond_detail.get('text') or ''}",
                    detail={
                        **detail,
                        "condition_met": False,
                        "skipped_reason": "condition_not_met",
                        "intent_chain": ["condition_precheck", "skip"],
                    },
                    source_node=rec.get("source"), target_node=target_node,
                ))
                continue
            if cond_status == "unknown":
                results.append(_make_result_from_graph(
                    rel_props, typed[0], "unverifiable",
                    issue_desc="条件字段缺失，无法判断规则是否适用",
                    detail={
                        **detail,
                        "condition_met": None,
                        "missing_fields": cond_detail.get("missing_fields", []),
                        "intent_chain": ["condition_precheck", "blocked"],
                    },
                    source_node=rec.get("source"), target_node=target_node,
                ))
                continue
            detail["condition_met"] = True

        # 2) 存在性判定（_get_field 内建单位后缀等价 + 签字角色解析 + 学到的角色映射）
        present: list[dict] = []
        for doc in typed:
            value = _get_field(doc, field)
            if value is not None and str(value).strip():
                present.append({"doc_id": str(doc.id), "doc_name": doc.file_name, "value": value})
        detail["evidence"] = {
            "requirement": detail["required"],
            "docs": present,
            "missing_fields": (
                []
                if present
                else [{"doc_type": doc_type, "field": field, "reason": "field_missing",
                       "doc_files": [d.file_name for d in typed]}]
            ),
        }
        if present:
            results.append(_make_result_from_graph(
                rel_props, typed[0], "pass", detail=detail,
                source_node=rec.get("source"), target_node=target_node,
            ))
        else:
            results.append(_make_result_from_graph(
                rel_props, typed[0], "fail",
                issue_desc=f"{doc_type} 缺少要求的事实：{field}",
                detail=detail,
                source_node=rec.get("source"), target_node=target_node,
            ))
    return results


# ============ 检查2：印章（MUST_STAMP 关系） ============
def _check_stamp_requirements(
    rels: list[dict],
    docs: list[Document],
) -> list[ReviewResult]:
    """印章检查：对每条 MUST_STAMP 关系，检查 source(doc_type) 对应的文档是否有印章。

    rels 形如:
      [{"source": "代理协议", "target": "印章要求",
        "rel_props": {"rule_id": "R005", "rule_text": "...", "doc_type": "代理协议", "check_category": "基础判断"}, ...}]
    """
    results: list[ReviewResult] = []
    # 按 doc_type 去重
    seen: set[str] = set()
    for rec in rels:
        rel_props = rec.get("rel_props", {}) or {}
        doc_type = rel_props.get("doc_type") or rec.get("source")
        if not doc_type or doc_type in seen:
            continue
        seen.add(doc_type)

        target_docs = _docs_by_type(docs, doc_type)
        if not target_docs:
            # 该类型未提供，跳过（不报错，齐套性会兜底）
            continue

        for doc in target_docs:
            if doc.ocr_status == "failed":
                results.append(_make_result_from_graph(
                    rel_props, doc, "unverifiable",
                    issue_desc=f"{doc.file_name} OCR 失败，无法判断印章",
                    detail={"reason": "ocr_failed"},
                    source_node=rec.get("source"), target_node=rec.get("target"),
                ))
            elif doc.has_stamp is True:
                results.append(_make_result_from_graph(
                    rel_props, doc, "pass", detail={"has_stamp": True},
                    source_node=rec.get("source"), target_node=rec.get("target"),
                ))
            elif doc.has_stamp is False:
                results.append(_make_result_from_graph(
                    rel_props, doc, "fail",
                    issue_desc=f"{doc.file_name} 未检测到印章",
                    detail={"has_stamp": False, "required": rel_props.get("rule_text", "")},
                    source_node=rec.get("source"), target_node=rec.get("target"),
                ))
            else:
                results.append(_make_result_from_graph(
                    rel_props, doc, "unverifiable",
                    issue_desc=f"{doc.file_name} 印章无法判断（OCR 置信度低）",
                    detail={"has_stamp": None, "reason": "low_confidence"},
                    source_node=rec.get("source"), target_node=rec.get("target"),
                ))
    return results


# ============ 检查3：字段比对（COMPARE_TO 关系） ============

def _check_compare_relationships(
    rels: list[dict],
    docs: list[Document],
    contract: Contract,
) -> list[ReviewResult]:
    """字段比对检查：意图链驱动（E1）——条件预检 → 断言比对 → 例外豁免 → 结果。

    rels 形如:
      [{"source": "代理协议.协议方", "target": "委托出口确认单.委托方",
        "rel_props": {"operator": "等于", "tolerance": 0, "rule_id": "R010",
                      "rule_text": "...", "doc_type": "代理协议",
                      "check_category": "信息准确性"}, ...}]
    批次 8：
    - 8-1 每条 COMPARE_TO 走 _run_intent_chain（条件/断言/例外/证据）
    - 8-2 聚合语义优先取 Value 节点声明（source_props/target_props），关系/节点名兜底
    - 8-4 容差统一挂到 Value 节点，图谱引擎不再读取全局 5%
    """
    results: list[ReviewResult] = []

    for rec in rels:
        rel_props = rec.get("rel_props", {}) or {}
        source_name = rec.get("source")
        target_name = rec.get("target")
        if not source_name or not target_name:
            continue

        result, issue_desc, detail = _run_intent_chain(
            rel_props=rel_props,
            source_props=rec.get("source_props", {}) or {},
            target_props=rec.get("target_props", {}) or {},
            source_name=source_name,
            target_name=target_name,
            docs=docs,
            contract=contract,
        )
        # 跨文件比对结果虽涉及 source/target 两类文件，但“原件对照”入口绑定
        # 规则的主文件类型（通常为 source/rule.doc_type），避免操作栏误显示“无原件”。
        primary_doc_type = rel_props.get("doc_type") or _parse_field_node(source_name)[0]
        results.append(_make_result_from_graph(
            rel_props,
            _first_doc_for_type(docs, primary_doc_type),
            result,
            issue_desc=issue_desc,
            detail=detail,
            source_node=source_name, target_node=target_name,
        ))
    return results


def _parse_field_node(node_name: str) -> tuple[str, str, Optional[str]]:
    """解析 '文件类型.字段名' 或 '文件类型.字段名|SUM|ANY|ALL' 为 (doc_type, field, aggregate)。"""
    aggregate: Optional[str] = None
    base = node_name
    if "|" in node_name:
        base, _, agg = node_name.partition("|")
        agg = agg.strip().upper()
        if agg in ("SUM", "ANY", "ALL"):
            aggregate = agg
    if "." in base:
        doc_type, _, field = base.partition(".")
        return doc_type.strip(), field.strip(), aggregate
    return base.strip(), "", aggregate


# ============ 批次 8：意图链执行器（E1） ============

def _run_intent_chain(
    rel_props: dict,
    source_props: dict,
    target_props: dict,
    source_name: str,
    target_name: str,
    docs: list[Document],
    contract: Contract,
) -> tuple[str, str, dict]:
    """意图链驱动比对（E1）：条件预检 → 币别预检 → 断言比对 → 例外豁免 → 结果（带证据）。
    Returns (result, issue_desc, detail)。result ∈ pass/fail/unverifiable。
    """
    src_doc_type, src_field, src_agg = _parse_field_node(source_name)
    tgt_doc_type, tgt_field, tgt_agg = _parse_field_node(target_name)
    if not src_doc_type or not tgt_doc_type:
        return "unverifiable", f"比对节点无法解析：{source_name} / {target_name}", {"reason": "node_unparseable"}

    # 9-3：证据链——规则→条件→字段→文档 完整来源（所有分支均携带）
    evidence = _build_evidence(
        rel_props, docs,
        src_doc_type, src_field, tgt_doc_type, tgt_field,
        source_name, target_name,
    )

    # 8-2：聚合语义以 Value 节点声明优先（图谱下沉），关系/节点名后缀兜底（旧图）
    aggregate = str(
        source_props.get("aggregate")
        or target_props.get("aggregate")
        or rel_props.get("aggregate")
        or src_agg
        or tgt_agg
        or ""
    ).upper()
    if aggregate not in ("SUM", "ANY", "ALL"):
        aggregate = None

    operator = str(rel_props.get("operator", "等于")).strip()

    # 8-4：容差统一——Value 节点 tolerance_params/tolerance 优先，关系属性兜底（旧图）
    # Neo4j 属性不支持嵌套结构：写入侧序列化为 JSON 字符串，读取侧反序列化
    tolerance_params = _props_dict(
        source_props.get("tolerance_params")
        or target_props.get("tolerance_params")
        or rel_props.get("tolerance_params")
        or {}
    )
    node_tolerance = source_props.get("tolerance")
    if node_tolerance is None:
        node_tolerance = target_props.get("tolerance")
    tolerance = float(
        node_tolerance if node_tolerance is not None else (rel_props.get("tolerance", 0) or 0)
    )

    # E1-1：条件预检（condition 未满足 → 规则不适用 → pass + 证据；条件字段缺失 → unverifiable）
    condition = _props_dict(
        rel_props.get("condition") or source_props.get("condition") or target_props.get("condition") or {}
    ) or None
    cond_doc_type = rel_props.get("doc_type") or src_doc_type
    cond_evidence: Optional[dict] = None
    if condition:
        cond_status, cond_detail = _evaluate_condition(condition, docs, cond_doc_type)
        if cond_status == "not_met":
            return (
                "pass",
                f"条件未满足，跳过断言比对：{cond_detail.get('text') or ''}",
                {
                    "condition_met": False,
                    "skipped_reason": "condition_not_met",
                    "condition": cond_detail,
                    "intent_chain": ["condition_precheck", "skip"],
                    "evidence": evidence,
                },
            )
        if cond_status == "unknown":
            return (
                "unverifiable",
                "条件字段缺失，无法判断规则是否适用",
                {
                    "reason": cond_detail.get("reason", "condition_field_missing"),
                    "condition": cond_detail,
                    "missing_fields": cond_detail.get("missing_fields", []),
                    "intent_chain": ["condition_precheck", "blocked"],
                    "evidence": evidence,
                },
            )
        cond_evidence = cond_detail

    # E1-2：币别预检（批次 7-4 保留，并入意图链证据）
    currency = rel_props.get("currency")
    # 若条件已对源文档的币别字段做过约束（如"报关单币种为美元"），
    # 币别预检即为冗余且可能误判（如人民币发票上的"共计美元"折算值），跳过。
    condition_gates_currency = bool(
        isinstance(condition, dict)
        and str(condition.get("field") or "").strip() in ("币别", "币种", "币制", "货币")
    )
    if currency and not condition_gates_currency:
        cur_res = _check_currency(currency, src_doc_type, tgt_doc_type, docs)
        if cur_res == "fail":
            return (
                "fail",
                f"币别不一致：{src_doc_type}/{tgt_doc_type} 币别与规则要求 [{currency}] 不符",
                {
                    "required_currency": currency,
                    "src_doc_type": src_doc_type,
                    "tgt_doc_type": tgt_doc_type,
                    "intent_chain": ["currency_precheck", "fail"],
                    "evidence": evidence,
                },
            )
        if cur_res == "unverifiable":
            return (
                "unverifiable",
                f"币别无法核验：{src_doc_type} 或 {tgt_doc_type} 缺少币别字段",
                {
                    "reason": "currency_missing",
                    "required_currency": currency,
                    "missing_fields": _collect_missing(docs, [(src_doc_type, "币别"), (tgt_doc_type, "币别")]),
                    "intent_chain": ["currency_precheck", "blocked"],
                    "evidence": evidence,
                },
            )

    # E1-3：断言比对（复用 operator 派发）
    result, issue_desc, detail = _dispatch_compare(
        operator=operator,
        src_doc_type=src_doc_type, src_field=src_field,
        tgt_doc_type=tgt_doc_type, tgt_field=tgt_field,
        docs=docs,
        tolerance=tolerance,
        tolerance_params=tolerance_params,
        contract=contract,
        aggregate=aggregate,
    )
    chain = detail.setdefault("intent_chain", [])
    if "assertion_compare" not in chain:
        chain.insert(0, "assertion_compare")
    # 断言算子/字段落进证据：LLM 兜底裁决需要据此识别"算子与字段语义不匹配"的
    # 建模缺陷（如「不小于」比较报销金额与部门负责人签字），并按规则文本重解。
    detail.setdefault("operator", operator)
    detail.setdefault("source_field", src_field)
    detail.setdefault("target_field", tgt_field)
    detail.setdefault("source_doc_type", src_doc_type)
    detail.setdefault("target_doc_type", tgt_doc_type)
    if cond_evidence is not None:
        detail["condition_met"] = True
        detail["condition"] = cond_evidence
        if "condition_precheck" not in chain:
            chain.insert(0, "condition_precheck")
    detail["evidence"] = evidence

    # E1-4：例外豁免——仅当断言 fail 时尝试；命中结构化例外 → pass（带证据），
    # 纯文本例外无法程序化核验 → 保留 fail 并写明"需人工确认"。
    if result == "fail":
        exceptions = _props_list(
            rel_props.get("exceptions") or source_props.get("exceptions") or target_props.get("exceptions") or []
        )
        if exceptions:
            exempted, evidence = _try_exempt(exceptions, docs, cond_doc_type)
            detail["exceptions_considered"] = True
            detail["exception_evidence"] = evidence
            if exempted:
                return (
                    "pass",
                    "命中例外条款，豁免本项比对",
                    {
                        **detail,
                        "exempted": True,
                        "original_result": "fail",
                        "exception": exempted,
                    },
                )
            detail["exception_applied"] = False
            if any(e.get("evaluable") is False for e in evidence):
                detail["exception_manual_review"] = True
    return result, issue_desc, detail


def _evaluate_condition(
    condition: dict,
    docs: list[Document],
    cond_doc_type: str,
) -> tuple[str, dict]:
    """条件预检（E1-1）。condition: {"text","field","operator","value"}。
    Returns (status, evidence)，status ∈ "met" | "not_met" | "unknown"。
    - met：条件成立，继续断言比对
    - not_met：条件不成立，规则不适用（pass + 跳过）
    - unknown：条件字段缺失/纯文本条件无法核验（unverifiable，防空满足）
    """
    if not isinstance(condition, dict):
        return "unknown", {"reason": "condition_invalid", "condition": condition}
    field = condition.get("field")
    text = condition.get("text") or ""
    if not field:
        # 纯文本条件（无结构化字段）→ 无法程序化核验，视为 unknown（防空满足）
        return "unknown", {
            "reason": "condition_text_only",
            "text": text,
            "missing_fields": [],
        }
    operator = str(condition.get("operator") or "等于").strip()
    value = condition.get("value")
    values, missing = _collect_field_evidence(docs, cond_doc_type, field)
    evidence: dict = {
        "text": text,
        "field": field,
        "operator": operator,
        "value": value,
        "cond_doc_type": cond_doc_type,
        "values": [v for _, v in values],
    }
    if not values:
        return "unknown", {
            **evidence,
            "reason": "condition_field_missing",
            "missing_fields": missing,
        }
    met, ok = _evaluate_criterion({"field": field, "operator": operator, "value": value}, [v for _, v in values])
    if not ok:
        return "unknown", {**evidence, "reason": "condition_unparseable"}
    return ("met" if met else "not_met"), evidence


def _evaluate_criterion(criterion: dict, values: list) -> tuple[Optional[bool], bool]:
    """求值字段级判据（条件/结构化例外共用）。
    criterion: {"field","operator","value"}；values: 已采集的字段值列表。
    Returns (met, ok)——ok=False 表示无法求值（值不可解析），防空满足。
    """
    operator = str(criterion.get("operator") or "等于").strip()
    value = criterion.get("value")
    if operator == "存在":
        return (bool(values), True)
    if operator in ("为空", "为空值"):
        return (not bool(values), True)
    # OCR 多行字段会以 ";" 拼接（如 币别="美元;美元"、单价="0.5459;1.4025"）。
    # 条件求值前先展开为独立值，避免"美元;美元"与"美元"整体比较误判为条件不满足。
    values = _expand_multi_values(values)
    # 币别类字段：值级别名（USD/美金 等）按注册表归一后再比较（批次 12）
    if is_currency_field(criterion.get("field")):
        values = [normalize_currency_value(v) or v for v in values]
        value = normalize_currency_value(value) if value is not None else value
    if not values or value is None:
        return (None, False)
    try:
        v_num = float(value)
        nums = [float(v) for v in values]
        numeric = True
    except (TypeError, ValueError):
        numeric = False

    if operator == "等于":
        if numeric:
            return (any(abs(n - v_num) < 1e-9 for n in nums), True)
        return (any(str(v).strip() == str(value).strip() for v in values), True)
    if operator == "不等于":
        if numeric:
            return (all(abs(n - v_num) >= 1e-9 for n in nums), True)
        return (all(str(v).strip() != str(value).strip() for v in values), True)
    if operator == "包含":
        needle = str(value)
        return (any(needle in str(v) for v in values), True)
    if operator == "包含于":
        haystack = value if isinstance(value, (list, tuple, set)) else [value]
        return (any(any(str(v) == str(h) for h in haystack) for v in values), True)
    if operator in ("大于", "小于", "不大于", "不小于"):
        if not numeric:
            return (None, False)
        if operator == "大于":
            return (any(n > v_num for n in nums), True)
        if operator == "小于":
            return (any(n < v_num for n in nums), True)
        if operator == "不大于":
            return (any(n <= v_num for n in nums), True)
        return (any(n >= v_num for n in nums), True)
    return (None, False)


def _expand_multi_values(values: list) -> list:
    """展开 OCR 多行字段的 ";" 拼接值（支持中英文分号），空片段丢弃。

    仅拆分字符串；数字/日期等非字符串原样保留。
    """
    out: list = []
    for v in values:
        if isinstance(v, str):
            for part in v.replace("；", ";").split(";"):
                part = part.strip()
                if part:
                    out.append(part)
        else:
            out.append(v)
    return out


def _try_exempt(
    exceptions: list,
    docs: list[Document],
    doc_type: str,
) -> tuple[Optional[dict], list[dict]]:
    """例外豁免（E1-4）。exceptions: [{"text","reason", 可选 field/operator/value}]。
    Returns (exempted_exception | None, evidence)。
    - 结构化例外（含 field/operator/value）可程序化求值，命中即豁免
    - 纯文本例外无法核验 → evidence 标记 evaluable=False，保留 fail 待人工确认
    """
    evidence: list[dict] = []
    if not isinstance(exceptions, list):
        return None, evidence
    for exc in exceptions:
        if not isinstance(exc, dict):
            continue
        entry: dict = {
            "text": exc.get("text"),
            "reason": exc.get("reason"),
        }
        if exc.get("field") and exc.get("value") is not None:
            values, missing = _collect_field_evidence(docs, doc_type, exc["field"])
            met, ok = _evaluate_criterion(exc, [v for _, v in values])
            entry["evaluable"] = True
            entry["applied"] = bool(met) if ok else None
            entry["field"] = exc.get("field")
            entry["operator"] = exc.get("operator")
            entry["value"] = exc.get("value")
            entry["values"] = [v for _, v in values]
            entry["missing_fields"] = missing
            evidence.append(entry)
            if ok and met:
                return entry, evidence
        else:
            entry["evaluable"] = False
            entry["applied"] = None
            entry["note"] = "纯文本例外，无法程序化核验，需人工确认"
            evidence.append(entry)
    return None, evidence


def _dispatch_compare(
    operator: str,
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    tolerance: float,
    tolerance_pct: Optional[float] = None,
    contract: Optional[Contract] = None,
    tolerance_params: Optional[dict] = None,
    aggregate: Optional[str] = None,
) -> tuple[str, str, dict]:
    """根据 operator 派发到对应的比对逻辑。返回 (result, issue_desc, detail)。"""
    op = operator.strip()
    # 日期间隔容差（天）：规则形如"…的间隔不得超过30日"时由规则解析写入
    # tolerance_params.time_days。此前该参数只落库不生效（批次 13 修复）。
    time_days = 0
    if isinstance(tolerance_params, dict):
        try:
            time_days = max(0, int(float(tolerance_params.get("time_days") or 0)))
        except (TypeError, ValueError):
            time_days = 0

    if op == "等于":
        return _cmp_eq(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, aggregate)
    if op == "不大于":
        return _cmp_numeric(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, "le", tolerance, tolerance_params, aggregate)
    if op == "不小于":
        return _cmp_numeric(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, "ge", tolerance, tolerance_params, aggregate)
    if op == "时间早于":
        return _cmp_date(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, allow_same_day=False, aggregate=aggregate, tolerance_days=time_days)
    if op == "时间晚于":
        return _cmp_date_reversed(
            src_doc_type, src_field, tgt_doc_type, tgt_field, docs,
            allow_same_day=False, aggregate=aggregate, tolerance_days=time_days,
        )
    if op == "时间不晚于":
        return _cmp_date(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, allow_same_day=True, aggregate=aggregate, tolerance_days=time_days)
    if op == "时间不早于":
        return _cmp_date_reversed(
            src_doc_type, src_field, tgt_doc_type, tgt_field, docs,
            allow_same_day=True, aggregate=aggregate, tolerance_days=time_days,
        )
    if op == "总额等于":
        return _cmp_total_eq(
            src_doc_type, src_field, tgt_doc_type, tgt_field, docs,
            tolerance_pct=tolerance_pct, tolerance_params=tolerance_params,
        )
    if op == "包含于":
        return _cmp_contains(src_doc_type, src_field, tgt_doc_type, tgt_field, docs)

    # 未知 operator 不能静默降级成字符串相等，否则日期/数量规则会被错误
    # 地当作标签比较。返回 unverifiable，让规则解析或人工复核修复算子。
    logger.warning("未知 operator: %s，判定为无法核验", op)
    return (
        "unverifiable",
        f"不支持的比较算子：{op or '<空>'}",
        {"reason": "unsupported_operator", "operator": op},
    )


# ---------- operator 实现 ----------

def _cmp_eq(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    aggregate: Optional[str] = None,
) -> tuple[str, str, dict]:
    """相等比较（多单汇总语义）。

    1-3：分批付款场景下同类型可能存在多份文档。
    - 两侧值均可解析为数值 → 按总和比较（严格相等）
    - 否则按字符串集合比较：任一侧的任一值匹配另一侧的任一值即通过
    批次 7-2：aggregate=ALL 时要求两侧集合完全一致；ANY/None 保持任一匹配。
    """
    src_pairs, src_missing = _collect_field_evidence(docs, src_doc_type, src_field)
    tgt_pairs, tgt_missing = _collect_field_evidence(docs, tgt_doc_type, tgt_field)
    src_vals = [v for _, v in src_pairs]
    tgt_vals = [v for _, v in tgt_pairs]
    if not src_vals or not tgt_vals:
        # 8-3：防空满足——数据缺失显式区分为 unverifiable，携带字段级缺失清单
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 字段数据缺失",
            {
                "reason": "field_data_missing",
                "missing_fields": _collect_missing(docs, [(src_doc_type, src_field), (tgt_doc_type, tgt_field)]),
            },
        )
    # SUM/default comparisons need every contributing document. With ANY, one
    # missing document cannot invalidate a positive match from another document.
    if aggregate != "ANY" and (src_missing or tgt_missing):
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 存在不完整数据",
            {
                "reason": "incomplete_field_data",
                "missing_fields": src_missing + tgt_missing,
            },
        )

    # 同类型同字段自比对 = 多文档一致性校验（如"两个订单文档的数量必须一致"）。
    # 源/目标采集自同一字段集合，跨集比较恒等（求和/任一匹配都恒 pass，会把
    # 25000 vs 100 这类真实不一致漏掉）；正确语义是集合内所有值一致，且任一份
    # 文档缺失该字段都按"数据缺失"处理（防空满足）。
    if src_doc_type == tgt_doc_type and src_field == tgt_field:
        values, missing = _collect_field_evidence(docs, src_doc_type, src_field)
        if missing or not values:
            return (
                "unverifiable",
                f"{src_doc_type}.{src_field} 字段数据缺失",
                {
                    "reason": "field_data_missing",
                    "missing_fields": missing
                    or _collect_missing(docs, [(src_doc_type, src_field)]),
                },
            )
        vals = [v for _, v in values]
        detail = {"src_vals": vals, "tgt_vals": vals, "self_consistency": True}
        nums = _to_numeric_list(vals)
        if nums is not None:
            if max(nums) - min(nums) < 1e-9:
                return "pass", "", detail
            return (
                "fail",
                f"{src_doc_type} 多份文档的{src_field}不一致: {vals}",
                detail,
            )
        # 主体类字段（代收方/承运人/收货方等）多文档一致性：公司核心名归一判等，
        # 吸收 '公司名, 完整地址' vs '公司名, 法律形式后缀' 这类详略/格式差异，
        # 避免 "Example International, ...Mexico" vs "Example International, SA CV" 假阳性。
        if is_party_field(src_field) or is_party_field(tgt_field):
            party_norms = [normalize_party_name(v) for v in vals]
            if party_norms and all(n is not None for n in party_norms):
                detail["party_name_norm"] = party_norms
                if len(set(party_norms)) == 1:
                    return "pass", "", detail
        strs = {str(v).strip() for v in vals}
        if len(strs) <= 1:
            return "pass", "", detail
        return (
            "fail",
            f"{src_doc_type} 多份文档的{src_field}不一致: {vals}",
            detail,
        )

    # 两侧全部为数值 → 按总和比较
    src_nums, tgt_nums = _to_numeric_list(src_vals), _to_numeric_list(tgt_vals)
    if src_nums is not None and tgt_nums is not None:
        src_sum = sum(src_nums)
        tgt_sum = sum(tgt_nums)
        detail = {"src_vals": src_vals, "tgt_vals": tgt_vals, "src_sum": src_sum, "tgt_sum": tgt_sum}
        if abs(src_sum - tgt_sum) < 1e-9:
            return "pass", "", detail
        return (
            "fail",
            f"{src_doc_type}.{src_field} 合计 [{src_sum}] 与 {tgt_doc_type}.{tgt_field} 合计 [{tgt_sum}] 不一致",
            detail,
        )

    # 主体类字段跨文档比对：先按公司核心名归一判等，吸收地址/法律后缀详略差异
    if is_party_field(src_field) or is_party_field(tgt_field):
        src_party = [normalize_party_name(v) for v in src_vals]
        tgt_party = [normalize_party_name(v) for v in tgt_vals]
        if all(n is not None for n in src_party) and all(n is not None for n in tgt_party):
            detail = {
                "src_vals": src_vals,
                "tgt_vals": tgt_vals,
                "party_name_norm": {"src": src_party, "tgt": tgt_party},
            }
            if aggregate == "ALL":
                if set(src_party) == set(tgt_party):
                    return "pass", "", detail
            elif set(src_party) & set(tgt_party):
                return "pass", "", detail

    # 字符串集合：任一匹配
    src_set = {str(v).strip() for v in src_vals}
    tgt_set = {str(v).strip() for v in tgt_vals}
    if aggregate == "ALL":
        if src_set == tgt_set:
            return "pass", "", {"src_vals": src_vals, "tgt_vals": tgt_vals, "aggregate": "ALL"}
        return (
            "fail",
            f"{src_doc_type}.{src_field} 集合 [{src_vals}] 与 {tgt_doc_type}.{tgt_field} 集合 [{tgt_vals}] 不完全一致",
            {"src_vals": src_vals, "tgt_vals": tgt_vals, "aggregate": "ALL"},
        )
    if src_set & tgt_set:
        return "pass", "", {"src_vals": src_vals, "tgt_vals": tgt_vals}
    return (
        "fail",
        f"{src_doc_type}.{src_field} [{src_vals}] 与 {tgt_doc_type}.{tgt_field} [{tgt_vals}] 不一致",
        {"src_vals": src_vals, "tgt_vals": tgt_vals},
    )


def _cmp_numeric(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    direction: str,  # "le" = src ≤ tgt, "ge" = src ≥ tgt
    tolerance: float,
    tolerance_params: Optional[dict] = None,
    aggregate: Optional[str] = None,
) -> tuple[str, str, dict]:
    """数值比较（不大于 / 不小于），多单汇总 + 统一容差。

    1-3：同合同多张水单为分批付款，按文档集合求和后比较。
    1-4：容差来源优先级：tolerance_params 按字段类型（金额→amount_percent 百分比，
    重量→weight_kg 绝对值）→ 关系 tolerance（绝对值）→ 0。
    批次 7-2 聚合模式：
    - SUM/None：多单求和后比较（分批付款默认）
    - ANY：任一 src 值满足与任一 tgt 值的比较（最宽松）
    - ALL：所有 src 值都满足与所有 tgt 值的比较（最严格）
    """
    src_pairs, src_missing = _collect_field_evidence(docs, src_doc_type, src_field)
    tgt_pairs, tgt_missing = _collect_field_evidence(docs, tgt_doc_type, tgt_field)
    src_vals = [v for _, v in src_pairs]
    tgt_vals = [v for _, v in tgt_pairs]
    if not src_vals or not tgt_vals:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 字段数据缺失",
            {
                "reason": "field_data_missing",
                "missing_fields": _collect_missing(docs, [(src_doc_type, src_field), (tgt_doc_type, tgt_field)]),
            },
        )
    if aggregate != "ANY" and (src_missing or tgt_missing):
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 存在不完整数据",
            {
                "reason": "incomplete_field_data",
                "missing_fields": src_missing + tgt_missing,
            },
        )

    src_nums, tgt_nums = _to_numeric_list(src_vals), _to_numeric_list(tgt_vals)
    if src_nums is None or tgt_nums is None:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field}/{tgt_doc_type}.{tgt_field} 字段无法解析为数值",
            {"reason": "unparseable_numeric", "src_vals": src_vals, "tgt_vals": tgt_vals},
        )
    if aggregate == "ANY":
        a, b = (min(src_nums), max(tgt_nums)) if direction == "le" else (max(src_nums), min(tgt_nums))
    elif aggregate == "ALL":
        a, b = (max(src_nums), min(tgt_nums)) if direction == "le" else (min(src_nums), max(tgt_nums))
    else:
        a, b = sum(src_nums), sum(tgt_nums)
    diff = a - b

    tol_kind, tol_val = _resolve_tolerance(src_field, tolerance, tolerance_params)
    if direction == "le":
        # src ≤ tgt + tolerance
        allowed = b + (b * tol_val / 100.0 if tol_kind == "pct" else tol_val)
        if a <= allowed:
            return "pass", "", {"src": a, "tgt": b, "diff": diff, "tolerance": tol_val, "tolerance_kind": tol_kind}
        return (
            "fail",
            f"{src_doc_type}.{src_field} 合计={a} 大于 {tgt_doc_type}.{tgt_field} 合计={b}（容差 {tol_val}{'%' if tol_kind == 'pct' else ''}）",
            {"src": a, "tgt": b, "diff": diff, "tolerance": tol_val, "tolerance_kind": tol_kind},
        )
    else:  # ge
        allowed = b - (b * tol_val / 100.0 if tol_kind == "pct" else tol_val)
        if a >= allowed:
            return "pass", "", {"src": a, "tgt": b, "diff": diff, "tolerance": tol_val, "tolerance_kind": tol_kind}
        return (
            "fail",
            f"{src_doc_type}.{src_field} 合计={a} 小于 {tgt_doc_type}.{tgt_field} 合计={b}（容差 {tol_val}{'%' if tol_kind == 'pct' else ''}）",
            {"src": a, "tgt": b, "diff": diff, "tolerance": tol_val, "tolerance_kind": tol_kind},
        )


def _cmp_date(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    allow_same_day: bool,
    aggregate: Optional[str] = None,
    tolerance_days: int = 0,
) -> tuple[str, str, dict]:
    """日期比较（多单汇总语义）：src 应早于（或不晚于）tgt。

    1-3：多张水单分批付款时，取 src 集合的最晚日期与 tgt 集合的最早日期比较，
    即所有 src 日期都应在所有 tgt 日期之前（或同日允许）。
    批次 7-2：aggregate=ANY 时取 src 最早 vs tgt 最晚（任一满足即可）。
    批次 13：tolerance_days>0 时按"间隔容差"判定——src 最晚日期允许比 tgt 最早日期
    晚至多 N 天（规则如"提交日期与费用发生日期的间隔不得超过30日"）。
    该参数此前只落库不生效，导致合规数据被判 fail（验收 BX-2026-0312 R0014）。
    """
    src_pairs, src_missing = _collect_field_evidence(docs, src_doc_type, src_field)
    tgt_pairs, tgt_missing = _collect_field_evidence(docs, tgt_doc_type, tgt_field)
    src_vals = [v for _, v in src_pairs]
    tgt_vals = [v for _, v in tgt_pairs]
    if not src_vals or not tgt_vals:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 日期字段数据缺失",
            {
                "reason": "field_data_missing",
                "missing_fields": _collect_missing(docs, [(src_doc_type, src_field), (tgt_doc_type, tgt_field)]),
            },
        )
    if aggregate != "ANY" and (src_missing or tgt_missing):
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 存在不完整日期数据",
            {
                "reason": "incomplete_field_data",
                "missing_fields": src_missing + tgt_missing,
            },
        )

    src_dates = _parse_dates(src_vals)
    tgt_dates = _parse_dates(tgt_vals)
    if src_dates is None or tgt_dates is None:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field}/{tgt_doc_type}.{tgt_field} 日期无法解析",
            {"reason": "unparseable_date", "src_vals": src_vals, "tgt_vals": tgt_vals},
        )

    # src 早于（不晚于）tgt：
    # ALL/默认 → max(src) <= min(tgt)（全部满足）
    # ANY → min(src) <= max(tgt)（任一满足）
    if aggregate == "ANY":
        latest_src = min(src_dates)
        earliest_tgt = max(tgt_dates)
    else:
        latest_src = max(src_dates)
        earliest_tgt = min(tgt_dates)
    detail = {
        "src_dates": src_vals, "tgt_dates": tgt_vals,
        "latest_src": latest_src.isoformat(), "earliest_tgt": earliest_tgt.isoformat(),
        "allow_same_day": allow_same_day,
    }
    if tolerance_days > 0:
        # 间隔容差：src 最晚日期 - tgt 最早日期 <= N 天（含同日/提前）
        gap_days = (latest_src - earliest_tgt).days
        detail["tolerance_days"] = tolerance_days
        detail["gap_days"] = gap_days
        if gap_days <= tolerance_days:
            return "pass", "", detail
        return (
            "fail",
            f"{src_doc_type}.{src_field} 最晚日期={latest_src.isoformat()} 比 "
            f"{tgt_doc_type}.{tgt_field} 最早日期={earliest_tgt.isoformat()} 晚 {gap_days} 天，"
            f"超过容差 {tolerance_days} 天",
            detail,
        )
    if latest_src < earliest_tgt:
        return "pass", "", detail
    if latest_src == earliest_tgt:
        return ("pass", "", detail) if allow_same_day else (
            "fail",
            f"{src_doc_type}.{src_field} 最晚日期={latest_src.isoformat()} 与 {tgt_doc_type}.{tgt_field} 最早日期={earliest_tgt.isoformat()} 同日（不允许）",
            detail,
        )
    return (
        "fail",
        f"{src_doc_type}.{src_field} 最晚日期={latest_src.isoformat()} 晚于 {tgt_doc_type}.{tgt_field} 最早日期={earliest_tgt.isoformat()}",
        detail,
    )


def _cmp_date_reversed(
    src_doc_type: str,
    src_field: str,
    tgt_doc_type: str,
    tgt_field: str,
    docs: list[Document],
    *,
    allow_same_day: bool,
    aggregate: Optional[str] = None,
    tolerance_days: int = 0,
) -> tuple[str, str, dict]:
    """校验 source 不早于（或晚于）target。

    实现上交换左右侧后复用 ``_cmp_date``：target <= source 等价于
    source >= target。多文档默认仍要求所有 source 都不早于所有 target。
    """
    result, issue, detail = _cmp_date(
        tgt_doc_type,
        tgt_field,
        src_doc_type,
        src_field,
        docs,
        allow_same_day=allow_same_day,
        aggregate=aggregate,
        tolerance_days=tolerance_days,
    )
    detail = dict(detail or {})
    detail["direction"] = "src_not_before_target"
    if result == "fail":
        issue = (
            f"{src_doc_type}.{src_field} 早于 {tgt_doc_type}.{tgt_field}"
            + ("" if allow_same_day else "或与其同日（严格顺序不允许同日）")
        )
    return result, issue, detail


def _cmp_total_eq(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    tolerance_pct: Optional[float] = None,
    tolerance_params: Optional[dict] = None,
) -> tuple[str, str, dict]:
    """一对多总额比对：src 类型的字段总和 == tgt 类型的字段总和（容差内）。

    8-4：容差统一——tolerance_params.amount_percent（Value 节点/规则声明）优先；
    tolerance_pct 仅兼容批次 1 旧调用签名；两者皆缺省时按严格 0% 执行
    （去除全局 5% 双轨，防空满足，杜绝隐式宽松）。
    """
    src_total, src_missing = _aggregate_field(docs, src_doc_type, src_field)
    tgt_total, tgt_missing = _aggregate_field(docs, tgt_doc_type, tgt_field)
    missing = (src_missing or []) + (tgt_missing or [])
    if src_total is None or tgt_total is None or missing:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 金额字段数据缺失或合计不完整",
            {
                "reason": (
                    "incomplete_field_data"
                    if src_total is not None and tgt_total is not None
                    else "field_data_missing"
                ),
                "missing_fields": missing,
            },
        )
    diff = src_total - tgt_total
    pct = _amount_diff_pct(src_total, tgt_total)
    params = tolerance_params or {}
    rule_tol = params.get("amount_percent")
    if rule_tol is not None:
        used_tolerance = float(rule_tol)
        tolerance_source = "value_node_or_rule"
    elif tolerance_pct is not None:
        used_tolerance = float(tolerance_pct)
        tolerance_source = "legacy_param"
    else:
        used_tolerance = 0.0
        tolerance_source = "strict_default"
    detail = {
        "src_total": src_total, "tgt_total": tgt_total,
        "diff": diff, "diff_pct": pct, "tolerance_pct": used_tolerance,
        "tolerance_source": tolerance_source,
    }
    if pct <= used_tolerance:
        return "pass", "", detail
    return (
        "fail",
        f"{src_doc_type}.{src_field} 总额 ¥{src_total:,.2f} 与 {tgt_doc_type}.{tgt_field} 总额 ¥{tgt_total:,.2f} "
        f"差额 ¥{diff:,.2f}（{pct:.1f}%），超出 {used_tolerance}% 容差",
        detail,
    )


def _cmp_contains(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
) -> tuple[str, str, dict]:
    """包含关系（多单汇总语义）：src 字段值应包含于 tgt 字段值（合同号归一化场景）。"""
    src_pairs, src_missing = _collect_field_evidence(docs, src_doc_type, src_field)
    tgt_pairs, tgt_missing = _collect_field_evidence(docs, tgt_doc_type, tgt_field)
    src_vals = [v for _, v in src_pairs]
    tgt_vals = [v for _, v in tgt_pairs]
    if not src_vals or not tgt_vals:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 字段数据缺失",
            {
                "reason": "field_data_missing",
                "missing_fields": _collect_missing(docs, [(src_doc_type, src_field), (tgt_doc_type, tgt_field)]),
            },
        )
    if src_missing or tgt_missing:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 存在不完整数据",
            {
                "reason": "incomplete_field_data",
                "missing_fields": src_missing + tgt_missing,
            },
        )

    src_strs = [str(v).strip() for v in src_vals]
    tgt_strs = [str(v).strip() for v in tgt_vals]

    # 合同号场景：归一化后比较（1-5：无合同上下文时容错，仅做归一化等价判断）
    contract = _current_contract_opt()
    aliases = set(getattr(contract, "alias_list", None) or []) if contract is not None else set()
    contract_no = getattr(contract, "contract_no", None) if contract is not None else None
    ok_aliases = aliases | ({contract_no} if contract_no else set())

    for src_str in src_strs:
        src_candidates = extract_contract_numbers(src_str)
        if not src_candidates:
            # 不是合同号格式 → 退化为字符串包含
            if any(src_str in t or t in src_str for t in tgt_strs):
                continue
            return "fail", f"{src_doc_type}.{src_field} [{src_str}] 与 {tgt_doc_type}.{tgt_field} 无包含关系", {"src": src_str, "tgt_vals": tgt_vals}
        src_canonical, _ = normalize_contract_no(src_candidates)
        tgt_canonicals = set()
        for tgt_str in tgt_strs:
            tc = extract_contract_numbers(tgt_str)
            if tc:
                cn, _ = normalize_contract_no(tc)
                tgt_canonicals.add(cn)
        if src_canonical in ok_aliases or src_canonical in tgt_canonicals:
            continue
        return "fail", f"{src_doc_type}.{src_field} [{src_str}] 未包含于 {tgt_doc_type}.{tgt_field} [{tgt_vals}]", {"src": src_str, "tgt_vals": tgt_vals, "src_canonical": src_canonical}
    return "pass", "", {"src_vals": src_vals, "tgt_vals": tgt_vals}


# ---------- 辅助 ----------

# 用 ContextVar 而非模块级全局：并发/多线程审查时各自隔离，避免合同上下文互相覆盖串数据。
_current_contract_var: contextvars.ContextVar[Optional[Contract]] = contextvars.ContextVar(
    "_current_contract_var", default=None
)
# 学到的签字角色映射（判定架构 P2）：{doc_type: {角色: 单据字段名}}，由 field_bindings 沉淀。
# 命中时优先按"已知签字栏"取值——新场景第二次出现就不必再靠启发式猜。
_learned_roles_var: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "_learned_roles_var", default={}
)


def _current_contract_opt() -> Optional[Contract]:
    """获取当前执行上下文的合同对象；无上下文时返回 None（容错，不抛异常）。"""
    return _current_contract_var.get()


def set_learned_role_fields(mapping: dict) -> None:
    """注入学到的签字角色映射（审查入口调用，线程/任务内隔离）。"""
    _learned_roles_var.set(mapping or {})


def _learned_roles_for(doc_type: str) -> dict:
    mapping = _learned_roles_var.get() or {}
    roles = mapping.get(doc_type)
    return roles if isinstance(roles, dict) else {}


def _collect_fields(docs: list[Document], doc_type: str, field: str) -> list:
    """收集指定 doc_type 所有文档的指定字段值（多单汇总用），跳过缺失值。"""
    values, _ = _collect_field_evidence(docs, doc_type, field)
    return [v for _, v in values]


def _collect_field_evidence(docs: list[Document], doc_type: str, field: str) -> tuple[list, list[dict]]:
    """字段值采集（8-2 聚合下沉 + 8-3 缺失证据）。

    Returns (values, missing)：
    - values: [(doc, value), ...]（跳过缺失值）
    - missing: 字段级缺失清单 [{doc_type, field, reason, doc_files}]，
      reason ∈ no_docs_of_type | field_missing
      - 复合表达式字段额外带 missing_atoms（缺的是哪个原子字段）
    """
    typed = _docs_by_type(docs, doc_type)
    if not typed:
        return [], [{"doc_type": doc_type, "field": field, "reason": "no_docs_of_type", "doc_files": []}]
    values: list = []
    missing_files: list[str] = []
    missing_atoms: set[str] = set()
    for d in typed:
        v = _get_field(d, field)
        if v is None:
            missing_files.append(d.file_name)
            missing_atoms.update(_field_missing_atoms(d, field))
        else:
            values.append((d, v))
    missing: list[dict] = []
    if missing_files:
        entry = {"doc_type": doc_type, "field": field, "reason": "field_missing", "doc_files": missing_files}
        if missing_atoms:
            entry["missing_atoms"] = sorted(missing_atoms)
        missing.append(entry)
    return values, missing


def _collect_missing(docs: list[Document], pairs: list[tuple[str, str]]) -> list[dict]:
    """批量收集字段级缺失清单（8-3）。pairs: [(doc_type, field), ...]"""
    missing: list[dict] = []
    for doc_type, field in pairs:
        _, m = _collect_field_evidence(docs, doc_type, field)
        missing.extend(m)
    return missing


def _props_dict(value) -> dict:
    """Neo4j 属性值反序列化：dict 原样返回；JSON 字符串解析为 dict（写入侧序列化所致）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _props_list(value) -> list:
    """Neo4j 属性值反序列化：list 原样返回；JSON 字符串解析为 list。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _field_evidence_entry(docs: list[Document], doc_type: str, field: str, node_name: str) -> dict:
    """字段级证据（9-3）：字段值来源文档明细 + 缺失清单。"""
    values, missing = _collect_field_evidence(docs, doc_type, field)
    return {
        "node": node_name,
        "doc_type": doc_type,
        "field": field,
        "docs": [
            {"doc_id": str(d.id), "doc_name": d.file_name, "value": v}
            for d, v in values
        ],
        "missing_fields": missing,
    }


def _build_evidence(
    rel_props: dict,
    docs: list[Document],
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    source_name: str, target_name: str,
) -> dict:
    """证据链（9-3，C3）：规则 → 条件/断言 → 字段 → 文档 的完整来源。
    与 detail 顶层字段互补：顶层存比较数值与意图链，evidence 存可追溯的文档级来源。
    """
    return {
        "rule": {
            "rule_id": rel_props.get("rule_id"),
            "rule_text": rel_props.get("rule_text"),
            "doc_type": rel_props.get("doc_type"),
            "check_category": rel_props.get("check_category"),
        },
        "source": _field_evidence_entry(docs, src_doc_type, src_field, source_name),
        "target": _field_evidence_entry(docs, tgt_doc_type, tgt_field, target_name),
    }


def _check_currency(
    required: str,
    src_doc_type: str,
    tgt_doc_type: str,
    docs: list[Document],
) -> str:
    """币别一致性校验（批次 7-4）：两侧文档的"币别"字段均须存在且与断言币别一致。

    Returns:
        "ok" / "fail" / "unverifiable"
    """
    src_vals = _collect_fields(docs, src_doc_type, "币别")
    tgt_vals = _collect_fields(docs, tgt_doc_type, "币别")
    if not src_vals or not tgt_vals:
        return "unverifiable"
    norm = lambda s: str(normalize_currency_value(s) or s).strip().upper()
    src_cur = norm(src_vals[0])
    tgt_cur = norm(tgt_vals[0])
    if src_cur != tgt_cur or src_cur != norm(required):
        return "fail"
    return "ok"


def _to_numeric_list(values: list) -> Optional[list[float]]:
    """把字段值列表全部转为数值；任一无法解析则返回 None。"""
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            return None
    return out


def _parse_dates(values: list) -> Optional[list[datetime]]:
    """把字段值列表全部解析为日期；任一无法解析则返回 None。"""
    out: list[datetime] = []
    for v in values:
        try:
            out.append(datetime.fromisoformat(str(v)))
        except Exception:
            return None
    return out


def _is_amount_field(field: str) -> bool:
    """金额字段特征判断（用于容差选择）。"""
    return any(k in field for k in ("金额", "总价", "单价", "货款", "价", "额", "amount", "price", "total"))


def _is_weight_field(field: str) -> bool:
    """重量字段特征判断（用于容差选择）。"""
    return any(k in field for k in ("重量", "毛重", "净重", "weight", "gross", "net"))


def _resolve_tolerance(field: str, tolerance: float, tolerance_params: Optional[dict]) -> tuple[str, float]:
    """统一容差来源（1-4）：
    金额字段 → tolerance_params.amount_percent（百分比）
    重量字段 → tolerance_params.weight_kg（绝对值）
    其他 → 关系 tolerance（绝对值）
    """
    params = tolerance_params or {}
    if _is_amount_field(field):
        ap = params.get("amount_percent")
        if ap is not None:
            return "pct", float(ap)
    if _is_weight_field(field):
        wk = params.get("weight_kg")
        if wk is not None:
            return "abs", float(wk)
    return "abs", float(tolerance or 0)


def _aggregate_field(docs: list[Document], doc_type: str, field: str) -> tuple[Optional[float], list[dict]]:
    """对指定 doc_type 的所有文档的指定数值字段求和（8-2 服务层聚合 + 8-3 缺失证据）。
    Returns (total | None, missing)。total=None 表示数据缺失，missing 为字段级缺失清单。
    """
    target = _docs_by_type(docs, doc_type)
    if not target:
        return None, [{"doc_type": doc_type, "field": field, "reason": "no_docs_of_type", "doc_files": []}]
    total = 0.0
    found = False
    missing_files: list[str] = []
    missing_atoms: set[str] = set()
    for d in target:
        v = _get_field(d, field)
        if v is None:
            missing_files.append(d.file_name)
            missing_atoms.update(_field_missing_atoms(d, field))
            continue
        num = _to_number(v)
        if num is None:
            missing_files.append(d.file_name)
            continue
        total += num
        found = True
    missing: list[dict] = []
    if missing_files:
        entry = {"doc_type": doc_type, "field": field, "reason": "field_missing", "doc_files": missing_files}
        if missing_atoms:
            entry["missing_atoms"] = sorted(missing_atoms)
        missing.append(entry)
    return (total if found else None), missing


def run_graph_review_with_contract(
    db: Session,
    tenant_id: uuid.UUID,
    contract: Contract,
    docs: list[Document],
    neo4j: Optional[Neo4jClient] = None,
    snapshot_id: Optional[uuid.UUID] = None,
) -> list[ReviewResult]:
    """与 run_graph_review 相同，但设置当前合同上下文供 _cmp_contains 使用。"""
    token = _current_contract_var.set(contract)
    role_token = _learned_roles_var.set(_load_learned_roles(db, tenant_id, docs))
    try:
        return run_graph_review(
            db,
            tenant_id,
            contract,
            docs,
            neo4j,
            snapshot_id=snapshot_id,
        )
    finally:
        _current_contract_var.reset(token)
        _learned_roles_var.reset(role_token)


def _load_learned_roles(db, tenant_id: uuid.UUID, docs: list[Document]) -> dict:
    """加载本案涉及文档类型已沉淀的签字角色映射（P2 学习闭环，失败不影响审查）。"""
    try:
        from .field_binding_service import learned_role_fields

        out: dict[str, dict] = {}
        for doc_type in {d.doc_type for d in docs if d.doc_type}:
            roles = learned_role_fields(db, tenant_id, doc_type)
            if roles:
                out[doc_type] = roles
        return out
    except Exception as e:  # pragma: no cover - 学习数据不可用时退化为启发式
        logger.info("签字角色映射加载失败（退化为启发式解析）: %s", e)
        return {}
