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

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..constants import (
    CHECK_ACCURACY,
    CHECK_COMPLETENESS,
    CHECK_STAMP,
    CHECK_TIME_LOGIC,
)
from ..models import Contract, Document, ReviewResult, RuleSnapshot
from ..neo4j_client import Neo4jClient, get_neo4j_client
from .contract_normalizer import extract_contract_numbers, normalize_contract_no
from .field_extraction_service import aggregate_amount, parse_amount, parse_date

logger = logging.getLogger(__name__)


# ============ 辅助：复用 review_service 的工具函数（避免循环依赖，就地实现） ============

def _docs_by_type(docs: list[Document], doc_type: str) -> list[Document]:
    return [d for d in docs if d.doc_type == doc_type]


def _get_field(doc: Document, key: str, aliases: list[str] | None = None):
    """从文档的 extracted_fields 取字段值，支持别名。"""
    fields = doc.extracted_fields or {}
    if key in fields and fields[key] is not None:
        return fields[key]
    for alias in (aliases or []):
        if alias in fields and fields[alias] is not None:
            return fields[alias]
    return None


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
) -> ReviewResult:
    """根据图谱关系属性构造 ReviewResult。

    rel_props 包含 rule_id, rule_text, doc_type, check_category。
    """
    return ReviewResult(
        rule_id=None,  # 图谱不再回写 rule_id（规则文本只是元数据）
        rule_text=rel_props.get("rule_text"),
        doc_type=rel_props.get("doc_type"),
        check_category=rel_props.get("check_category"),
        doc_id=doc.id if doc else None,
        result=result,
        issue_desc=issue_desc or None,
        detail=detail or {},
        suggestion=suggestion or None,
    )


# ============ 主入口 ============

def run_graph_review(
    db: Session,
    contract: Contract,
    docs: list[Document],
    neo4j: Optional[Neo4jClient] = None,
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

    # 1. 取最新快照的 graph_id
    from sqlalchemy import select
    stmt = select(RuleSnapshot).order_by(RuleSnapshot.snapshot_time.desc()).limit(1)
    snapshot = db.execute(stmt).scalars().first()
    if snapshot is None or not snapshot.graph_id:
        raise ValueError("无可用图谱快照，请先构建规则图谱")
    graph_id = snapshot.graph_id
    logger.info("图谱驱动审查: graph_id=%s, 合同=%s, 文档数=%d", graph_id, contract.contract_no, len(docs))

    results: list[ReviewResult] = []

    # 2. REQUIRED 关系 → 齐套性检查
    try:
        required_rels = neo4j.get_required_docs(graph_id)
        results.extend(_check_required_docs(required_rels, docs))
    except Exception as e:
        logger.error("齐套性图谱查询失败: %s", e, exc_info=True)

    # 3. MUST_STAMP 关系 → 印章检查
    try:
        stamp_rels = neo4j.get_stamp_requirements(graph_id)
        results.extend(_check_stamp_requirements(stamp_rels, docs))
    except Exception as e:
        logger.error("印章图谱查询失败: %s", e, exc_info=True)

    # 4. COMPARE_TO 关系 → 字段比对（信息准确性 / 时间逻辑）
    try:
        compare_rels = neo4j.get_compare_relationships(graph_id)
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
                rel_props, None, "pass", detail={"doc_type": doc_type},
            ))
        else:
            results.append(_make_result_from_graph(
                rel_props, None, "fail",
                issue_desc=f"缺失必备文件：{doc_type}",
                detail={"missing_doc_type": doc_type},
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
                ))
            elif doc.has_stamp is True:
                results.append(_make_result_from_graph(
                    rel_props, doc, "pass", detail={"has_stamp": True},
                ))
            elif doc.has_stamp is False:
                results.append(_make_result_from_graph(
                    rel_props, doc, "fail",
                    issue_desc=f"{doc.file_name} 未检测到印章",
                    detail={"has_stamp": False, "required": rel_props.get("rule_text", "")},
                ))
            else:
                results.append(_make_result_from_graph(
                    rel_props, doc, "unverifiable",
                    issue_desc=f"{doc.file_name} 印章无法判断（OCR 置信度低）",
                    detail={"has_stamp": None, "reason": "low_confidence"},
                ))
    return results


