# -*- coding: utf-8 -*-
"""规则意图编译（Intent Compiler）：把"规则条款 + 结构化 structure + 图谱关系"
编译成大模型可执行的审查意图。

设计（判定架构 P0/P3）：

- **图谱回归"检索与适用性"角色**：它回答"这条规则管哪些单据、要找什么、跟什么比"，
  不再充当"字段名字符串相等"的接头；
- 一条规则编译成一个或多个 intent（一条规则的多个断言/多份单据各成一项）；
- 每个 intent 带 `evidence_needed`（要找什么，语义描述）与 `prelim`（确定性引擎初判，
  可能为 None）；初判只是参考，最终由大模型在证据包上判定、由验证门核对；
- `warnings` 是**确定性建模体检**：把"算子与字段语义不匹配""条件被丢掉""方向可能反了"
  这类以往靠人肉发现的问题显式告诉大模型，让它在条款层面纠正，而不是我们继续打补丁。

对外接口：
- compile_intents(rules, graph_data, prelim_results) -> list[Intent]
- INTENT_KINDS / field_semantics(field)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..models import ReviewResult, Rule
from .doc_normalizer import normalize_field_name

logger = logging.getLogger(__name__)

# 义务类型：比对 / 存在性（签字、字段齐备）/ 齐套 / 用印 / 定性（纯文本）
OBLIGATION_KINDS = ("compare", "exist", "complete", "stamp", "free_text")

# ---------- 字段语义判定（用于建模体检，不做取值） ----------

_SIGNATURE_HINTS = ("签字", "签名", "签署", "签批", "盖章", "用印", "审批", "审核", "核准", "批准")
_DATE_HINTS = ("日期", "时间", "日")
_NUMERIC_HINTS = ("金额", "价税合计", "税额", "总价", "单价", "数量", "件数", "重量", "毛重", "净重", "税额", "比例")
_NUMERIC_OPERATORS = ("不小于", "不大于", "总额等于", "大于", "小于")
_DATE_OPERATORS = ("时间早于", "时间晚于", "时间不早于", "时间不晚于")
_INTERVAL_HINTS = ("间隔", "不得超过", "不超过", "日内", "天内", "之内", "以内")
_THRESHOLD_RE = re.compile(r"(达到|超过|不低于|不少于|及以上|以上|大于|满)\s*[0-9０-９]")
_REQUIREMENT_RE = re.compile(r"(应同时具备|应具备|须同时|需同时|均应|均须|必须有|应提供|齐备|齐全)")


def field_semantics(field: Any) -> str:
    """字段名 → 语义类别：number / date / signature / text。"""
    name = normalize_field_name(field)
    if not name:
        return "text"
    if any(h in name for h in _SIGNATURE_HINTS):
        return "signature"
    if any(h in name for h in _DATE_HINTS):
        return "date"
    if any(h in name for h in _NUMERIC_HINTS):
        return "number"
    return "text"


def _props(value: Any) -> dict:
    """Neo4j 属性反序列化（写入侧把嵌套结构序列化成 JSON 字符串）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _props_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _split_node(node_name: str) -> tuple[str, str]:
    """节点名 "报销单.报销金额|SUM" → ("报销单", "报销金额")。"""
    text = str(node_name or "")
    doc_type, _, field = text.partition(".")
    field = field.split("|", 1)[0] if field else ""
    return doc_type.strip(), field.strip()


def _node_props(nodes_by_name: dict[str, dict], name: str) -> dict:
    return _props((nodes_by_name.get(name) or {}).get("properties"))


