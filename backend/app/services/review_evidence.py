# -*- coding: utf-8 -*-
"""证据包（Evidence Bundle）：把单据的 OCR 结果整理成"可引用的事实清单"。

设计（判定架构 P1，替代"字段名投影"）：

- 判定不再以"字段名字符串相等"作为规则侧与单据侧的接头，而是把**单据事实连同出处**
  （所属文件、字段名、值、原文行、置信度）整包交给大模型；
- 大模型的结论必须**引用包内事实**（citations），并声明它做过的比较（checks），
  Python 验证门按同一份包逐条核对/重算——包是提示词与验证的**唯一真相**；
- 原文出处取 documents.ocr_text（文本型 PDF/DOCX 与图片 OCR 统一存放），
  若 ocr_layout 有对应行则附归一化 bbox，便于前端高亮与人工复核。

对外接口：
- build_evidence_bundle(docs)：构建证据包
- resolve_citation(bundle, doc, key, value)：验证一条引用
- resolve_operand(bundle, operand)：按 operand 语法取值（支持多单聚合）
- candidate_keys(bundle, field)：为规则侧字段名找单据侧等价键（提示 + 学习信号）
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

from ..models import Document
from .doc_normalizer import normalize_field_name, strip_unit_qualifier

logger = logging.getLogger(__name__)

# 事实条数上限（防超长上下文；超出时按"值非空 + 有原文"优先保留）
MAX_FACTS_PER_DOC = 160
MAX_TEXT_CHARS_PER_DOC = 6000
# 引用值允许"部分引用"（如整格 "部门负责人 李强；财务负责人 未签字" 只引用前半段）的最小长度
MIN_PARTIAL_CITATION_CHARS = 4
# 数字比较相对容差
_NUM_REL_TOL = 1e-6

_WS_RE = re.compile(r"[\s\u3000]+")
_PUNCT_RE = re.compile(r"[，,。；;：:、（）()\[\]【】\"'“”‘’]")


def _norm_text(value: Any) -> str:
    """文本归一：去空白与常见标点、统一大小写（用于引用比对，不改变原值）。"""
    return _PUNCT_RE.sub("", _WS_RE.sub("", str(value if value is not None else ""))).lower()


def _to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _WS_RE.sub("", str(value)).replace(",", "").replace("，", "")
    text = text.lstrip("¥￥$€£").rstrip("%％")
    try:
        return float(text)
    except ValueError:
        return None


_LEADING_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?")


def _to_quantity(value: Any) -> Optional[float]:
    """宽松数量解析（仅用于 checks 的阈值/操作数）："30日" → 30、"1,234.50 元" → 1234.5。

    引用比对不用这个（避免把 "24HCSP000000123" 这类编号误当数字），
    只用于"模型声明的门槛值"与"证据包里的数量值"之间的重算。
    """
    strict = _to_number(value)
    if strict is not None:
        return strict
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "").replace("，", "").lstrip("¥￥$€£")
    match = _LEADING_NUMBER_RE.match(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _values_equal(a: Any, b: Any) -> tuple[bool, bool]:
    """值比对。Returns (相等, 是否部分引用)。"""
    na, nb = _to_number(a), _to_number(b)
    if na is not None and nb is not None:
        if abs(na - nb) <= _NUM_REL_TOL * max(1.0, abs(na), abs(nb)):
            return True, False
        return False, False
    sa, sb = _norm_text(a), _norm_text(b)
    if not sa or not sb:
        return False, False
    if sa == sb:
        return True, False
    # 部分引用：引用的是原件片段（如整格里的一段）
    if len(sb) >= MIN_PARTIAL_CITATION_CHARS and sb in sa:
        return True, True
    return False, False


def _layout_lines(doc: Document) -> list[dict]:
    layout = doc.ocr_layout or {}
    lines = layout.get("lines") if isinstance(layout, dict) else None
    if isinstance(lines, list):
        return [x for x in lines if isinstance(x, dict) and x.get("text")]
    return []


def _match_span(
    text_lines: list[str],
    layout_lines: list[dict],
    key: str,
    value: Any,
) -> tuple[Optional[str], Optional[int], Optional[list[float]]]:
    """在原文里定位该字段的出处行：同一行含 key 与 value > 含 key > 含 value。"""
    key_norm = _norm_text(key)
    val_norm = _norm_text(value)
    key_hit: Optional[tuple[str, int]] = None
    val_hit: Optional[tuple[str, int]] = None
    for idx, line in enumerate(text_lines, start=1):
        line_norm = _norm_text(line)
        if key_norm and key_norm in line_norm:
            if val_norm and val_norm in line_norm:
                return line, idx, _bbox_of(layout_lines, line)
            if key_hit is None:
                key_hit = (line, idx)
            continue
        if val_hit is None and val_norm and len(val_norm) >= MIN_PARTIAL_CITATION_CHARS and val_norm in line_norm:
            val_hit = (line, idx)
    hit = key_hit or val_hit
    if hit is None:
        return None, None, None
    return hit[0], hit[1], _bbox_of(layout_lines, hit[0])


def _bbox_of(layout_lines: list[dict], text: str) -> Optional[list[float]]:
    target = _norm_text(text)
    if not target:
        return None
    for line in layout_lines:
        if _norm_text(line.get("text")) == target:
            bbox = line.get("bbox")
            return list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None
    for line in layout_lines:
        lt = _norm_text(line.get("text"))
        if lt and (lt in target or target in lt):
            bbox = line.get("bbox")
            return list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None
    return None


def _doc_facts(doc: Document, text_lines: list[str], layout_lines: list[dict]) -> list[dict]:
    fields = doc.extracted_fields or {}
    facts: list[dict] = []
    # 文档级事实：印章判断（has_stamp）是 OCR 的直接结论，也要能被引用与重算
    if doc.has_stamp is not None:
        facts.append({"key": "has_stamp", "value": bool(doc.has_stamp), "source": "doc_attribute"})
    # 签字/用印类字段：派生出"角色 → 签字"的可引用事实（忽略"未签字"这类否定表述），
    # 让"部门负责人已签、财务负责人未签"变成 LLM 可引用、机器可重算的证据。
    try:
        from .graph_review_service import iter_signature_roles  # 延迟导入避免循环

        for role, signer in iter_signature_roles(fields):
            derived = f"签字角色:{role}"
            if derived in fields:
                continue
            facts.append(
                {"key": derived, "value": signer, "derived_from": "signature_role"}
            )
    except Exception:  # pragma: no cover - 派生失败不影响主流程
        pass
    for key, value in fields.items():
        if not key or str(key).startswith("__"):
            continue
        if value is None or not str(value).strip():
            continue
        raw, line_no, bbox = _match_span(text_lines, layout_lines, str(key), value)
        fact: dict = {"key": str(key), "value": value}
        if raw:
            fact["raw"] = raw
        if line_no is not None:
            fact["line"] = line_no
        if bbox:
            fact["bbox"] = bbox
        facts.append(fact)
    # 优先保留有原文出处的事实（可引用性更强）
    if len(facts) > MAX_FACTS_PER_DOC:
        facts.sort(key=lambda f: 0 if f.get("raw") else 1)
        facts = facts[:MAX_FACTS_PER_DOC]
    return facts


def build_evidence_bundle(docs: Iterable[Document]) -> dict:
    """构建证据包。Returns {docs: [...], index: {...}, stats: {...}}。"""
    doc_entries: list[dict] = []
    index: dict[str, list[dict]] = {}
    for doc in docs:
        text = doc.ocr_text or ""
        text_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        layout_lines = _layout_lines(doc)
        facts = _doc_facts(doc, text_lines, layout_lines)
        entry = {
            "doc_id": str(getattr(doc, "id", "") or ""),
            "file_name": doc.file_name,
            "doc_type": doc.doc_type,
            "has_stamp": doc.has_stamp,
            "ocr_confidence": round(float(doc.ocr_confidence or 0.0), 2),
            "facts": facts,
        }
        if text and not facts:
            # 无结构化字段的定性材料（如纯文本说明），保留原文供定性判断
            entry["text_excerpt"] = text[:MAX_TEXT_CHARS_PER_DOC]
        doc_entries.append(entry)
        for fact in facts:
            index.setdefault(fact["key"], []).append(
                {"doc": doc.file_name, "doc_type": doc.doc_type, "value": fact["value"]}
            )
            base = strip_unit_qualifier(fact["key"])
            if base and base != fact["key"]:
                index.setdefault(base, []).append(
                    {
                        "doc": doc.file_name,
                        "doc_type": doc.doc_type,
                        "value": fact["value"],
                        "via_key": fact["key"],
                    }
                )
    bundle = {
        "docs": doc_entries,
        "index": index,
        "stats": {
            "docs": len(doc_entries),
            "facts": sum(len(d["facts"]) for d in doc_entries),
            "facts_with_raw": sum(
                1 for d in doc_entries for f in d["facts"] if f.get("raw")
            ),
        },
    }
    return bundle


# ============ 引用解析与验证 ============

def _facts_for_doc(bundle: dict, doc_name: str | None) -> list[dict]:
    out: list[dict] = []
    for entry in bundle.get("docs") or []:
        if doc_name and entry.get("file_name") != doc_name:
            continue
        for fact in entry.get("facts") or []:
            out.append({**fact, "doc": entry.get("file_name"), "doc_type": entry.get("doc_type")})
    return out


def candidate_keys(bundle: dict, field: str) -> list[dict]:
    """为规则侧字段名找单据侧等价键（单位/括号差异 + 完全同名）。"""
    base = strip_unit_qualifier(field)
    target = normalize_field_name(field)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in bundle.get("docs") or []:
        for fact in entry.get("facts") or []:
            key = normalize_field_name(fact.get("key"))
            if key == target:
                continue
            if strip_unit_qualifier(fact.get("key")) == base:
                marker = (entry["file_name"], fact["key"])
                if marker in seen:
                    continue
                seen.add(marker)
                out.append(
                    {
                        "doc": entry["file_name"],
                        "key": fact["key"],
                        "value": fact["value"],
                    }
                )
    return out


def resolve_citation(bundle: dict, doc: Any, key: Any, value: Any) -> dict:
    """验证一条引用。Returns {ok, reason, partial, fact_key, resolved_via}。

    - doc 必须指向包内单据；key 必须能落到该单据的事实（支持单位/括号等价键）；
    - value 必须与事实值一致（数值等值，或原文片段的部分引用）。
    """
    doc_name = str(doc or "").strip()
    key_name = str(key or "").strip()
    if not key_name:
        return {"ok": False, "reason": "citation_key_missing"}
    scoped = _facts_for_doc(bundle, doc_name or None)
    if doc_name and not scoped:
        return {"ok": False, "reason": f"citation_doc_not_found:{doc_name}"}
    if not scoped:
        return {"ok": False, "reason": "bundle_has_no_facts"}

    exact = [f for f in scoped if normalize_field_name(f["key"]) == normalize_field_name(key_name)]
    resolved_via = None
    if not exact:
        base = strip_unit_qualifier(key_name)
        exact = [f for f in scoped if strip_unit_qualifier(f["key"]) == base]
        if exact:
            resolved_via = exact[0]["key"]
    if not exact:
        # 允许"字段名互为包含"（如 所属公司 vs 所属公司名称）
        contains = [
            f
            for f in scoped
            if len(normalize_field_name(key_name)) >= 3
            and (
                normalize_field_name(key_name) in normalize_field_name(f["key"])
                or normalize_field_name(f["key"]) in normalize_field_name(key_name)
            )
        ]
        if len(contains) == 1:
            exact = contains
            resolved_via = contains[0]["key"]
    if not exact:
        return {"ok": False, "reason": f"citation_key_not_found:{key_name}"}
    if value is None or not str(value).strip():
        return {
            "ok": False,
            "reason": f"citation_value_missing:{key_name}",
            "fact_key": exact[0]["key"],
        }
    for fact in exact:
        same, partial = _values_equal(fact.get("value"), value)
        if same:
            return {
                "ok": True,
                "reason": "partial_value" if partial else "ok",
                "partial": partial,
                "fact_key": fact["key"],
                "fact_value": fact.get("value"),
                "doc": fact.get("doc"),
                "doc_type": fact.get("doc_type"),
                "resolved_via": resolved_via,
            }
    return {
        "ok": False,
        "reason": f"citation_value_mismatch:{key_name}",
        "fact_key": exact[0]["key"],
        "fact_value": exact[0].get("value"),
    }


# ============ operand 取值（checks 重算用） ============

_AGGREGATES = ("sum", "count", "min", "max", "first", "any", "all")

_TRUE_WORDS = ("true", "1", "是", "有", "已", "yes", "y")
_FALSE_WORDS = ("false", "0", "否", "无", "未", "no", "n", "")


def _as_bool(value: Any) -> Optional[bool]:
    """布尔/是否类取值：True/False、true/false、是/否 等。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return None