# ============ 检查3：字段比对（COMPARE_TO 关系） ============

def _check_compare_relationships(
    rels: list[dict],
    docs: list[Document],
    contract: Contract,
) -> list[ReviewResult]:
    """字段比对检查：根据 operator 派发。

    rels 形如:
      [{"source": "代理协议.协议方", "target": "委托出口确认单.委托方",
        "rel_props": {"operator": "等于", "tolerance": 0, "rule_id": "R010",
                      "rule_text": "...", "doc_type": "代理协议",
                      "check_category": "信息准确性"}, ...}]
    """
    results: list[ReviewResult] = []
    tolerance_pct = settings.amount_tolerance_percent

    for rec in rels:
        rel_props = rec.get("rel_props", {}) or {}
        source_name = rec.get("source")
        target_name = rec.get("target")
        operator = rel_props.get("operator", "等于")
        tolerance = float(rel_props.get("tolerance", 0) or 0)

        if not source_name or not target_name:
            continue

        # 解析"文件类型.字段名"
        src_doc_type, src_field = _parse_field_node(source_name)
        tgt_doc_type, tgt_field = _parse_field_node(target_name)
        if not src_doc_type or not tgt_doc_type:
            continue

        result, issue_desc, detail = _dispatch_compare(
            operator=operator,
            src_doc_type=src_doc_type, src_field=src_field,
            tgt_doc_type=tgt_doc_type, tgt_field=tgt_field,
            docs=docs,
            tolerance=tolerance,
            tolerance_pct=tolerance_pct,
            contract=contract,
        )

        results.append(_make_result_from_graph(
            rel_props, None, result, issue_desc=issue_desc, detail=detail,
        ))
    return results


def _parse_field_node(node_name: str) -> tuple[str, str]:
    """解析 '文件类型.字段名' 为 (doc_type, field)。无点号则返回 (node_name, "")。"""
    if "." in node_name:
        doc_type, _, field = node_name.partition(".")
        return doc_type.strip(), field.strip()
    return node_name.strip(), ""


def _dispatch_compare(
    operator: str,
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    tolerance: float,
    tolerance_pct: float,
    contract: Contract,
) -> tuple[str, str, dict]:
    """根据 operator 派发到对应的比对逻辑。返回 (result, issue_desc, detail)。"""
    op = operator.strip()

    if op == "等于":
        return _cmp_eq(src_doc_type, src_field, tgt_doc_type, tgt_field, docs)
    if op == "不大于":
        return _cmp_numeric(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, "le", tolerance)
    if op == "不小于":
        return _cmp_numeric(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, "ge", tolerance)
    if op == "时间早于":
        return _cmp_date(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, allow_same_day=False)
    if op == "时间不晚于":
        return _cmp_date(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, allow_same_day=True)
    if op == "总额等于":
        return _cmp_total_eq(src_doc_type, src_field, tgt_doc_type, tgt_field, docs, tolerance_pct)
    if op == "包含于":
        return _cmp_contains(src_doc_type, src_field, tgt_doc_type, tgt_field, docs)

    # 未知 operator，降级为字符串相等
    logger.warning("未知 operator: %s，降级为字符串相等", op)
    return _cmp_eq(src_doc_type, src_field, tgt_doc_type, tgt_field, docs)


# ---------- operator 实现 ----------

