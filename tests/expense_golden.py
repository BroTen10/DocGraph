# -*- coding: utf-8 -*-
"""费用报销黄金集回归门禁（判定架构 P4）。

把 `samples/费用报销测试套件/README.md` 的预期结果固化成可执行断言：
- 01-合规 BX-2026-0312：10 条规则**全部通过**；
- 02-问题 BX-2026-0318：命中 5 条不通过（金额不一致 / 开票日期晚于提交 / 间隔 46 天 /
  缺行程单 / 仅部门签字缺财务签字），其余通过。

两层：
1. **确定性层（默认执行，零 LLM）**：断言规则意图编译正确——条件、存在性要求、
   30 日间隔容差都进了意图，且"金额与签字比大小"这类错误建模会被体检提示命中；
2. **实测层（--live，调用真实大模型）**：跑统一判定，断言每条的最终结论与黄金集一致，
   并要求**引用验证率 100%、无交人工**——换模型/改提示词后必须过这一关。

运行：
    backend/.venv/bin/python tests/expense_golden.py            # 确定性层
    backend/.venv/bin/python tests/expense_golden.py --live     # 真实模型端到端
"""

from __future__ import annotations

import os
import sys
from typing import Any

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
# 以 backend 为工作目录：应用配置（含 LLM key）从 backend/.env 读取
os.chdir(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.models import Document, ReviewResult, Rule  # noqa: E402
from app.services.review_intent import compile_intents  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


# ============ 规则集（10 条，含正确的结构契约） ============
# 说明：规则正文取自 samples/费用报销测试套件/费用报销规则描述文档.xlsx，
# structure 按"判定架构 P3 契约"给出正确建模（条件 + 义务；存在性要求不进 assertion）。

def _rule(no: int, category: str, text: str, doc_type: str, structure: dict | None = None,
          tolerance: dict | None = None) -> Rule:
    return Rule(
        rule_set_id=None,
        rule_no=no,
        rule_text=text,
        doc_type=doc_type,
        check_category=category,
        enabled=True,
        priority=no * 10,
        status="confirmed",
        structure=structure,
        tolerance=tolerance or {"time_days": 0, "weight_kg": 0, "amount_percent": 0},
        scope={"doc_types": [doc_type] if doc_type else "ALL"},
        intents=[category],
    )


EXPENSE_RULES: list[Rule] = [
    _rule(
        1, "信息准确性",
        "报销单的报销金额应等于所附全部增值税发票价税合计之和。", "报销单",
        {"assertion": {
            "source": {"doc_type": "增值税发票", "field": "价税合计", "aggregate": "SUM"},
            "target": {"doc_type": "报销单", "field": "报销金额", "aggregate": "SUM"},
            "operator": "总额等于"}},
    ),
    _rule(
        2, "信息准确性",
        "增值税发票的购货方应与报销单的所属公司名称一致。", "增值税发票",
        {"assertion": {
            "source": {"doc_type": "增值税发票", "field": "购货方"},
            "target": {"doc_type": "报销单", "field": "所属公司名称"},
            "operator": "等于"}},
    ),
    _rule(
        3, "时间逻辑",
        "增值税发票的开票日期应不晚于报销单的提交日期。", "增值税发票",
        {"assertion": {
            "source": {"doc_type": "增值税发票", "field": "开票日期"},
            "target": {"doc_type": "报销单", "field": "提交日期"},
            "operator": "时间不晚于"}},
    ),
    _rule(
        4, "时间逻辑",
        "报销单的提交日期与费用发生日期的间隔不得超过30日；跨年度报销另行审批。", "报销单",
        {"assertion": {
            "source": {"doc_type": "报销单", "field": "提交日期"},
            "target": {"doc_type": "报销单", "field": "费用发生日期"},
            "operator": "时间不晚于"},
         "condition": None},
        {"time_days": 30, "weight_kg": 0, "amount_percent": 0, "allow_same_day": True},
    ),
    _rule(5, "齐套性", "费用报销应齐套：报销单、增值税发票、行程单三者齐全。", None),
    _rule(
        6, "信息准确性",
        "报销金额达到5000元及以上的报销单，应同时具备部门负责人与财务负责人签字审批。", "报销单",
        {"condition": {"text": "报销金额达到5000元及以上", "field": "报销金额",
                       "operator": "不小于", "value": 5000},
         "requirement": {"items": [
             {"doc_type": "报销单", "field": "部门负责人签字", "must": "signed"},
             {"doc_type": "报销单", "field": "财务负责人签字", "must": "signed"}]}},
    ),
    _rule(7, "基础判断", "增值税发票应加盖销售方发票专用章。", "增值税发票"),
    _rule(
        8, "信息准确性",
        "报销单的申请人应与费用发生人一致。", "报销单",
        {"assertion": {
            "source": {"doc_type": "报销单", "field": "申请人"},
            "target": {"doc_type": "报销单", "field": "费用发生人"},
            "operator": "等于"}},
    ),
    _rule(
        9, "信息准确性",
        "行程单的乘机人应与报销单的申请人一致。", "行程单",
        {"assertion": {
            "source": {"doc_type": "行程单", "field": "乘机人"},
            "target": {"doc_type": "报销单", "field": "申请人"},
            "operator": "等于"}},
    ),
    _rule(
        10, "信息准确性",
        "增值税发票的价税合计应等于该发票的金额与税额之和。", "增值税发票",
        {"assertion": {
            "source": {"doc_type": "增值税发票", "field": "价税合计", "aggregate": "SUM"},
            "target": {"doc_type": "增值税发票", "field": "金额+税额", "aggregate": "SUM"},
            "operator": "总额等于"}},
    ),
]


# ============ 两个案件（单据字段取自测试套件 README 的关键数值） ============

def _doc(doc_type: str, file_name: str, fields: dict, has_stamp: bool | None = None) -> Document:
    text = "\n".join(f"{k}：{v}" for k, v in fields.items())
    return Document(doc_type=doc_type, file_name=file_name, extracted_fields=fields,
                    ocr_text=text, ocr_confidence=0.98, has_stamp=has_stamp)


CASE_01 = [
    _doc("报销单", "BX-2026-0312_费用报销单.docx", {
        "报销单号": "BX-2026-0312", "申请人": "张伟", "费用发生人": "张伟",
        "所属公司名称": "深圳市远航进出口有限公司", "费用发生日期": "2026-03-05",
        "提交日期": "2026-03-09", "报销金额": 4860.0, "币别": "人民币",
        "审批签字": "部门负责人 李强"}),
    _doc("增值税发票", "BX-2026-0312_增值税发票_1_航空客票.docx", {
        "价税合计": 3200.0, "金额": 3036.7, "税额": 163.3, "开票日期": "2026-03-06",
        "购货方": "深圳市远航进出口有限公司"}, has_stamp=True),
    _doc("增值税发票", "BX-2026-0312_增值税发票_2_住宿费.docx", {
        "价税合计": 1660.0, "金额": 1566.04, "税额": 93.96, "开票日期": "2026-03-08",
        "购货方": "深圳市远航进出口有限公司"}, has_stamp=True),
    _doc("行程单", "BX-2026-0312_行程单.docx", {"乘机人": "张伟", "合计金额": 3200.0}),
]

CASE_02 = [
    _doc("报销单", "BX-2026-0318_费用报销单.docx", {
        "报销单号": "BX-2026-0318", "申请人": "张伟", "费用发生人": "张伟",
        "所属公司名称": "深圳市远航进出口有限公司", "费用发生日期": "2026-03-05",
        "提交日期": "2026-04-20", "报销金额": 5860.0, "币别": "人民币",
        "审批签字": "部门负责人 李强；财务负责人 未签字"}),
    _doc("增值税发票", "BX-2026-0318_增值税发票_1_住宿费.docx", {
        "价税合计": 5000.0, "金额": 4716.98, "税额": 283.02, "开票日期": "2026-04-25",
        "购货方": "深圳市远航进出口有限公司"}, has_stamp=False),
]

# 黄金集：{案件: {规则序号: 期望结论}}；缺失规则按"该案件不产出结果"处理（如无行程单）
GOLDEN: dict[str, dict[int, str | None]] = {
    "01": {1: "pass", 2: "pass", 3: "pass", 4: "pass", 5: "pass",
           6: "pass", 7: "pass", 8: "pass", 9: "pass", 10: "pass"},
    # 02 缺行程单：R0009（乘机人=申请人）无单据可核 → unverifiable，
    # "缺单据"本身由 R0005 齐套性规则报出，两条不重复报同一个问题
    "02": {1: "fail", 2: "pass", 3: "fail", 4: "fail", 5: "fail",
           6: "fail", 7: "fail", 8: "pass", 9: "unverifiable", 10: "pass"},
}


# ============ 1. 确定性层：意图编译 ============

def test_intents() -> None:
    print("1. 意图编译（零 LLM）")
    intents = compile_intents(EXPENSE_RULES, {"nodes": [], "edges": []}, [])
    by_rule: dict[str, list[dict]] = {}
    for intent in intents:
        by_rule.setdefault(intent["rule_id"], []).append(intent)

    check("10 条规则全部编译出意图", len(by_rule) == 10, str(sorted(by_rule)))
    r6 = by_rule["R0006"][0]
    check("R0006 义务类型=存在性要求", r6["obligation"]["kind"] == "exist", r6["obligation"]["kind"])
    check("R0006 携带触发条件（5000 元门槛）",
          r6["condition"] and r6["condition"].get("value") == 5000, str(r6["condition"]))
    check("R0006 两个签字要求都在意图里",
          len(r6["obligation"]["items"]) == 2, str(r6["obligation"]["items"]))
    r4 = by_rule["R0004"][0]
    check("R0004 条件义务为日期比对",
          r4["obligation"]["kind"] == "compare", r4["obligation"]["kind"])
    r5 = by_rule["R0005"][0]
    check("R0005 齐套性规则为完整性义务",
          r5["obligation"]["kind"] in ("complete", "free_text"), r5["obligation"]["kind"])

    # 错误建模必须被体检命中（回归：曾把"双签"建成"金额 不小于 签字"）
    bad_rule = _rule(
        99, "信息准确性",
        "报销金额达到5000元及以上的报销单，应同时具备部门负责人与财务负责人签字审批。", "报销单",
        {"assertion": {
            "source": {"doc_type": "报销单", "field": "报销金额"},
            "target": {"doc_type": "报销单", "field": "部门负责人签字"},
            "operator": "不小于"}},
    )
    graph = {"nodes": [], "edges": [{
        "source": "报销单.报销金额", "target": "报销单.部门负责人签字", "type": "COMPARE_TO",
        "properties": {"rule_id": "R0099", "operator": "不小于"}}]}
    bad_intent = compile_intents([bad_rule], graph, [])[0]
    warnings = " ".join(bad_intent["warnings"])
    check("错误建模被体检命中", "疑似被错误建模" in warnings and "条件字段可能被丢弃" in warnings, warnings)


# ============ 2. 实测层：真实模型端到端 ============

def _prelim_for(intent: dict, docs: list[Document], rule: Rule) -> ReviewResult:
    """用确定性引擎在该意图上跑一遍，得到初判（与线上链路一致）。"""
    from app.services import graph_review_service as engine

    obligation = intent["obligation"]
    rel_props = {
        "rule_id": intent["rule_id"],
        "rule_text": rule.rule_text,
        "doc_type": rule.doc_type,
        "check_category": rule.check_category,
    }
    nodes = intent.get("graph_nodes") or {}
    if obligation["kind"] == "compare":
        result, issue, detail = engine._dispatch_compare(
            obligation["operator"],
            obligation["source"]["doc_type"], obligation["source"]["field"],
            obligation["target"]["doc_type"], obligation["target"]["field"],
            docs,
            tolerance=0,
            tolerance_params=obligation.get("tolerance_params") or {},
            aggregate=obligation.get("source", {}).get("aggregate"),
        )
    elif obligation["kind"] == "exist":
        rel_props["condition"] = intent.get("condition")
        results = engine._check_exist_requirements(
            [{"source": "存在性检查",
              "target": f"{obligation['items'][0]['doc_type']}.{obligation['items'][0]['field']}",
              "rel_props": {**rel_props, "must": obligation["items"][0].get("must", "present"),
                            "condition": {**(intent.get("condition") or {}),
                                          "doc_type": rule.doc_type}}}],
            docs,
        )
        return results[0] if results else None  # type: ignore[return-value]
    else:
        return None  # type: ignore[return-value]
    from app.services.graph_review_service import _make_result_from_graph

    return _make_result_from_graph(
        rel_props, docs[0], result, issue_desc=issue, detail=detail,
        source_node=nodes.get("source"), target_node=nodes.get("target"),
    )


def test_live(cases: dict[str, list[Document]], graph_data: dict | None) -> None:
    print("2. 真实模型端到端（黄金集对照）")
    import app.services.review_judge as judge

    for case_name, docs in cases.items():
        golden = GOLDEN[case_name]
        results: list[ReviewResult] = []
        intents = compile_intents(EXPENSE_RULES, graph_data, [])
        rule_by_id = {f"R{r.rule_no:04d}": r for r in EXPENSE_RULES}
        for intent in intents:
            rule = rule_by_id[intent["rule_id"]]
            if intent["obligation"]["kind"] == "exist":
                for item in intent["obligation"]["items"]:
                    child = {**intent, "obligation": {**intent["obligation"], "items": [item]}}
                    prelim = _prelim_for(child, docs, rule)
                    if prelim is not None:
                        results.append(prelim)
            else:
                prelim = _prelim_for(intent, docs, rule)
                if prelim is not None:
                    results.append(prelim)

        report = judge.run_unified_judgement(
            None, None, docs, EXPENSE_RULES, results, graph_data=graph_data
        )
        metrics = report.as_dict()
        print(f"  --- 案件 {case_name}: {len(results)} 条，判定 {metrics['verified']} 条，"
              f"交人工 {metrics['escalated']}，覆盖 {metrics['overrides']}")

        by_rule: dict[str, list[ReviewResult]] = {}
        for r in results:
            rid = judge.result_rule_id(r)
            by_rule.setdefault(rid, []).append(r)

        for rule_no, expected in golden.items():
            rid = f"R{rule_no:04d}"
            items = by_rule.get(rid, [])
            if expected is None:
                check(f"[{case_name}] R{rule_no:04d} 不产出结果（缺单据/不适用）",
                      not items, str([r.result for r in items]))
                continue
            if not items:
                check(f"[{case_name}] R{rule_no:04d} 应产出结果", False, "无结果")
                continue
            if rule_no == 6:
                # 双签规则：两条要求都通过才算通过，否则命中不通过
                verdicts = [r.result for r in items]
                actual = "fail" if "fail" in verdicts else (
                    "pass" if all(v == "pass" for v in verdicts) else "unverifiable")
            else:
                actual = items[0].result
            check(f"[{case_name}] R{rule_no:04d} 期望 {expected}，实际 {actual}",
                  actual == expected,
                  " | ".join((r.issue_desc or "")[:70] for r in items))

        # 硬门禁：全部条目要么通过机器验证、要么被显式跳过；不允许交人工
        check(f"[{case_name}] 引用验证率 100%（含跳过）",
              metrics["verified"] + metrics["items_skipped"] == metrics["items_sent"],
              str(metrics))
        check(f"[{case_name}] 无交人工", metrics["escalated"] == 0, str(metrics))
        if "--verbose" in sys.argv:
            for r in results:
                rejected = (r.detail or {}).get("judge_rejected")
                if rejected:
                    print(f"    [打回留痕] {judge.result_rule_id(r)} "
                          f"verdict={rejected.get('verdict')} "
                          f"confidence={rejected.get('confidence')} "
                          f"errors={(r.detail or {}).get('judge_errors')}\n"
                          f"      checks={rejected.get('checks')}")
        if metrics["citation_errors"] or metrics["recompute_errors"]:
            print(f"    （观测：验证门打回 引用 {metrics['citation_errors']} / 重算 "
                  f"{metrics['recompute_errors']} 次，重试后已收敛）")


def main() -> None:
    test_intents()
    if "--live" in sys.argv:
        test_live({"01": CASE_01, "02": CASE_02}, None)
    else:
        print("（跳过真实模型层：加 --live 运行）")
    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
