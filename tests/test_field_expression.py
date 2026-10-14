# -*- coding: utf-8 -*-
"""复合字段表达式单元测试（A：图谱引擎表达式支持 + B：兜底裁决放宽）。

回归背景：规则断言字段可能写作复合算式（"件数*数量"、"数量*箱数"），
这类名字在单据 OCR 字段里不存在，确定性引擎曾一律判「字段数据缺失 → 无法核验」，
即便 报关单.件数=1、报关单.数量=990、送货单.数量=990、送货单.箱数=1 数据齐全。

运行：backend/.venv/Scripts/python.exe tests/test_field_expression.py
前置：无需数据库 / Neo4j（仅导入求值函数 + 构造未入库的 Document 对象）。
"""

from __future__ import annotations

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from types import SimpleNamespace  # noqa: E402

from app.models import Document  # noqa: E402
from app.services.graph_review_service import (  # noqa: E402
    _aggregate_field,
    _cmp_eq,
    _collect_field_evidence,
    _get_field,
    _split_field_expr,
    _to_number,
)
from app.services.llm_review_service import _is_adjudication_candidate  # noqa: E402


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        raise SystemExit(1)


def _doc(doc_type: str, file_name: str, **fields):
    return Document(doc_type=doc_type, file_name=file_name, extracted_fields=dict(fields))


def main() -> None:
    print("宽容数值解析 _to_number")
    check('"1" -> 1.0', _to_number("1") == 1.0)
    check('"1,234.5" -> 1234.5', _to_number("1,234.5") == 1234.5)
    check('"￥1,234.50" -> 1234.5', _to_number("￥1,234.50") == 1234.5)
    check('"12.5%" -> 12.5', _to_number("12.5%") == 12.5)
    check("int -> float", _to_number(3) == 3.0)
    check("None -> None", _to_number(None) is None)
    check("bool 不算数值", _to_number(True) is None)
    check('"abc" -> None', _to_number("abc") is None)

    print("表达式拆词 _split_field_expr")
    check("件数*数量", _split_field_expr("件数*数量") == ["件数", "*", "数量"])
    check("数量*箱数", _split_field_expr("数量*箱数") == ["数量", "*", "箱数"])
    check("全角乘号 件数×数量", _split_field_expr("件数×数量") == ["件数", "*", "数量"])
    check("空格加号 数量 + 件数", _split_field_expr("数量 + 件数") == ["数量", "+", "件数"])
    check("三原子 件数*数量*1", _split_field_expr("件数*数量*1") == ["件数", "*", "数量", "*", "1"])
    check("字段名 订单号/合同号 不是表达式", _split_field_expr("订单号/合同号") is None)
    check("普通字段 数量 不是表达式", _split_field_expr("数量") is None)

    print("_get_field：复合字段按原子字段求值")
    customs = _doc("出口报关单", "报关单.pdf", 件数=1.0, 数量=990.0, 总价=2932.88)
    delivery = _doc("送货单", "送货单.pdf", 数量=990.0, 箱数="1", **{"订单号/合同号": "1012866493"})
    check("报关单 件数*数量 = 990", _get_field(customs, "件数*数量") == 990.0)
    check("送货单 数量*箱数 = 990（箱数为字符串）", _get_field(delivery, "数量*箱数") == 990.0)
    check(
        "带斜杠的真实字段名不被当除法",
        _get_field(delivery, "订单号/合同号") == "1012866493",
    )
    check("原子字段缺失 → None", _get_field(_doc("送货单", "d.pdf", 数量=990.0), "数量*箱数") is None)

    print("断言比对 _cmp_eq：复合字段应可确定性命中")
    result, issue, detail = _cmp_eq("出口报关单", "件数*数量", "送货单", "数量*箱数", [customs, delivery], aggregate="SUM")
    check("结果 pass", result == "pass", f"got {result} / {issue}")
    check("两侧合计均 990", detail.get("src_sum") == 990.0 and detail.get("tgt_sum") == 990.0, str(detail))

    print("缺失原子字段的证据要指到具体字段")
    _, missing = _collect_field_evidence([customs, _doc("送货单", "d.pdf", 数量=990.0)], "送货单", "数量*箱数")
    check("missing_atoms 含 箱数", missing and missing[0].get("missing_atoms") == ["箱数"], str(missing))
    result2, issue2, detail2 = _cmp_eq(
        "出口报关单", "件数*数量", "送货单", "数量*箱数",
        [customs, _doc("送货单", "d.pdf", 数量=990.0)], aggregate="SUM",
    )
    check("缺原子字段 → unverifiable", result2 == "unverifiable", f"got {result2}")
    check(
        "证据里带 missing_atoms",
        any(m.get("missing_atoms") == ["箱数"] for m in detail2.get("missing_fields", [])),
        str(detail2),
    )
    _, missing_atom_text = _collect_field_evidence([_doc("出口报关单", "c.pdf", 件数="一", 数量=990.0)], "出口报关单", "件数*数量")
    check("非数值原子也报缺失", missing_atom_text and missing_atom_text[0].get("missing_atoms") == ["件数"], str(missing_atom_text))

    print("_aggregate_field：复合字段求和")
    total, agg_missing = _aggregate_field([customs], "出口报关单", "件数*数量")
    check("合计 990", total == 990.0, f"got {total}")
    check("无缺失", agg_missing == [], str(agg_missing))

    print("B：兜底裁决准入放宽")
    check(
        "两侧 doc 采集为空但文档存在（field_missing）→ 进裁决",
        _is_adjudication_candidate(SimpleNamespace(
            source="graph", result="unverifiable",
            detail={
                "reason": "field_data_missing",
                "evidence": {
                    "source": {"docs": [], "missing_fields": [{"field": "件数*数量", "reason": "field_missing", "doc_files": ["报关单.pdf"]}]},
                    "target": {"docs": [], "missing_fields": [{"field": "数量*箱数", "reason": "field_missing", "doc_files": ["送货单.pdf"]}]},
                },
            },
        )) is True,
    )
    check(
        "该类型单据本身不存在（no_docs_of_type）→ 不进裁决",
        _is_adjudication_candidate(SimpleNamespace(
            source="graph", result="unverifiable",
            detail={
                "reason": "field_data_missing",
                "evidence": {
                    "source": {"docs": [], "missing_fields": [{"field": "数量", "reason": "no_docs_of_type", "doc_files": []}]},
                    "target": {"docs": [], "missing_fields": [{"field": "数量", "reason": "no_docs_of_type", "doc_files": []}]},
                },
            },
        )) is False,
    )
    check(
        "某侧已采集到值 → 仍进裁决（原行为不回退）",
        _is_adjudication_candidate(SimpleNamespace(
            source="graph", result="unverifiable",
            detail={
                "reason": "field_data_missing",
                "evidence": {
                    "source": {"docs": [], "missing_fields": [{"field": "件数*数量", "reason": "field_missing", "doc_files": ["报关单.pdf"]}]},
                    "target": {"docs": [{"value": 990.0}], "missing_fields": []},
                },
            },
        )) is True,
    )

    print("\n全部通过")


if __name__ == "__main__":
    main()
