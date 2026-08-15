# -*- coding: utf-8 -*-
"""条件求值单元测试：OCR 多行字段以 ";" 拼接时，条件判据不应误判。

回归背景：报关单币别提取为 "美元;美元"（多行拼接）时，
条件 "币种==美元" 曾因整体字符串比较不相等而被误判为 not_met，
导致 "若报关单币种为美元，则报关单金额应等于增值税发票共计美元"
这类规则被跳过、断言未执行。

批次 12 扩展：
- 币别值别名归一（USD/美金/美圆 → 美元）在条件求值、写时归一两个层面生效；
- LLM 语义裁决通道的候选识别（条件未知 / 条件值语义歧义 / 断言不可核验）。

运行：backend/.venv/Scripts/python.exe tests/test_condition_evaluation.py
前置：无需数据库 / Neo4j（仅导入求值函数）。
"""

from __future__ import annotations

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.graph_review_service import _evaluate_criterion, _expand_multi_values  # noqa: E402
from app.services.doc_normalizer import is_currency_field, normalize_currency_value  # noqa: E402
from app.services.field_extraction_service import normalize_fields  # noqa: E402
from app.services.llm_review_service import (  # noqa: E402
    _is_adjudication_candidate,
    semantic_adjudication_fallback,
)
import app.services.llm_review_service as llm_review_module  # noqa: E402
from app.models import ReviewResult  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        raise SystemExit(1)