def resolve_operand(bundle: dict, operand: Any) -> tuple[Any, Optional[str]]:
    """解析 check 的操作数。Returns (值, 错误码)。

    operand 语法：
      标量（数字/字符串）→ 原样；
      {"cite": "字段名", "doc"?: "文件名", "doc_type"?: "类型",
       "aggregate"?: "sum|count|min|max|first|any", "index"?: 0}
      {"date_diff": {"from": OPERAND, "to": OPERAND}} → 两个日期相差的天数（to - from），
        用于"…与…的间隔不得超过 N 日"这类区间规则。
    aggregate 用于多张同类型单据（如发票价税合计求和）；多值且未声明聚合 → 报歧义，
    由大模型补 aggregate 后重试（而不是由 Python 猜）。
    """
    if not isinstance(operand, dict):
        return operand, None
    if "date_diff" in operand:
        spec = operand.get("date_diff") or {}
        if not isinstance(spec, dict):
            return None, "operand_date_diff_invalid"
        start, err = resolve_operand(bundle, spec.get("from"))
        if err:
            return None, err
        end, err = resolve_operand(bundle, spec.get("to"))
        if err:
            return None, err
        first, second = _as_iso_date(start), _as_iso_date(end)
        if first is None or second is None:
            return None, "operand_date_diff_not_date"
        try:
            from datetime import date as _date

            delta = (_date.fromisoformat(second) - _date.fromisoformat(first)).days
        except ValueError:
            return None, "operand_date_diff_not_date"
        return float(delta), None
    cite = str(operand.get("cite") or "").strip()
    if not cite:
        return None, "operand_cite_missing"
    if cite in ("*", "__docs__"):
        # 单据计数：{"cite": "*", "doc_type": "行程单", "aggregate": "count"}
        entries = [
            d
            for d in (bundle.get("docs") or [])
            if (not operand.get("doc") or d.get("file_name") == operand.get("doc"))
            and (not operand.get("doc_type") or d.get("doc_type") == operand.get("doc_type"))
        ]
        return len(entries), None
    doc_filter = str(operand.get("doc") or "").strip() or None
    type_filter = str(operand.get("doc_type") or "").strip() or None
    aggregate = str(operand.get("aggregate") or "").strip().lower()
    facts = [
        f
        for f in _facts_for_doc(bundle, doc_filter)
        if not type_filter or f.get("doc_type") == type_filter
    ]
    matched = [
        f
        for f in facts
        if normalize_field_name(f["key"]) == normalize_field_name(cite)
        or strip_unit_qualifier(f["key"]) == strip_unit_qualifier(cite)
    ]
    if not matched:
        # 复合字段表达式（如 金额+税额、件数*数量）：按同一份单据的原子字段求值
        expr_value, expr_err = _eval_expression_operand(
            bundle, cite, doc_filter, type_filter, aggregate
        )
        if expr_err:
            return None, expr_err
        if expr_value is not None:
            return expr_value, None
        return None, f"operand_not_found:{cite}"
    if "index" in operand:
        try:
            idx = int(operand["index"])
        except (TypeError, ValueError):
            return None, f"operand_index_invalid:{cite}"
        if not (0 <= idx < len(matched)):
            return None, f"operand_index_out_of_range:{cite}"
        return matched[idx].get("value"), None
    if len(matched) == 1:
        return matched[0].get("value"), None
    if not aggregate:
        return None, f"operand_ambiguous:{cite}"
    if aggregate not in _AGGREGATES:
        return None, f"operand_aggregate_unsupported:{aggregate}"
    values = [f.get("value") for f in matched]
    if aggregate == "count":
        return len(values), None
    if aggregate == "first":
        return values[0], None
    if aggregate == "any":
        return values, None
    if aggregate == "all":
        # 布尔/是否类字段（如 has_stamp）：all=全部为真
        flags = [_as_bool(v) for v in values]
        if all(f is not None for f in flags):
            return all(flags), None  # type: ignore[arg-type]
        # 文本/数值类字段：all=多张单据取值是否一致；一致返回该值，不一致返回 None
        # （模型用它表达"每张发票的购货方都应与报销单所属公司一致"）
        normalized = {_norm_text(v) for v in values}
        return (values[0] if len(normalized) == 1 else None), None
    nums = [_to_number(v) for v in values]
    if any(n is None for n in nums):
        if aggregate in ("min", "max"):
            # 日期类字段（ISO 字符串可直接比大小）：min=最早、max=最晚
            dates = [_as_iso_date(v) for v in values]
            if all(d is not None for d in dates):
                return (min(dates) if aggregate == "min" else max(dates)), None
        if aggregate in ("min", "max", "sum"):
            return None, f"operand_not_numeric:{cite}"
        return values, None
    if aggregate == "sum":
        return sum(nums), None  # type: ignore[arg-type]
    if aggregate == "min":
        return min(nums), None  # type: ignore[arg-type]
    return max(nums), None  # type: ignore[arg-type]


