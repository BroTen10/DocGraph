# -*- coding: utf-8 -*-
"""统一判定架构回归测试（证据包 + 带引用判定 + 机器验证门 + 绑定沉淀）。

背景：此前的审查判定是"图引擎 → 语义复核 → 语义裁决 → 全量复核 → 兜底收口"五段串联，
每段一个候选筛选器、每段都可能静默丢弃，问题表现为"字段名对不上就判无法核验"。
现在收敛为一条路径：

    意图编译（条款 + 意图 + 确定性初判）
      + 证据包（单据事实 + 原文出处）
      → 大模型带引用判定（citations / checks / bindings）
      → Python 验证门（引用存在性 + 算术重算 + 护栏阈值）
      → 通过落库 / 不通过带原因重试一次 / 仍不通过交人工

本测试验证：
1. 证据包：事实带原文出处；引用的三种解析（精确 / 单位等价 resolved_via / 片段部分引用）；
2. 验证门：引用不存在、算术重算不符、fail 置信度不足、条件与结论冲突 → 一律打回；
3. 治理：打回 → 带失败原因重试一次 → 仍不通过 → 显性「需人工确认」（不静默停在无法核验）；
4. 智能：确定性初判"无法核验/不通过"时，大模型可给出正确结论并覆盖（覆盖留痕 judge_override）；
5. 意图编译：条款条件/义务/例外/建模疑点提示进入意图，未覆盖规则也送审（无静默丢弃）。

运行：backend/.venv/bin/python tests/test_review_judge.py
前置：无需数据库 / 网络（LLM 客户端用替身替换）。
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.llm_client import LLMError  # noqa: E402
from app.models import Document, ReviewResult, Rule  # noqa: E402
import app.services.review_judge as judge  # noqa: E402
from app.services.review_evidence import (  # noqa: E402
    build_evidence_bundle,
    candidate_keys,
    evaluate_check,
    resolve_citation,
    resolve_operand,
)
from app.services.review_intent import compile_intents, field_semantics  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


# ============ 测试数据：01-合规报销 BX-2026-0312（用户报告的那条） ============

BAOXIAO_FILE = "BX-2026-0312_费用报销单.docx"
BAOXIAO_TEXT = "\n".join(
    [
        "费用报销单",
        "报销单号：BX-2026-0312",
        "所属公司：深圳市远航进出口有限公司",
        "申请人：张伟",
        "报销金额：4,860.00",
        "审批签字：部门负责人 李强",
        "备注：本报销单为端到端测试用模拟单据。",
    ]
)


def baoxiao_doc(**extra) -> Document:
    fields = {
        "报销金额": 4860.0,
        "审批签字": "部门负责人 李强",
        "申请人": "张伟",
        "所属公司": "深圳市远航进出口有限公司",
    }
    fields.update(extra)
    return Document(
        doc_type="报销单",
        file_name=BAOXIAO_FILE,
        extracted_fields=fields,
        ocr_text=BAOXIAO_TEXT,
        ocr_confidence=0.98,
        has_stamp=False,
    )


def invoice_doc(file_name: str, amount: float) -> Document:
    return Document(
        doc_type="增值税发票",
        file_name=file_name,
        extracted_fields={"价税合计": amount, "购货方": "深圳市远航进出口有限公司"},
        ocr_text=f"增值税发票\n价税合计：{amount}\n购货方：深圳市远航进出口有限公司",
        ocr_confidence=0.97,
        has_stamp=True,
    )


def rule_0016() -> Rule:
    return Rule(
        rule_set_id=None,  # 单测不落库
        rule_no=16,
        rule_text="报销金额达到5000元及以上的报销单，应同时具备部门负责人与财务负责人签字审批。",
        doc_type="报销单",
        check_category="信息准确性",
        enabled=True,
        priority=30,
        status="confirmed",
        structure=None,
        scope={"intents": ["信息准确性", "审批层级"], "doc_types": ["报销单"]},
        intents=["信息准确性"],
        tolerance={"time_days": 0, "weight_kg": 0, "amount_percent": 0},
    )


GRAPH_0016 = {
    "nodes": [
        {
            "name": "报销单.报销金额",
            "type": "Field",
            "properties": {"doc_type": "报销单", "field": "报销金额"},
        },
        {
            "name": "报销单.部门负责人签字",
            "type": "Field",
            "properties": {"doc_type": "报销单", "field": "部门负责人签字"},
        },
    ],
    "edges": [
        {
            "source": "报销单.报销金额",
            "target": "报销单.部门负责人签字",
            "type": "COMPARE_TO",
            "properties": {
                "rule_id": "R0016",
                "operator": "不小于",
                "check_category": "信息准确性",
                "rule_text": "报销金额达到5000元及以上的报销单，应同时具备部门负责人与财务负责人签字审批。",
                "condition": None,
                "tolerance_params": {"time_days": 0},
            },
        }
    ],
}

INTENT_0016 = "R0016#报销单.报销金额->报销单.部门负责人签字"


def prelim_unverifiable() -> ReviewResult:
    """复刻旧链路在这条规则上的输出：字段名对不上 → 无法核验。"""
    return ReviewResult(
        rule_text="报销金额达到5000元及以上的报销单，应同时具备部门负责人与财务负责人签字审批。",
        check_category="信息准确性",
        doc_type="报销单",
        result="unverifiable",
        status="open",
        status_history=[],
        source="graph",
        graph_source="报销单.报销金额",
        graph_target="报销单.部门负责人签字",
        issue_desc="报销单.报销金额 或 报销单.部门负责人签字 字段数据缺失",
        detail={
            "rule_id": "R0016",
            "reason": "field_data_missing",
            "operator": "不小于",
            "evidence": {
                "source": {
                    "docs": [],
                    "missing_fields": [
                        {"field": "报销金额", "reason": "field_missing", "doc_files": [BAOXIAO_FILE]}
                    ],
                },
                "target": {
                    "docs": [],
                    "missing_fields": [
                        {"field": "部门负责人签字", "reason": "field_missing", "doc_files": [BAOXIAO_FILE]}
                    ],
                },
            },
        },
    )


class _FakeLLM:
    """chat_json 替身：按调用次序返回 payload（列表按次取，最后一个可重复）。"""

    def __init__(self, payloads):
        if isinstance(payloads, (dict, Exception)):
            payloads = [payloads]
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self.payloads) - 1)
        payload = self.payloads[idx]
        if isinstance(payload, Exception):
            raise payload
        return payload


def _patch_llm(fake: _FakeLLM) -> None:
    judge.get_llm_client = lambda: fake  # type: ignore[assignment]


def _verdict_pass_condition_not_met() -> dict:
    return {
        "results": [
            {
                "intent_id": INTENT_0016,
                "rule_id": "R0016",
                "verdict": "pass",
                "condition_met": False,
                "reason": "报销金额 4860.0 元 < 5000 元，触发条件不成立，规则不适用。",
                "confidence": 0.95,
                "checks": [
                    {"op": "lt", "left": {"cite": "报销金额"}, "right": 5000, "ok": True}
                ],
                "citations": [
                    {"doc": BAOXIAO_FILE, "key": "报销金额", "value": 4860.0}
                ],
                "bindings": [],
            }
        ]
    }


# ============ 1. 证据包 ============

def test_evidence_bundle() -> None:
    print("1. 证据包（事实 + 原文出处 + 引用解析）")
    bundle = build_evidence_bundle([baoxiao_doc()])
    stats = bundle["stats"]
    check("事实条数（4 字段 + 1 文档级印章 + 1 派生签字角色）", stats["facts"] == 6, str(stats))
    check("事实带原文出处", stats["facts_with_raw"] >= 3, str(stats))
    fact = next(f for f in bundle["docs"][0]["facts"] if f["key"] == "报销金额")
    check("原文行可回溯", fact.get("raw") == "报销金额：4,860.00", str(fact))
    check("行号可回溯", fact.get("line") == 5, str(fact.get("line")))

    ok = resolve_citation(bundle, BAOXIAO_FILE, "报销金额", 4860.0)
    check("精确引用通过", ok["ok"] is True, str(ok))
    wrong = resolve_citation(bundle, BAOXIAO_FILE, "报销金额", 9999.0)
    check("值不符被打回", wrong["ok"] is False and "value_mismatch" in wrong["reason"], str(wrong))
    ghost = resolve_citation(bundle, BAOXIAO_FILE, "财务负责人签字", "王芳")
    check("引用不存在的字段被打回", ghost["ok"] is False, str(ghost))

    unit_doc = baoxiao_doc()
    unit_doc.extracted_fields = {"报销金额（元）": 4860.0, "审批签字": "部门负责人 李强"}
    unit_bundle = build_evidence_bundle([unit_doc])
    resolved = resolve_citation(unit_bundle, BAOXIAO_FILE, "报销金额", 4860.0)
    check(
        "单位后缀等价键可解析（resolved_via 供沉淀）",
        resolved["ok"] is True and resolved.get("resolved_via") == "报销金额（元）",
        str(resolved),
    )
    candidates = candidate_keys(unit_bundle, "报销金额")
    check("等价键候选供提示与沉淀", candidates and candidates[0]["key"] == "报销金额（元）", str(candidates))

    partial = resolve_citation(
        build_evidence_bundle([baoxiao_doc(审批签字="部门负责人 李强；财务负责人 未签字")]),
        BAOXIAO_FILE,
        "审批签字",
        "部门负责人 李强",
    )
    check("整格中的片段可作部分引用", partial["ok"] is True and partial.get("partial") is True, str(partial))


def test_operand_and_checks() -> None:
    print("2. 验证门的算术重算（checks）")
    docs = [baoxiao_doc(), invoice_doc("发票1.docx", 3200.0), invoice_doc("发票2.docx", 1660.0)]
    bundle = build_evidence_bundle(docs)
    total, err = resolve_operand(
        bundle, {"cite": "价税合计", "aggregate": "sum"}
    )
    check("多单求和由 Python 计算", total == 4860.0 and err is None, f"{total}/{err}")
    _ambiguous, amb_err = resolve_operand(bundle, {"cite": "价税合计"})
    check("多值未声明聚合 → 报歧义（要求模型补 aggregate）", amb_err is not None, str(amb_err))
    count, _ = resolve_operand(bundle, {"cite": "价税合计", "aggregate": "count"})
    check("计数聚合", count == 2, str(count))

    good = evaluate_check(
        bundle,
        {
            "op": "eq",
            "left": {"cite": "价税合计", "aggregate": "sum"},
            "right": {"cite": "报销金额"},
            "ok": True,
        },
    )
    check("模型声明正确 → 通过", good["ok"] is True, str(good))
    bad = evaluate_check(
        bundle,
        {"op": "gt", "left": {"cite": "报销金额"}, "right": 5000, "ok": True},
    )
    check(
        "模型算错 → 打回（check_recompute_mismatch）",
        bad["ok"] is False and bad["reason"] == "check_recompute_mismatch",
        str(bad),
    )
    missing = evaluate_check(
        bundle, {"op": "exists", "left": {"cite": "财务负责人签字"}, "ok": True}
    )
    check(
        "存在性声明与证据包不符 → 打回",
        missing["ok"] is False and missing["reason"] == "check_recompute_mismatch",
        str(missing),
    )


# ============ 3. 意图编译 ============

def test_gate_self_contradiction() -> None:
    print("2.5 验证门：结论与自身声明矛盾 → 打回")
    bundle = build_evidence_bundle([baoxiao_doc()])
    intent = {"rule_id": "R0016", "intent_id": INTENT_0016, "obligation": {"kind": "compare"}}
    contradictory = {
        "intent_id": INTENT_0016,
        "rule_id": "R0016",
        "verdict": "fail",  # 理由/check 都说相等，却判不通过
        "condition_met": None,
        "reason": "两者相等，金额一致",
        "confidence": 0.95,
        "checks": [{"op": "eq", "left": {"cite": "报销金额"}, "right": 4860.0, "ok": True}],
        "citations": [{"doc": BAOXIAO_FILE, "key": "报销金额", "value": 4860.0}],
    }
    ok, errors, _meta, _dropped = judge.verify_verdict(bundle, intent, contradictory)
    check("全部 check 通过却判 fail → 打回", not ok and "fail_without_failing_check" in errors, str(errors))

    consistent = dict(contradictory)
    consistent["verdict"] = "pass"
    ok, errors, _meta, _dropped = judge.verify_verdict(bundle, intent, consistent)
    check("改为 pass 后通过", ok, str(errors))

    reverse = dict(contradictory)
    reverse["verdict"] = "pass"
    reverse["checks"] = [{"op": "eq", "left": {"cite": "报销金额"}, "right": 9999.0, "ok": False}]
    ok, errors, _meta, _dropped = judge.verify_verdict(bundle, intent, reverse)
    check("有失败 check 却判 pass → 打回", not ok and "pass_with_failing_check" in errors, str(errors))

    # 日期可比 + 印章可引用 + 复合表达式可重算
    docs = [baoxiao_doc(), invoice_doc("发票1.docx", 3200.0), invoice_doc("发票2.docx", 1660.0)]
    b2 = build_evidence_bundle(docs)
    check(
        "复合表达式求和（金额+税额 风格）",
        resolve_operand(b2, {"cite": "价税合计", "aggregate": "sum"})[0] == 4860.0,
    )
    check(
        "印章事实可引用",
        resolve_operand(b2, {"cite": "has_stamp", "aggregate": "count"})[0] == 3,
    )
    date_bundle = build_evidence_bundle(
        [
            Document(doc_type="报销单", file_name="bx.docx",
                     extracted_fields={"提交日期": "2026-03-09", "费用发生日期": "2026-03-05"},
                     ocr_confidence=0.9),
        ]
    )
    stamp_bundle = build_evidence_bundle(
        [
            invoice_doc("发票1.docx", 3200.0),
            Document(doc_type="增值税发票", file_name="发票2.docx",
                     extracted_fields={"价税合计": 1660.0}, has_stamp=False, ocr_confidence=0.9),
        ]
    )
    check(
        "全部盖章（has_stamp all）可重算",
        evaluate_check(
            stamp_bundle,
            {"op": "eq", "left": {"cite": "has_stamp", "aggregate": "all"}, "right": True, "ok": True},
        )["ok"]
        is False,
    )
    check(
        "任一盖章（has_stamp any）可重算",
        evaluate_check(
            stamp_bundle,
            {"op": "eq", "left": {"cite": "has_stamp", "aggregate": "any"}, "right": True, "ok": True},
        )["ok"]
        is True,
    )
    check(
        "日期必须先按日期比（不能被宽松数量解析成年份）",
        evaluate_check(
            date_bundle,
            {"op": "le", "left": {"cite": "提交日期"}, "right": {"cite": "费用发生日期"}, "ok": False},
        )["ok"]
        is True,
    )
    check(
        "签字角色派生事实（未签字不派生）",
        [f["key"] for f in build_evidence_bundle(
            [baoxiao_doc(审批签字="部门负责人 李强；财务负责人 未签字")]
        )["docs"][0]["facts"]].count("签字角色:财务负责人")
        == 0,
    )
    check(
        "签字角色派生事实（已签派生）",
        any(
            f["key"] == "签字角色:部门负责人"
            for f in build_evidence_bundle([baoxiao_doc()])["docs"][0]["facts"]
        ),
    )
    check(
        "日期间隔天数由 Python 计算（date_diff）",
        evaluate_check(
            date_bundle,
            {
                "op": "le",
                "left": {
                    "date_diff": {
                        "from": {"cite": "费用发生日期"},
                        "to": {"cite": "提交日期"},
                    }
                },
                "right": 30,
                "ok": True,
            },
        )["ok"]
        is True,
    )
    check(
        "阈值带单位也能重算（30日）",
        evaluate_check(
            date_bundle,
            {
                "op": "le",
                "left": {
                    "date_diff": {
                        "from": {"cite": "费用发生日期"},
                        "to": {"cite": "提交日期"},
                    }
                },
                "right": "30日",
                "ok": True,
            },
        )["ok"]
        is True,
    )
    check(
        "间隔算错被打回",
        evaluate_check(
            date_bundle,
            {
                "op": "le",
                "left": {
                    "date_diff": {
                        "from": {"cite": "费用发生日期"},
                        "to": {"cite": "提交日期"},
                    }
                },
                "right": 2,
                "ok": True,
            },
        )["ok"]
        is False,
    )
    check(
        "日期先后可比（不必转数值）",
        evaluate_check(
            date_bundle,
            {"op": "le", "left": {"cite": "费用发生日期"}, "right": {"cite": "提交日期"}, "ok": True},
        )["ok"]
        is True,
    )


def test_intent_compilation() -> None:
    print("3. 意图编译（条款 + 义务 + 建模疑点）")
    intents = compile_intents([rule_0016()], GRAPH_0016, [prelim_unverifiable()])
    check("一条比对边编译为一个意图", len(intents) == 1, str(len(intents)))
    intent = intents[0]
    check("意图带条款", "报销金额达到5000元及以上" in intent["clause"])
    check("义务类型为比对", intent["obligation"]["kind"] == "compare", str(intent["obligation"]["kind"]))
    check("初判随意图进入判定", intent["prelim"]["result"] == "unverifiable")
    warnings = " ".join(intent["warnings"])
    check("识破「数值算子比签字字段」", "疑似被错误建模" in warnings, warnings)
    check("识破「条款有条件但条件丢失」", "条件字段可能被丢弃" in warnings, warnings)
    check("识破「同时具备类要求被建模成比对」", "义务类型建模错误" in warnings, warnings)
    check("字段语义判定", field_semantics("部门负责人签字") == "signature" and field_semantics("报销金额") == "number")

    # 结构化 requirement：条款是"应同时具备"时也能审（图谱还没有对应边）
    rule = rule_0016()
    rule.structure = {
        "condition": {"text": "报销金额达到5000元及以上", "field": "报销金额", "operator": "不小于", "value": 5000},
        "requirement": {
            "items": [
                {"doc_type": "报销单", "field": "部门负责人签字", "must": "signed"},
                {"doc_type": "报销单", "field": "财务负责人签字", "must": "signed"},
            ]
        },
    }
    intents = compile_intents([rule], {"nodes": [], "edges": []}, [])
    check("requirement 规则编译为存在性义务", intents and intents[0]["obligation"]["kind"] == "exist", str(intents[0]["obligation"]["kind"]) if intents else "-")
    check("条件随意图进入", intents[0]["condition"]["value"] == 5000)

    # 无图谱、无结构化的定性规则 → free_text 意图（不会被漏掉）
    rule = rule_0016()
    rule.structure = None
    intents = compile_intents([rule], {"nodes": [], "edges": []}, [])
    check("未覆盖规则仍进入判定（free_text）", intents and intents[0]["obligation"]["kind"] == "free_text", str(intents[0]["obligation"]["kind"]) if intents else "-")


# ============ 4. 主流程：判定 → 验证 → 落地 ============

def test_verified_judgement_applies() -> None:
    print("4. 验证通过的判定：覆盖旧结论并留痕")
    fake = _FakeLLM(_verdict_pass_condition_not_met())
    original = judge.get_llm_client
    try:
        _patch_llm(fake)
        prelim = prelim_unverifiable()
        results = [prelim]
        report = judge.run_unified_judgement(
            None, None, [baoxiao_doc()], [rule_0016()], results, graph_data=GRAPH_0016
        )
        check("送审 1 项、验证通过 1 项", report.items_sent == 1 and report.verified == 1, str(report.as_dict()))
        check("结论覆盖为 pass", prelim.result == "pass", prelim.result)
        check("记来源 judge", prelim.source == "judge", str(prelim.source))
        check("问题描述写明规则不适用", "触发条件不成立" in (prelim.issue_desc or ""), str(prelim.issue_desc))
        detail = prelim.detail or {}
        check("证据链带引用", detail["judge"]["verified"] is True and detail["judge"]["citations"], str(detail["judge"]["citations"]))
        decision = detail.get("judge_decision") or {}
        check(
            "判定留痕（unverifiable → pass，kind=resolved）",
            detail.get("adjudicated_from") == "unverifiable"
            and decision.get("to") == "pass"
            and decision.get("kind") == "resolved",
            str(decision),
        )
        check("建模疑点写进证据", detail["judge"]["modeling_warnings"], str(detail["judge"]["modeling_warnings"]))
        check("覆盖计数", report.overrides == 1 and report.escalated == 0, str(report.as_dict()))
        prompt = fake.calls[0]["messages"][1]["content"]
        check("提示词含证据包与意图", "\"facts\"" in prompt and "modeling_warnings" in prompt)
        check("提示词含证据包字段事实", "\"key\": \"报销金额\"" in prompt)
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]


def test_gate_rejects_and_retries() -> None:
    print("5. 验证门：引用造假打回 → 带原因重试 → 通过")
    bad = {
        "results": [
            {
                "intent_id": INTENT_0016,
                "rule_id": "R0016",
                "verdict": "pass",
                "condition_met": False,
                "reason": "凭空引用一个不存在的签字",
                "confidence": 0.95,
                "checks": [{"op": "lt", "left": {"cite": "报销金额"}, "right": 5000, "ok": True}],
                "citations": [{"doc": BAOXIAO_FILE, "key": "财务负责人签字", "value": "王芳"}],
            }
        ]
    }
    fake = _FakeLLM([bad, _verdict_pass_condition_not_met()])
    original = judge.get_llm_client
    try:
        _patch_llm(fake)
        prelim = prelim_unverifiable()
        report = judge.run_unified_judgement(
            None, None, [baoxiao_doc()], [rule_0016()], [prelim], graph_data=GRAPH_0016
        )
        check("调用两次（一次判定 + 一次带原因重试）", len(fake.calls) == 2, str(len(fake.calls)))
        check("重试请求带失败原因", "verification_failed_last_time" in fake.calls[1]["messages"][1]["content"])
        check("重试后通过并落库", prelim.result == "pass" and report.verified == 1, f"{prelim.result}/{report.as_dict()}")
        check("重试计数与引用错误计数", report.retried == 1 and report.citation_errors == 1, str(report.as_dict()))
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]


def test_gate_escalates_after_retry() -> None:
    print("6. 验证门：重试仍不过 → 显性交人工（不静默停在无法核验）")
    bad = {
        "results": [
            {
                "intent_id": INTENT_0016,
                "rule_id": "R0016",
                "verdict": "fail",
                "condition_met": True,
                "reason": "低置信不通过",
                "confidence": 0.5,
                "citations": [{"doc": BAOXIAO_FILE, "key": "报销金额", "value": 4860.0}],
            }
        ]
    }
    fake = _FakeLLM(bad)
    original = judge.get_llm_client
    try:
        _patch_llm(fake)
        prelim = prelim_unverifiable()
        report = judge.run_unified_judgement(
            None, None, [baoxiao_doc()], [rule_0016()], [prelim], graph_data=GRAPH_0016
        )
        check("交人工计数", report.escalated == 1 and report.verified == 0, str(report.as_dict()))
        check("结论为无法核验", prelim.result == "unverifiable", prelim.result)
        check("显性标记需人工确认", (prelim.detail or {}).get("manual_review_required") is True)
        check("写明失败原因", "验证门" in (prelim.issue_desc or ""), str(prelim.issue_desc))
        check("失败原因入证据", (prelim.detail or {}).get("judge_errors"), str((prelim.detail or {}).get("judge_errors")))
        check(
            "被打回的原始判定留痕（供人工复核）",
            (prelim.detail or {}).get("judge_rejected", {}).get("verdict") == "fail",
            str((prelim.detail or {}).get("judge_rejected")),
        )
        check(
            "打回结果标记未通过验证",
            (prelim.detail or {}).get("judge", {}).get("verified") is False,
            str((prelim.detail or {}).get("judge")),
        )
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]


def test_llm_failure_escalates() -> None:
    print("7. 判定调用失败 → 保留结论并交人工")
    fake = _FakeLLM(LLMError("boom", "network"))
    original = judge.get_llm_client
    try:
        _patch_llm(fake)
        prelim = prelim_unverifiable()
        report = judge.run_unified_judgement(
            None, None, [baoxiao_doc()], [rule_0016()], [prelim], graph_data=GRAPH_0016
        )
        check("调用失败计数", report.llm_error == 1 and report.escalated == 1, str(report.as_dict()))
        check("标需人工确认", (prelim.detail or {}).get("manual_review_required") is True)
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]


def test_override_deterministic_fail() -> None:
    print("8. 覆盖确定性 fail：公司名详略不同（假阳性纠正）")
    doc_a = Document(
        doc_type="委托出口确认单",
        file_name="委托单.docx",
        extracted_fields={"委托方": "上海远航物流有限公司"},
        ocr_text="委托方：上海远航物流有限公司",
        ocr_confidence=0.95,
    )
    doc_b = Document(
        doc_type="运单",
        file_name="运单.docx",
        extracted_fields={"发货方": "上海远航物流"},
        ocr_text="发货方：上海远航物流",
        ocr_confidence=0.95,
    )
    rule = Rule(
        rule_set_id=None,
        rule_no=3,
        rule_text="委托出口确认单的委托方应与运单的发货方一致。",
        doc_type="委托出口确认单",
        check_category="信息准确性",
        enabled=True,
        priority=20,
        status="confirmed",
        structure=None,
        scope={"doc_types": ["委托出口确认单", "运单"]},
        intents=["信息准确性"],
    )
    graph = {
        "nodes": [
            {"name": "委托出口确认单.委托方", "type": "Field", "properties": {}},
            {"name": "运单.发货方", "type": "Field", "properties": {}},
        ],
        "edges": [
            {
                "source": "委托出口确认单.委托方",
                "target": "运单.发货方",
                "type": "COMPARE_TO",
                "properties": {"rule_id": "R0003", "operator": "等于"},
            }
        ],
    }
    prelim = ReviewResult(
        rule_text=rule.rule_text,
        check_category="信息准确性",
        doc_type="委托出口确认单",
        result="fail",
        status="open",
        status_history=[],
        source="graph",
        graph_source="委托出口确认单.委托方",
        graph_target="运单.发货方",
        issue_desc="字符串不一致",
        detail={
            "rule_id": "R0003",
            "operator": "等于",
            "src_vals": ["上海远航物流有限公司"],
            "tgt_vals": ["上海远航物流"],
        },
    )
    fake = _FakeLLM(
        {
            "results": [
                {
                    "intent_id": "R0003#委托出口确认单.委托方->运单.发货方",
                    "rule_id": "R0003",
                    "verdict": "pass",
                    "condition_met": None,
                    "reason": "「上海远航物流有限公司」与「上海远航物流」是同一主体的详略写法，按语义等价处理。",
                    "confidence": 0.9,
                    "citations": [
                        {"doc": "委托单.docx", "key": "委托方", "value": "上海远航物流有限公司"},
                        {"doc": "运单.docx", "key": "发货方", "value": "上海远航物流"},
                    ],
                    "checks": [
                        {
                            "op": "contains",
                            "left": {"cite": "委托方"},
                            "right": "上海远航物流",
                            "ok": True,
                        }
                    ],
                }
            ]
        }
    )
    original = judge.get_llm_client
    try:
        _patch_llm(fake)
        report = judge.run_unified_judgement(
            None, None, [doc_a, doc_b], [rule], [prelim], graph_data=graph
        )
        check("覆盖为 pass", prelim.result == "pass" and report.overrides == 1, f"{prelim.result}/{report.as_dict()}")
        check(
            "覆盖留痕（from=fail）",
            (prelim.detail or {}).get("judge_override", {}).get("from") == "fail",
            str((prelim.detail or {}).get("judge_override")),
        )
        check("双引用入证据", len((prelim.detail or {}).get("judge", {}).get("citations", [])) == 2)
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]


def test_uncovered_rule_creates_result() -> None:
    print("9. 未覆盖规则：判定后新建结果（不再有漏判）")
    rule = Rule(
        rule_set_id=None,
        rule_no=17,
        rule_text="增值税发票应加盖销售方发票专用章；电子发票除外。",
        doc_type="增值税发票",
        check_category="基础判断",
        enabled=True,
        priority=10,
        status="confirmed",
        structure=None,
        scope={"doc_types": ["增值税发票"]},
        intents=["基础判断"],
    )
    fake = _FakeLLM(
        {
            "results": [
                {
                    "intent_id": "R0017#free",
                    "rule_id": "R0017",
                    "verdict": "pass",
                    "condition_met": None,
                    "reason": "两张增值税发票均识别到印章（has_stamp=true）。",
                    "confidence": 0.9,
                    "citations": [
                        {"doc": "发票1.docx", "key": "价税合计", "value": 3200.0}
                    ],
                }
            ]
        }
    )
    original = judge.get_llm_client
    try:
        _patch_llm(fake)
        results: list[ReviewResult] = []
        report = judge.run_unified_judgement(
            None, None, [invoice_doc("发票1.docx", 3200.0)], [rule], results, graph_data=None
        )
        check("新建结果 1 条", len(results) == 1 and report.created == 1, f"{len(results)}/{report.as_dict()}")
        check("新结果来源 judge", results[0].source == "judge", str(results[0].source))
        check("新结果结论", results[0].result == "pass", results[0].result)
        check("新结果绑定规则", (results[0].detail or {}).get("rule_id") == "R0017")
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]


def main() -> None:
    test_evidence_bundle()
    test_operand_and_checks()
    test_gate_self_contradiction()
    test_intent_compilation()
    test_verified_judgement_applies()
    test_gate_rejects_and_retries()
    test_gate_escalates_after_retry()
    test_llm_failure_escalates()
    test_override_deterministic_fail()
    test_uncovered_rule_creates_result()
    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
