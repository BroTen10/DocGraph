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

from app.services.graph_review_service import _evaluate_criterion, _expand_multi_values, _cmp_eq  # noqa: E402
from app.services.doc_normalizer import (  # noqa: E402
    is_currency_field,
    is_party_field,
    normalize_currency_value,
    normalize_party_name,
)
from app.services.field_extraction_service import normalize_fields  # noqa: E402
from app.services.llm_review_service import (  # noqa: E402
    _is_adjudication_candidate,
    _is_string_mismatch_item,
    semantic_adjudication_fallback,
    semantic_equivalence_fallback,
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

    print("LLM 语义复核：多文档同字段一致性（代收方详略不同假阳性）")
    consignee_a = "Schenker International, Av. Guadalupe 920-B, Zapopan, Jalisco 985010, Mexico"
    consignee_b = "Schenker International, SA CV"

    # 候选识别：self_consistency 字符串值进入复核；数值/日期仍走确定性结论
    cand = ReviewResult(
        result="fail",
        detail={"src_vals": [consignee_a, consignee_b], "tgt_vals": [consignee_a, consignee_b], "self_consistency": True},
        source="graph",
    )
    check("self_consistency 字符串不一致进入复核", _is_string_mismatch_item(cand) is True)
    numeric = ReviewResult(
        result="fail",
        detail={"src_vals": [100, 23600], "tgt_vals": [100, 23600], "self_consistency": True},
        source="graph",
    )
    check("self_consistency 数值不一致不进入复核", _is_string_mismatch_item(numeric) is False)
    no_self = ReviewResult(
        result="fail",
        detail={"src_vals": [consignee_a, consignee_b], "tgt_vals": [consignee_a, consignee_b]},
        source="graph",
    )
    check("缺少 self_consistency 标记不进入复核", _is_string_mismatch_item(no_self) is False)

    # 场景A：同一公司，一份含完整地址、一份仅法律主体名 → LLM 高置信判同义 → pass
    ra = ReviewResult(
        rule_text="订单多份文档的代收方必须一致",
        check_category="信息准确性",
        result="fail",
        issue_desc=f"订单 多份文档的代收方不一致: {[consignee_a, consignee_b]}",
        detail={
            "src_vals": [consignee_a, consignee_b],
            "tgt_vals": [consignee_a, consignee_b],
            "self_consistency": True,
        },
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {
            "results": [
                {
                    "index": 0,
                    "equivalent": True,
                    "confidence": 0.95,
                    "reason": "两值均为 Schenker International，地址/法律形式后缀差异不影响同一收货主体",
                }
            ]
        }
    )
    changed = semantic_equivalence_fallback(None, fake_contract, fake_docs, [ra])
    check("场景A 受影响 1 条", changed == 1)
    check("场景A 同义 → pass", ra.result == "pass", ra.result)
    check("场景A 状态 closed", ra.status == "closed", ra.status)
    check("场景A 记录语义复核证据", ra.detail.get("semantic_fallback") is True and "Schenker" in ra.detail.get("llm_evidence", ""))

    # 场景B：确实不同主体 → LLM 高置信判不同 → 保持 fail 并补充证据
    rb = ReviewResult(
        rule_text="订单多份文档的代收方必须一致",
        check_category="信息准确性",
        result="fail",
        issue_desc="订单 多份文档的代收方不一致: [甲, 乙]",
        detail={"src_vals": ["甲", "乙"], "tgt_vals": ["甲", "乙"], "self_consistency": True},
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {"results": [{"index": 0, "equivalent": False, "confidence": 0.9, "reason": "甲与乙为不同公司"}]}
    )
    changed = semantic_equivalence_fallback(None, fake_contract, fake_docs, [rb])
    check("场景B 受影响 1 条", changed == 1)
    check("场景B 确认不同 → 保持 fail", rb.result == "fail", rb.result)
    check("场景B 状态 open", rb.status == "open", rb.status)
    check("场景B 补充确认不一致", "确认不一致" in rb.issue_desc)

    # 场景C：LLM 无法确认 → 降级 unverifiable 待人工确认（护栏）
    rc = ReviewResult(
        rule_text="订单多份文档的代收方必须一致",
        check_category="信息准确性",
        result="fail",
        detail={"src_vals": [consignee_a, consignee_b], "tgt_vals": [consignee_a, consignee_b], "self_consistency": True},
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {"results": [{"index": 0, "equivalent": None, "confidence": 0.4, "reason": "无法确认"}]}
    )
    changed = semantic_equivalence_fallback(None, fake_contract, fake_docs, [rc])
    check("场景C 受影响 1 条", changed == 1)
    check("场景C 无法确认 → unverifiable", rc.result == "unverifiable", rc.result)
    check("场景C 标记人工确认", rc.detail.get("reason") == "llm_uncertain_semantic")

    # 场景D：三份文档，任一对无法确认 → 整体 unverifiable（不贸然 pass/fail）
    rd = ReviewResult(
        rule_text="订单多份文档的代收方必须一致",
        check_category="信息准确性",
        result="fail",
        detail={
            "src_vals": [consignee_a, consignee_b, "Schenker International de Mexico"],
            "tgt_vals": [consignee_a, consignee_b, "Schenker International de Mexico"],
            "self_consistency": True,
        },
        source="graph",
    )
    llm_review_module.get_llm_client = lambda: _FakeLLM(
        {
            "results": [
                {"index": 0, "equivalent": True, "confidence": 0.95, "reason": "同一主体"},
                {"index": 1, "equivalent": None, "confidence": 0.5, "reason": "无法确认"},
            ]
        }
    )
    changed = semantic_equivalence_fallback(None, fake_contract, fake_docs, [rd])
    check("场景D 受影响 1 条", changed == 1)
    check("场景D 任一对不确定 → unverifiable", rd.result == "unverifiable", rd.result)
    check("场景D 置信度取最低", rd.confidence == 0.5, str(rd.confidence))

    print("主体名称归一：代收方/承运人地址与法律后缀详略差异")
    schenker_addr = "Schenker International, Av. Guadalupe 920-B, Zapopan, Jalisco 985010, Mexico"
    schenker_suffix = "Schenker International, SA CV"
    core = normalize_party_name(schenker_addr)
    check("代收方 是主体字段", is_party_field("代收方") is True)
    check("承运人 是主体字段", is_party_field("承运人") is True)
    check("卖方名称 是主体字段", is_party_field("卖方名称") is True)
    check("买方订单号 不是主体字段", is_party_field("买方订单号") is False)
    check("买方地址 不是主体字段", is_party_field("买方地址") is False)
    check("商品名称 不是主体字段", is_party_field("商品名称") is False)
    check("完整地址 vs 法律后缀 归一相同", normalize_party_name(schenker_suffix) == core,
          f"{normalize_party_name(schenker_suffix)!r} vs {core!r}")
    check("无逗号写法也归一相同", normalize_party_name("Schenker International SA CV") == core)
    check("大小写差异归一相同", normalize_party_name("schenker INTERNATIONAL") == core)
    check("空值返回 None", normalize_party_name(None) is None)
    check("纯标点返回 None", normalize_party_name("---") is None)

    def _party_doc(name, val, doc_type="订单", field="代收方"):
        return SimpleNamespace(
            file_name=name, doc_type=doc_type,
            extracted_fields={field: val},
            has_stamp=False, ocr_confidence=1.0,
        )

    # 端到端：_cmp_eq 多文档一致性直接判 pass（确定性，不再依赖 LLM）
    res, desc, det = _cmp_eq(
        "订单", "代收方", "订单", "代收方",
        [_party_doc("订单A.pdf", schenker_addr), _party_doc("订单B.pdf", schenker_suffix)],
    )
    check("代收方地址/后缀详略不同 → pass", res == "pass", desc)
    check("记录主体归一证据", det.get("party_name_norm") == [core, core], str(det.get("party_name_norm")))

    # 跨文档（不同文件类型同主体字段）同样归一判等
    res2, desc2, _ = _cmp_eq(
        "订单", "代收方", "运单", "收货方",
        [
            _party_doc("订单.pdf", schenker_addr, "订单", "代收方"),
            _party_doc("运单.pdf", schenker_suffix, "运单", "收货方"),
        ],
    )
    check("跨文档主体详略不同 → pass", res2 == "pass", desc2)

    # 不同主体仍 fail（归一化不掩盖真实差异）
    res3, desc3, _ = _cmp_eq(
        "订单", "代收方", "订单", "代收方",
        [_party_doc("订单A.pdf", schenker_suffix), _party_doc("订单B.pdf", "DHL Global Forwarding")],
    )
    check("不同承运人仍 fail", res3 == "fail", desc3)

    # 非主体字段不受归一化影响（数值/单号类保持原判）
    res4, desc4, _ = _cmp_eq(
        "订单", "数量", "订单", "数量",
        [_party_doc("订单A.pdf", 100, field="数量"), _party_doc("订单B.pdf", 200, field="数量")],
    )
    check("数值字段不一致仍 fail", res4 == "fail", desc4)

    print("全部通过")


if __name__ == "__main__":
    main()