def _eval_expression_operand(
    bundle: dict,
    expr: str,
    doc_filter: Optional[str],
    type_filter: Optional[str],
    aggregate: str = "",
) -> tuple[Optional[float], Optional[str]]:
    """把"金额+税额"这类复合字段表达式按单据原子字段求值。

    复用图谱引擎的拆词/求值（严格规则：`*`/`×` 直接当乘号，`+`/`-`/`/` 需两侧空白）；
    严格规则拆不动时再按"每个原子都是本单据字段"宽松拆一次（规则文本里常写"金额+税额"）。
    多张单据时按 aggregate 汇总；未声明聚合 → 报歧义（要求模型补 aggregate）。
    """
    text = str(expr or "")
    if not any(op in text for op in ("*", "×", "+", "/", "-", "－")):
        return None, None
    by_doc: dict[str, dict] = {}
    for fact in _facts_for_doc(bundle, doc_filter):
        if type_filter and fact.get("doc_type") != type_filter:
            continue
        by_doc.setdefault(str(fact.get("doc")), {})[str(fact.get("key"))] = fact.get("value")
    if not by_doc:
        return None, None
    values: list[float] = []
    for fields in by_doc.values():
        value = _eval_strict(fields, text)
        if value is None:
            value = _eval_loose(fields, text)
        if value is None:
            return None, None
        values.append(float(value))
    if len(values) == 1:
        return values[0], None
    if aggregate == "sum":
        return sum(values), None
    if aggregate == "count":
        return len(values), None
    if aggregate == "min":
        return min(values), None
    if aggregate == "max":
        return max(values), None
    if aggregate in ("first", "any"):
        return (values[0] if aggregate == "first" else values), None
    return None, f"operand_ambiguous:{expr}"