def main() -> None:
    print("币别值归一（批次 12）")
    check("美元;美元 -> 美元", normalize_currency_value("美元;美元") == "美元")
    check("USD -> 美元", normalize_currency_value("USD") == "美元")
    check("全角分号 美元；人民币 -> 美元;人民币", normalize_currency_value("美元；人民币") == "美元;人民币")
    check("非字符串原样保留", normalize_currency_value(3706.25) == 3706.25)
    check("币制 是币别字段", is_currency_field("币制") is True)
    check("币种 是币别字段", is_currency_field("币种") is True)
    check("金额 不是币别字段", is_currency_field("金额") is False)

    print("写时归一：normalize_fields")
    norm = normalize_fields("出口报关单", {"币别": "USD;美元", "总价": "3706.25"})
    check("币别 USD;美元 -> 美元", norm.get("币别") == "美元", str(norm))
    check("总价保留数值", norm.get("总价") == 3706.25, str(norm))

    print("展开多值字段")
    check("美元;美元 -> [美元, 美元]", _expand_multi_values(["美元;美元"]) == ["美元", "美元"])
    check("全角分号 美元；美元 -> [美元, 美元]", _expand_multi_values(["美元；美元"]) == ["美元", "美元"])
    check("数字原样保留", _expand_multi_values([3706.25]) == [3706.25])

    print("条件求值")
    cases = [
        ("多行币别 美元;美元 等于 美元 -> met", {"operator": "等于", "value": "美元"}, ["美元;美元"], (True, True)),
        ("单值币别 美元 等于 美元 -> met", {"operator": "等于", "value": "美元"}, ["美元"], (True, True)),
        ("多行币别 美元;美元 等于 人民币 -> not_met", {"operator": "等于", "value": "人民币"}, ["美元;美元"], (False, True)),
        ("数值多行 3706.25;3706.25 等于 3706.25 -> met", {"operator": "等于", "value": "3706.25"}, ["3706.25;3706.25"], (True, True)),
        ("数值 438.34 等于 3706.25 -> not_met", {"operator": "等于", "value": "3706.25"}, ["438.34"], (False, True)),
        ("币别 USD 不等于 美元 -> met", {"operator": "不等于", "value": "美元"}, ["USD"], (True, True)),
        ("币别别名 USD 等于 美元 -> met", {"field": "币别", "operator": "等于", "value": "美元"}, ["USD"], (True, True)),
        ("币别别名 美金 等于 美元 -> met", {"field": "币别", "operator": "等于", "value": "美元"}, ["美金"], (True, True)),
        ("币别 美元 等于 USD -> met", {"field": "币别", "operator": "等于", "value": "USD"}, ["美元"], (True, True)),
        ("币别 人民币 等于 美元 -> not_met", {"field": "币别", "operator": "等于", "value": "美元"}, ["人民币"], (False, True)),
    ]
    for name, criterion, values, expected in cases:
        got = _evaluate_criterion(criterion, values)
        check(name, got == expected, f"got={got}, expected={expected}")

    print("LLM 语义裁决候选识别（引擎 B-3）")
    skip_clean = ReviewResult(
        result="pass",
        detail={
            "skipped_reason": "condition_not_met",
            "condition": {"field": "币别", "value": "美元", "values": ["人民币"]},
        },
        source="graph",
    )
    check("干净不满足（人民币 vs 美元）不进入裁决", _is_adjudication_candidate(skip_clean) is False)
    skip_suspicious = ReviewResult(
        result="pass",
        detail={
            "skipped_reason": "condition_not_met",
            "condition": {"field": "币别", "value": "美元", "values": ["USD"]},
        },
        source="graph",
    )
    check("别名变体（USD vs 美元）进入裁决", _is_adjudication_candidate(skip_suspicious) is True)
    unknown_cond = ReviewResult(
        result="unverifiable",
        detail={
            "reason": "condition_field_missing",
            "condition": {"field": "币别", "value": "美元", "values": []},
        },
        source="graph",
    )
    check("条件字段缺失（unverifiable）进入裁决", _is_adjudication_candidate(unknown_cond) is True)
    llm_result = ReviewResult(
        result="unverifiable",
        detail={"condition": {"field": "币别"}},
        source="llm",
    )
    check("已由 LLM 判定的结果不二次裁决", _is_adjudication_candidate(llm_result) is False)

    print("LLM 语义裁决执行（假 LLM，护栏验证）")

    class _FakeLLM:
        def __init__(self, payload):
            self._payload = payload

        def chat_json(self, messages, temperature=None, max_tokens=None):
            return self._payload

    fake_contract = SimpleNamespace(
        contract_no="1-514120250410348977",
        alias_list=["1-514120250410348977"],
    )
    fake_docs = [
        SimpleNamespace(
            file_name="报关单.pdf",
            doc_type="出口报关单",
            has_stamp=True,
            ocr_confidence=0.9,
            extracted_fields={"币别": "USD", "总价": 3706.25},
        )
    ]

    # 场景1：条件疑似误判（USD vs 美元），LLM 高置信确认条件成立且断言通过
    r1 = ReviewResult(
        rule_text="若报关单币种为美元，则报关单金额应等于增值税发票共计美元",
        check_category="信息准确性",
        result="pass",
        graph_source="出口报关单.总价",
        graph_target="增值税发票.共计美元",
        detail={
            "skipped_reason": "condition_not_met",
            "condition": {"field": "币别", "value": "美元", "values": ["USD"]},
            "evidence": {
                "source": {"docs": [{"doc_name": "报关单.pdf", "value": 3706.25}]},
                "target": {"docs": [{"doc_name": "发票.pdf", "value": "3706.25"}]},
            },
        },
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {"results": [{"index": 0, "condition_met": True, "result": "pass", "confidence": 0.95, "reason": "USD 即美元"}]}
    )
    changed = semantic_adjudication_fallback(None, fake_contract, fake_docs, [r1])
    check("场景1 受影响 1 条", changed == 1)
    check("场景1 结果仍为 pass", r1.result == "pass", r1.result)
    check("场景1 记录裁决证据", r1.detail.get("semantic_adjudication") is True and r1.detail.get("llm_condition_met") is True)

    # 场景2：条件未知（字段缺失），LLM 无法确认 → 保持 unverifiable 待人工
    r2 = ReviewResult(
        rule_text="若报关单币种为美元，则报关单金额应等于增值税发票共计美元",
        check_category="信息准确性",
        result="unverifiable",
        detail={
            "reason": "condition_field_missing",
            "condition": {"field": "币别", "value": "美元", "values": []},
        },
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {"results": [{"index": 0, "condition_met": None, "result": "unverifiable", "confidence": 0.3, "reason": "无币别证据"}]}
    )
    changed = semantic_adjudication_fallback(None, fake_contract, fake_docs, [r2])
    check("场景2 受影响 1 条", changed == 1)
    check("场景2 保持 unverifiable", r2.result == "unverifiable", r2.result)
    check("场景2 标记需人工确认", r2.detail.get("reason") == "llm_uncertain_adjudication")

    # 场景3：条件成立但断言不通过，LLM 高置信判 fail
    r3 = ReviewResult(
        rule_text="若报关单币种为美元，则报关单金额应等于增值税发票共计美元",
        check_category="信息准确性",
        result="pass",
        detail={
            "skipped_reason": "condition_not_met",
            "condition": {"field": "币别", "value": "美元", "values": ["USD"]},
            "evidence": {
                "source": {"docs": [{"doc_name": "报关单.pdf", "value": 438.34}]},
                "target": {"docs": [{"doc_name": "发票.pdf", "value": "3706.25"}]},
            },
        },
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {"results": [{"index": 0, "condition_met": True, "result": "fail", "confidence": 0.9, "reason": "总价 438.34 与共计美元 3706.25 不一致"}]}
    )
    changed = semantic_adjudication_fallback(None, fake_contract, fake_docs, [r3])
    check("场景3 受影响 1 条", changed == 1)
    check("场景3 改为 fail", r3.result == "fail", r3.result)
    check("场景3 状态 open", r3.status == "open", r3.status)

    print("全部通过")


if __name__ == "__main__":
    main()