def _cmp_eq(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
) -> tuple[str, str, dict]:
    """字符串相等比较。"""
    src_val = _first_field(docs, src_doc_type, src_field)
    tgt_val = _first_field(docs, tgt_doc_type, tgt_field)
    if src_val is None or tgt_val is None:
        return "unverifiable", f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 字段无法提取", {}
    if str(src_val).strip() == str(tgt_val).strip():
        return "pass", "", {"src": src_val, "tgt": tgt_val}
    return (
        "fail",
        f"{src_doc_type}.{src_field} [{src_val}] 与 {tgt_doc_type}.{tgt_field} [{tgt_val}] 不一致",
        {"src": src_val, "tgt": tgt_val},
    )


def _cmp_numeric(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    direction: str,  # "le" = src ≤ tgt, "ge" = src ≥ tgt
    tolerance: float,
) -> tuple[str, str, dict]:
    """数值比较（不大于 / 不小于），支持容差。"""
    src_val = _first_field(docs, src_doc_type, src_field)
    tgt_val = _first_field(docs, tgt_doc_type, tgt_field)
    if src_val is None or tgt_val is None:
        return "unverifiable", f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 字段无法提取", {}
    try:
        a, b = float(src_val), float(tgt_val)
    except (TypeError, ValueError):
        return "unverifiable", f"{src_doc_type}.{src_field}/{tgt_doc_type}.{tgt_field} 字段无法解析为数值", {}
    diff = a - b
    if direction == "le":
        # src ≤ tgt + tolerance
        if a <= b + tolerance:
            return "pass", "", {"src": a, "tgt": b, "diff": diff, "tolerance": tolerance}
        return (
            "fail",
            f"{src_doc_type}.{src_field}={a} 大于 {tgt_doc_type}.{tgt_field}={b}（容差 {tolerance}）",
            {"src": a, "tgt": b, "diff": diff, "tolerance": tolerance},
        )
    else:  # ge
        if a >= b - tolerance:
            return "pass", "", {"src": a, "tgt": b, "diff": diff, "tolerance": tolerance}
        return (
            "fail",
            f"{src_doc_type}.{src_field}={a} 小于 {tgt_doc_type}.{tgt_field}={b}（容差 {tolerance}）",
            {"src": a, "tgt": b, "diff": diff, "tolerance": tolerance},
        )


def _cmp_date(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    allow_same_day: bool,
) -> tuple[str, str, dict]:
    """日期比较：src 应早于（或不晚于）tgt。"""
    src_val = _first_field(docs, src_doc_type, src_field)
    tgt_val = _first_field(docs, tgt_doc_type, tgt_field)
    if src_val is None or tgt_val is None:
        return "unverifiable", f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 日期字段无法提取", {}
    res = _compare_dates(str(src_val), str(tgt_val), allow_same_day=allow_same_day)
    detail = {"src_date": src_val, "tgt_date": tgt_val, "allow_same_day": allow_same_day}
    if res == "pass":
        return "pass", "", detail
    if res == "unverifiable":
        return "unverifiable", f"{src_doc_type}.{src_field}/{tgt_doc_type}.{tgt_field} 日期无法解析", detail
    return (
        "fail",
        f"{src_doc_type}.{src_field}={src_val} 晚于 {tgt_doc_type}.{tgt_field}={tgt_val}",
        detail,
    )


def _cmp_total_eq(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
    tolerance_pct: float,
) -> tuple[str, str, dict]:
    """一对多总额比对：src 类型的字段总和 == tgt 类型的字段总和（容差内）。"""
    src_total = _aggregate_field(docs, src_doc_type, src_field)
    tgt_total = _aggregate_field(docs, tgt_doc_type, tgt_field)
    if src_total is None or tgt_total is None:
        return (
            "unverifiable",
            f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 金额字段无法提取",
            {},
        )
    diff = src_total - tgt_total
    pct = _amount_diff_pct(src_total, tgt_total)
    detail = {
        "src_total": src_total, "tgt_total": tgt_total,
        "diff": diff, "diff_pct": pct, "tolerance_pct": tolerance_pct,
    }
    if pct <= tolerance_pct:
        return "pass", "", detail
    return (
        "fail",
        f"{src_doc_type}.{src_field} 总额 ¥{src_total:,.2f} 与 {tgt_doc_type}.{tgt_field} 总额 ¥{tgt_total:,.2f} "
        f"差额 ¥{diff:,.2f}（{pct:.1f}%），超出 {tolerance_pct}% 容差",
        detail,
    )