def _eval_strict(fields: dict, expr: str) -> Optional[float]:
    try:
        from .graph_review_service import _eval_field_expression  # 延迟导入避免循环

        shim = _BundleDoc(fields)
        value, _missing = _eval_field_expression(shim, expr)
        return value if isinstance(value, (int, float)) else None
    except Exception:
        return None


def _eval_loose(fields: dict, expr: str) -> Optional[float]:
    """宽松表达式：+ - / 两侧无空白时，逐段要求"是同一份单据的数值字段"再计算。"""
    parts = re.split(r"([+\-－/])", str(expr))
    if len(parts) < 3 or len(parts) % 2 == 0:
        return None
    nums: list[float] = []
    ops: list[str] = []
    for index, token in enumerate(parts):
        if index % 2 == 0:
            value = _to_number(fields.get(token.strip()))
            if value is None:
                return None
            nums.append(value)
        else:
            ops.append({"－": "-"}.get(token, token))
    total = nums[0]
    for op, nxt in zip(ops, nums[1:]):
        if op == "+":
            total += nxt
        elif op == "-":
            total -= nxt
        elif op == "/" and nxt:
            total /= nxt
        else:
            return None
    return total


class _BundleDoc:
    """把证据包里的字段映射成 _eval_field_expression 需要的 Document 形状。"""

    def __init__(self, fields: dict):
        self.fields = dict(fields)
        self.extracted_fields = self.fields


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")