def model_warnings(
    rule_text: str,
    condition: Optional[dict],
    operator: str,
    src_field: str,
    tgt_field: str,
) -> list[str]:
    """确定性建模体检：把疑似"规则被错误结构化"的地方显式标出来。

    这些结论只作为提示交给大模型（并在结果里留痕），不直接改判——
    真正的纠正依据是规则条款，而不是我们的启发式。
    """
    out: list[str] = []
    op = str(operator or "").strip()
    src_sem, tgt_sem = field_semantics(src_field), field_semantics(tgt_field)
    if op in _NUMERIC_OPERATORS:
        for name, sem in ((src_field, src_sem), (tgt_field, tgt_sem)):
            if sem in ("signature", "date"):
                out.append(
                    f"算子「{op}」是数值比较，但字段「{name}」是{'签字/用印' if sem == 'signature' else '日期'}类，"
                    "断言疑似被错误建模为比对"
                )
    if op in _DATE_OPERATORS and any(h in f"{src_field}{tgt_field}" for h in ("间隔", "天数", "期限")):
        out.append("日期算子疑似用于表达「间隔不超过N天」，需按条款与容差核对")
    if op in _DATE_OPERATORS and any(h in str(rule_text) for h in _INTERVAL_HINTS):
        out.append("条款含「间隔/不得超过N日」表述，但断言是单纯先后顺序，方向或容差可能不符")
    if condition is None and _THRESHOLD_RE.search(str(rule_text)):
        out.append("条款含金额门槛条件，但结构化条件为空，条件字段可能被丢弃")
    if _REQUIREMENT_RE.search(str(rule_text)) and op in _NUMERIC_OPERATORS + _DATE_OPERATORS:
        out.append("条款是「同时具备/齐备」类要求，断言却是字段比对，疑似义务类型建模错误")
    return out


def _condition_from_structure(structure: dict) -> Optional[dict]:
    cond = structure.get("condition") if isinstance(structure, dict) else None
    return cond if isinstance(cond, dict) and cond else None


def _requirement_items(structure: dict) -> list[dict]:
    req = structure.get("requirement") if isinstance(structure, dict) else None
    if not isinstance(req, dict):
        return []
    items = req.get("items") or req.get("all_of") or []
    return [x for x in items if isinstance(x, dict) and x.get("field")]


def _prelim_index(prelim: list[ReviewResult]) -> dict[tuple[str, str, str], ReviewResult]:
    out: dict[tuple[str, str, str], ReviewResult] = {}
    for r in prelim:
        rid = str((r.detail or {}).get("rule_id") or "")
        out[(rid, str(r.graph_source or ""), str(r.graph_target or ""))] = r
    return out


def _prelim_snapshot(r: ReviewResult) -> dict:
    detail = r.detail or {}
    return {
        "result": r.result,
        "issue": r.issue_desc or "",
        "operator": detail.get("operator"),
        "reason": detail.get("reason"),
        "src_values": detail.get("src_vals") or detail.get("src"),
        "tgt_values": detail.get("tgt_vals") or detail.get("tgt"),
        "latest_src": detail.get("latest_src"),
        "earliest_tgt": detail.get("earliest_tgt"),
        "tolerance_days": detail.get("tolerance_days"),
        "gap_days": detail.get("gap_days"),
        "condition_met": detail.get("condition_met"),
        "skipped_reason": detail.get("skipped_reason"),
    }