def _cmp_contains(
    src_doc_type: str, src_field: str,
    tgt_doc_type: str, tgt_field: str,
    docs: list[Document],
) -> tuple[str, str, dict]:
    """包含关系：src 字段值应包含于 tgt 字段值（合同号归一化场景）。"""
    src_val = _first_field(docs, src_doc_type, src_field)
    tgt_val = _first_field(docs, tgt_doc_type, tgt_field)
    if src_val is None or tgt_val is None:
        return "unverifiable", f"{src_doc_type}.{src_field} 或 {tgt_doc_type}.{tgt_field} 字段无法提取", {}

    src_str = str(src_val).strip()
    tgt_str = str(tgt_val).strip()

    # 合同号场景：归一化后比较
    src_candidates = extract_contract_numbers(src_str)
    tgt_candidates = extract_contract_numbers(tgt_str)
    if src_candidates and tgt_candidates:
        src_canonical, _ = normalize_contract_no(src_candidates)
        tgt_canonical, _ = normalize_contract_no(tgt_candidates)
        # 任何主合同号或别名匹配即可
        aliases = set(getattr(_current_contract(), "alias_list", None) or [])
        contract_no = getattr(_current_contract(), "contract_no", None)
        ok_aliases = aliases | ({contract_no} if contract_no else set())
        if src_canonical in ok_aliases or src_canonical == tgt_canonical:
            return "pass", "", {"src": src_val, "tgt": tgt_val, "src_canonical": src_canonical, "tgt_canonical": tgt_canonical}
        return (
            "fail",
            f"{src_doc_type}.{src_field} [{src_val}] 未包含于 {tgt_doc_type}.{tgt_field} [{tgt_val}]",
            {"src": src_val, "tgt": tgt_val, "src_canonical": src_canonical, "tgt_canonical": tgt_canonical},
        )

    # 普通字符串包含
    if src_str in tgt_str or tgt_str in src_str:
        return "pass", "", {"src": src_val, "tgt": tgt_val}
    return (
        "fail",
        f"{src_doc_type}.{src_field} [{src_val}] 与 {tgt_doc_type}.{tgt_field} [{tgt_val}] 无包含关系",
        {"src": src_val, "tgt": tgt_val},
    )


# ---------- 辅助 ----------

_current_contract_ref: Optional[Contract] = None


def _current_contract() -> Contract:
    """获取当前线程上下文的合同对象（用于 _cmp_contains 中的别名匹配）。"""
    if _current_contract_ref is None:
        raise RuntimeError("当前合同上下文未初始化")
    return _current_contract_ref


def _first_field(docs: list[Document], doc_type: str, field: str):
    """取指定 doc_type 的第一份文档的指定字段值。"""
    target = _docs_by_type(docs, doc_type)
    if not target:
        return None
    return _get_field(target[0], field)


def _aggregate_field(docs: list[Document], doc_type: str, field: str) -> Optional[float]:
    """对指定 doc_type 的所有文档的指定数值字段求和。"""
    target = _docs_by_type(docs, doc_type)
    if not target:
        return None
    total = 0.0
    found = False
    for d in target:
        v = _get_field(d, field)
        if v is None:
            continue
        try:
            total += float(v)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def run_graph_review_with_contract(
    db: Session,
    contract: Contract,
    docs: list[Document],
    neo4j: Optional[Neo4jClient] = None,
) -> list[ReviewResult]:
    """与 run_graph_review 相同，但设置当前合同上下文供 _cmp_contains 使用。"""
    global _current_contract_ref
    _current_contract_ref = contract
    try:
        return run_graph_review(db, contract, docs, neo4j)
    finally:
        _current_contract_ref = None