def _as_iso_date(value: Any) -> Optional[str]:
    """把值识别成 ISO 日期字符串（日期排序按 ISO 字符串比较即可，无需转 datetime）。"""
    text = str(value).strip()
    if not _ISO_DATE_RE.match(text):
        return None
    return text[:10]


# ============ checks 重算（验证门的核心） ============

_CHECK_OPS = ("lt", "le", "gt", "ge", "eq", "ne", "contains", "in", "exists", "absent")

_CHECK_FUNCS = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "contains": lambda a, b: str(b) in str(a),
    "in": lambda a, b: str(a) in str(b),
}


def evaluate_check(bundle: dict, check: dict) -> dict:
    """按证据包重算一条 check。Returns {ok, computed, reason}。

    ok=False 且 reason 为 *_mismatch 时，代表大模型声明的结论与证据包算出来的不符
    （验证门据此打回并要求重判）。
    """
    if not isinstance(check, dict):
        return {"ok": False, "reason": "check_invalid"}
    op = str(check.get("op") or "").strip().lower()
    if op not in _CHECK_OPS:
        return {"ok": False, "reason": f"check_op_unsupported:{op or '-'}"}
    declared = check.get("ok")
    if not isinstance(declared, bool):
        return {"ok": False, "reason": "check_ok_missing"}

    left, err = resolve_operand(bundle, check.get("left"))
    if op in ("exists", "absent"):
        # "字段不存在"本身就是存在性判断的依据，不当作操作数解析失败
        if err and err.startswith("operand_not_found"):
            present = False
        elif err:
            return {"ok": False, "reason": err}
        else:
            present = bool(left) and str(left).strip() not in ("", "None")
        computed = present if op == "exists" else not present
        if computed != declared:
            return {
                "ok": False,
                "reason": "check_recompute_mismatch",
                "computed": computed,
            }
        return {"ok": True, "computed": computed}
    if err:
        return {"ok": False, "reason": err}

    right, err = resolve_operand(bundle, check.get("right"))
    if err:
        return {"ok": False, "reason": err}
    func = _CHECK_FUNCS.get(op)
    if func is None:
        return {"ok": False, "reason": f"check_op_unsupported:{op}"}
    try:
        if op in ("eq", "ne"):
            same, _partial = _values_equal(left, right)
            computed = same if op == "eq" else not same
        elif op in ("lt", "le", "gt", "ge"):
            ln, rn = _to_number(left), _to_number(right)
            if ln is not None and rn is not None:
                computed = bool(func(ln, rn))
            else:
                # 先按日期比（ISO 日期必须先判定，否则 "2026-04-25" 会被宽松数量解析
                # 当成 2026 这个数字，得到错误结论）
                ld, rd = _as_iso_date(left), _as_iso_date(right)
                if ld is not None and rd is not None:
                    computed = bool(func(ld, rd))
                else:
                    # 容错：阈值可能写成 "30日"/"1,234.50 元" 这类带单位的数量
                    lq, rq = _to_quantity(left), _to_quantity(right)
                    if lq is not None and rq is not None:
                        computed = bool(func(lq, rq))
                    else:
                        return {"ok": False, "reason": f"check_not_comparable:{op}"}
        else:
            computed = bool(func(left, right))
    except Exception as e:  # pragma: no cover - 防御
        return {"ok": False, "reason": f"check_eval_error:{e}"}
    if computed != declared:
        return {
            "ok": False,
            "reason": "check_recompute_mismatch",
            "computed": computed,
            "left": left,
            "right": right,
        }
    return {"ok": True, "computed": computed}


def bundle_payload(bundle: dict, *, max_chars: int = 60000) -> dict:
    """给提示词用的紧凑包（超长时按单据裁剪原文，不裁剪字段事实）。"""
    payload = {"docs": [], "stats": bundle.get("stats")}
    for entry in bundle.get("docs") or []:
        item = {k: v for k, v in entry.items() if k != "text_excerpt"}
        if entry.get("text_excerpt"):
            item["text_excerpt"] = str(entry["text_excerpt"])[:2000]
        payload["docs"].append(item)
    import json as _json

    if len(_json.dumps(payload, ensure_ascii=False, default=str)) > max_chars:
        for item in payload["docs"]:
            item.pop("text_excerpt", None)
    return payload