def compile_intents(
    rules: list[Rule],
    graph_data: Optional[dict],
    prelim_results: list[ReviewResult],
) -> list[dict]:
    """把规则集编译成审查意图列表（含图谱覆盖的比对/齐套/用印与无图谱覆盖的定性规则）。"""
    nodes = (graph_data or {}).get("nodes") or []
    edges = (graph_data or {}).get("edges") or []
    nodes_by_name = {n.get("name"): n for n in nodes if isinstance(n, dict)}
    by_rule: dict[str, list[dict]] = {}
    for edge in edges:
        props = _props(edge.get("properties"))
        rid = str(props.get("rule_id") or "").strip()
        if rid:
            by_rule.setdefault(rid, []).append(edge)

    prelim_by_key = _prelim_index(prelim_results)
    intents: list[dict] = []
    covered_rule_ids: set[str] = set()

    for rule in rules:
        rid = str(getattr(rule, "rule_code", "") or "").strip()
        if not rid:
            continue
        structure = rule.structure if isinstance(rule.structure, dict) else {}
        condition = _condition_from_structure(structure)
        exceptions = [x for x in (structure.get("exceptions") or []) if isinstance(x, dict)]
        base = {
            "rule_id": rid,
            "clause": rule.rule_text or "",
            "check_category": rule.check_category,
            "doc_type": rule.doc_type,
            "scope": rule.scope or {},
            "condition": condition,
            "exceptions": exceptions,
        }
        rule_edges = by_rule.get(rid, [])
        produced = 0

        for edge in rule_edges:
            etype = str(edge.get("type") or "")
            props = _props(edge.get("properties"))
            src_node, tgt_node = edge.get("source") or "", edge.get("target") or ""
            if etype == "COMPARE_TO":
                src_doc_type, src_field = _split_node(src_node)
                tgt_doc_type, tgt_field = _split_node(tgt_node)
                src_props = _node_props(nodes_by_name, src_node)
                tgt_props = _node_props(nodes_by_name, tgt_node)
                operator = str(props.get("operator") or "等于").strip()
                prelim = prelim_by_key.get((rid, src_node, tgt_node))
                intent = {
                    **base,
                    "intent_id": f"{rid}#{src_node}->{tgt_node}",
                    "obligation": {
                        "kind": "compare",
                        "operator": operator,
                        "source": {
                            "doc_type": src_doc_type,
                            "field": src_field,
                            "aggregate": props.get("aggregate") or src_props.get("aggregate"),
                            "role": src_props.get("role"),
                        },
                        "target": {
                            "doc_type": tgt_doc_type,
                            "field": tgt_field,
                            "aggregate": props.get("aggregate") or tgt_props.get("aggregate"),
                            "role": tgt_props.get("role"),
                        },
                        "tolerance_params": _props(
                            props.get("tolerance_params") or src_props.get("tolerance_params")
                        ),
                    },
                    "graph_nodes": {"source": src_node, "target": tgt_node},
                    "evidence_needed": [
                        {"doc_type": src_doc_type, "field": src_field},
                        {"doc_type": tgt_doc_type, "field": tgt_field},
                    ]
                    + (
                        [{"doc_type": condition.get("doc_type") or src_doc_type, "field": condition.get("field")}]
                        if condition and condition.get("field")
                        else []
                    ),
                    "warnings": model_warnings(
                        rule.rule_text or "", condition, operator, src_field, tgt_field
                    ),
                    "prelim": _prelim_snapshot(prelim) if prelim is not None else None,
                }
                intents.append(intent)
                produced += 1
            elif etype == "MUST_EXIST":
                doc_type, field = _split_node(edge.get("target"))
                doc_type = doc_type or str(props.get("doc_type") or "")
                prelim = prelim_by_key.get((rid, src_node, tgt_node))
                intents.append(
                    {
                        **base,
                        "intent_id": f"{rid}#exist:{src_node}->{tgt_node}",
                        "obligation": {
                            "kind": "exist",
                            "items": [
                                {
                                    "doc_type": doc_type,
                                    "field": field,
                                    "must": props.get("must") or "present",
                                }
                            ],
                        },
                        "graph_nodes": {"source": src_node, "target": tgt_node},
                        "evidence_needed": [{"doc_type": doc_type, "field": field}],
                        "warnings": model_warnings(
                            rule.rule_text or "", condition, "", field, ""
                        ),
                        "prelim": _prelim_snapshot(prelim) if prelim is not None else None,
                    }
                )
                produced += 1
            elif etype == "REQUIRED":
                doc_type = str(edge.get("target") or "")
                prelim = prelim_by_key.get((rid, src_node, tgt_node))
                intents.append(
                    {
                        **base,
                        "intent_id": f"{rid}#required:{doc_type}",
                        "obligation": {"kind": "complete", "required_doc_types": [doc_type]},
                        "graph_nodes": {"source": src_node, "target": tgt_node},
                        "evidence_needed": [{"doc_type": doc_type, "field": None}],
                        "warnings": [],
                        "prelim": _prelim_snapshot(prelim) if prelim is not None else None,
                    }
                )
                produced += 1
            elif etype == "MUST_STAMP":
                doc_type = str(edge.get("source") or props.get("doc_type") or "")
                prelim = prelim_by_key.get((rid, src_node, tgt_node))
                intents.append(
                    {
                        **base,
                        "intent_id": f"{rid}#stamp:{doc_type}",
                        "obligation": {"kind": "stamp", "doc_type": doc_type},
                        "graph_nodes": {"source": src_node, "target": tgt_node},
                        "evidence_needed": [{"doc_type": doc_type, "field": "has_stamp"}],
                        "warnings": [],
                        "prelim": _prelim_snapshot(prelim) if prelim is not None else None,
                    }
                )
                produced += 1

        produced_kinds = {
            intents[-i]["obligation"]["kind"]
            for i in range(1, min(len(intents), produced) + 1)
        } if produced else set()

        # 结构化 requirement（P3）：条款是"应同时具备/须齐备"但图谱还没长成边时也能审
        # （图谱已产出同样义务时不重复）
        req_items = _requirement_items(structure)
        if req_items and "exist" not in produced_kinds:
            intents.append(
                {
                    **base,
                    "intent_id": f"{rid}#requirement",
                    "obligation": {"kind": "exist", "items": req_items},
                    "graph_nodes": {},
                    "evidence_needed": [
                        {"doc_type": x.get("doc_type") or rule.doc_type, "field": x.get("field")}
                        for x in req_items
                    ],
                    "warnings": [],
                    "prelim": None,
                }
            )
            produced += 1
            produced_kinds.add("exist")

        # 图谱不可用（无快照/Neo4j 异常）时的降级路径：直接用结构断言的字段与算子
        assertion = structure.get("assertion") if isinstance(structure, dict) else None
        if "compare" not in produced_kinds and isinstance(assertion, dict):
            src = assertion.get("source") if isinstance(assertion.get("source"), dict) else {}
            tgt = assertion.get("target") if isinstance(assertion.get("target"), dict) else {}
            src_field, tgt_field = src.get("field"), tgt.get("field")
            if src_field and tgt_field:
                operator = str(assertion.get("operator") or "等于").strip()
                src_doc_type = src.get("doc_type") or rule.doc_type
                tgt_doc_type = tgt.get("doc_type") or rule.doc_type
                intents.append(
                    {
                        **base,
                        "intent_id": f"{rid}#structure",
                        "obligation": {
                            "kind": "compare",
                            "operator": operator,
                            "source": {"doc_type": src_doc_type, "field": src_field,
                                       "aggregate": src.get("aggregate")},
                            "target": {"doc_type": tgt_doc_type, "field": tgt_field,
                                       "aggregate": tgt.get("aggregate")},
                            "tolerance_params": _props(
                                assertion.get("tolerance_params") or rule.tolerance or {}
                            ),
                        },
                        "graph_nodes": {},
                        "evidence_needed": [
                            {"doc_type": src_doc_type, "field": src_field},
                            {"doc_type": tgt_doc_type, "field": tgt_field},
                        ],
                        "warnings": model_warnings(
                            rule.rule_text or "", condition, operator,
                            str(src_field), str(tgt_field),
                        ),
                        "prelim": None,
                    }
                )
                produced += 1
                produced_kinds.add("compare")

        if produced == 0:
            # 无图谱覆盖、无结构化义务：交给大模型按条款在证据包上定性判定
            doc_types = []
            scope = rule.scope or {}
            if isinstance(scope.get("doc_types"), list):
                doc_types = [x for x in scope["doc_types"] if isinstance(x, str)]
            elif rule.doc_type:
                doc_types = [rule.doc_type]
            intents.append(
                {
                    **base,
                    "intent_id": f"{rid}#free",
                    "obligation": {"kind": "free_text", "doc_types": doc_types},
                    "graph_nodes": {},
                    "evidence_needed": [{"doc_type": dt, "field": None} for dt in doc_types],
                    "warnings": model_warnings(rule.rule_text or "", condition, "", "", ""),
                    "prelim": None,
                }
            )
            produced += 1

        if produced:
            covered_rule_ids.add(rid)

    return intents


def intent_payload(intent: dict, *, include_prelim: bool = True) -> dict:
    """给提示词的紧凑意图（去掉内部字段）。"""
    payload = {
        "intent_id": intent.get("intent_id"),
        "rule_id": intent.get("rule_id"),
        "clause": intent.get("clause"),
        "check_category": intent.get("check_category"),
        "scope": intent.get("scope"),
        "condition": intent.get("condition"),
        "obligation": intent.get("obligation"),
        "exceptions": intent.get("exceptions"),
        "evidence_needed": intent.get("evidence_needed"),
    }
    if include_prelim and intent.get("prelim") is not None:
        payload["prelim"] = intent["prelim"]
    if intent.get("warnings"):
        payload["modeling_warnings"] = intent["warnings"]
    return payload
