# -*- coding: utf-8 -*-
"""自动测试流程：验证图谱规则是否正确被调用对文档进行检查。

流程（零 LLM、零 OCR，全程确定性）：
  1. 创建临时规则集，灌入最简测试集的结构化规则（tests/minimal_test_set.py）
  2. 构建规则图谱（程序化转换：REQUIRED / MUST_STAMP / COMPARE_TO）
  3. 校验图谱内容：三类关系是否按规则正确写入
  4. 对每个测试场景构造合成文档（extracted_fields 预设），执行图谱审查并断言
     - 结果值（pass / fail / unverifiable）与期望一致
     - 结果来源全部为 source="graph"（证明走了图谱规则而非旧逻辑 fallback）
     - rule_text 来自图谱、COMPARE_TO 结果携带图谱节点名
  5. 完整链路集成：review_service.start_review(snapshot) → 结果全部来自图谱
  6. 边界场景：无图谱快照时审查入口抛 ValueError（不静默 fallback）
  7. 清理：删除规则集（PG 级联）+ 清除 Neo4j 图谱

运行：backend/.venv/Scripts/python.exe tests/run_graph_rule_tests.py
前置：Postgres / Neo4j 已启动（backend/.env 配置），无需启动后端服务。
"""

from __future__ import annotations

import os
import sys
import time
import traceback

# ---------- 环境准备：以 backend 为工作目录（读取 .env），并把 tests 加入 import 路径 ----------
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
os.chdir(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)
sys.path.insert(0, _TEST_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal  # noqa: E402
from app.models import Contract, Document, ReviewResult, ReviewTask, Rule, RuleSet  # noqa: E402
from app.neo4j_client import get_neo4j_client  # noqa: E402
from app.services.graph_builder_service import build_graph  # noqa: E402
from app.services.graph_review_service import run_graph_review_with_contract  # noqa: E402

from minimal_test_set import (  # noqa: E402
    EXPECTED_GRAPH_NODES,
    NEGATIVE_SCENARIO,
    PIPELINE_SCENARIO,
    RULES,
    SCENARIOS,
)


# ============ 测试基架 ============

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    """记录一条断言结果。"""
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"


def create_rule_set(db, name: str) -> RuleSet:
    rs = RuleSet(
        name=name,
        description="图谱规则自动测试",
        doc_types=[],
        check_categories=[],
        is_default=False,
    )
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return rs


def insert_rules(db, rule_set_id) -> None:
    """灌入测试规则（直接写库，跳过 LLM 解析；结构字段保证图谱确定性转换）。"""
    for spec in RULES:
        rule = Rule(
            rule_set_id=rule_set_id,
            doc_type=spec["doc_type"],
            check_category=spec["check_category"],
            rule_text=spec["rule_text"],
            tolerance=spec.get("tolerance") or {},
            priority=spec.get("priority", 100),
            enabled=True,
            status="confirmed",
            confirmed_by="auto-test",
            structure=spec.get("structure"),
            intents=[spec["check_category"]],
        )
        db.add(rule)
    db.commit()


def create_contract_with_docs(db, rule_set_id, scenario, contract_no: str):
    """创建合成合同 + 文档（ocr_status=done + extracted_fields 预设，跳过 OCR）。"""
    contract = Contract(
        rule_set_id=rule_set_id,
        contract_no=contract_no,
        alias_list=[],
        status="uploaded",
        note=f"自动化测试 {scenario['id']}",
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)

    docs: list[Document] = []
    for i, spec in enumerate(scenario["docs"]):
        d = Document(
            contract_id=contract.id,
            file_name=f"{scenario['id']}_{i}_{spec['doc_type']}.pdf",
            file_path=f"synthetic://{scenario['id']}/{i}",
            file_type="pdf",
            doc_type=spec["doc_type"],
            is_required=False,
            ocr_status="done",
            ocr_confidence=1.0,
            extracted_fields=spec["fields"],
            has_stamp=spec["has_stamp"],
        )
        db.add(d)
        docs.append(d)
    db.commit()
    for d in docs:
        db.refresh(d)
    return contract, docs


# ============ 断言：图谱规则审查结果 ============

def assert_scenario_results(scenario, results) -> list[str]:
    """对单个场景执行全套断言，返回失败信息列表。"""
    failures: list[str] = []
    expected = scenario["expected"]
    by_cat: dict[str, list[ReviewResult]] = {}
    for r in results:
        by_cat.setdefault(r.check_category, []).append(r)

    if len(results) != len(expected):
        failures.append(
            f"结果条数 {len(results)} != 期望 {len(expected)} "
            f"(实际类别: {sorted(by_cat)})"
        )

    rule_text_by_cat = {spec["check_category"]: spec["rule_text"] for spec in RULES}
    for cat, exp in expected.items():
        items = by_cat.get(cat, [])
        if len(items) != 1:
            failures.append(f"[{cat}] 图谱规则结果条数 {len(items)} != 1")
            continue
        r = items[0]
        if r.result != exp:
            failures.append(f"[{cat}] result={r.result} != 期望 {exp}（{r.issue_desc}）")
        if r.source != "graph":
            failures.append(f"[{cat}] source={r.source} != graph：图谱规则未被调用")
        if r.rule_text != rule_text_by_cat.get(cat):
            failures.append(f"[{cat}] rule_text 非来自图谱: {r.rule_text!r}")
        if r.rule_id is not None:
            failures.append(f"[{cat}] 图谱结果不应绑定 rule_id（当前 {r.rule_id}）")
        if cat in EXPECTED_GRAPH_NODES:
            src, tgt = EXPECTED_GRAPH_NODES[cat]
            if r.graph_source != src or r.graph_target != tgt:
                failures.append(
                    f"[{cat}] 图谱节点 {r.graph_source}->{r.graph_target} != 期望 {src}->{tgt}"
                )
        if r.result == "pass" and r.status != "closed":
            failures.append(f"[{cat}] pass 结果状态应为 closed，实际 {r.status}")
        if r.result != "pass" and r.status != "open":
            failures.append(f"[{cat}] {r.result} 结果状态应为 open，实际 {r.status}")

    # 全局：不允许任何非图谱来源结果混入（证明未 fallback 旧逻辑/LLM）
    non_graph = [(r.check_category, r.source) for r in results if r.source != "graph"]
    if non_graph:
        failures.append(f"存在非图谱来源结果: {non_graph}")
    return failures


def run_scenario_checks(db, rule_set_id, snapshot_id, scenario) -> None:
    """执行单个场景的图谱审查并断言。"""
    print(f"\n=== {scenario['id']} {scenario['name']} ===")
    contract, docs = create_contract_with_docs(
        db, rule_set_id, scenario, contract_no=f"TEST-{scenario['id']}"
    )
    results = run_graph_review_with_contract(db, contract, docs, snapshot_id=snapshot_id)
    failures = assert_scenario_results(scenario, results)
    if failures:
        for f in failures:
            check(f"[{scenario['id']}] {f}", False)
    else:
        check(f"{scenario['id']} 全部断言通过", True, f"（{len(results)} 条图谱结果）")
    return contract, docs


# ============ 完整审查链路集成（TC-07） ============

def run_pipeline_integration(db, rule_set_id, snapshot_id, scenario) -> None:
    """走 review_service.start_review（含 snapshot）验证链路确实调用图谱规则。"""
    print(f"\n=== {scenario['id']} {scenario['name']} ===")
    from app.services import review_service
    import app.services.llm_review_service as llm_mod

    contract, docs = create_contract_with_docs(
        db, rule_set_id, scenario, contract_no=f"TEST-{scenario['id']}"
    )

    # 测试规则全部有 structure.assertion，LLM 语义审查本身即为空；
    # 此处再显式屏蔽 LLM/建议依赖，保证测试确定性。
    orig_unstructured = llm_mod.review_unstructured_rules
    orig_semantic = llm_mod.semantic_equivalence_fallback
    orig_suggest = review_service.build_suggestion_llm
    llm_mod.review_unstructured_rules = lambda *a, **k: []
    llm_mod.semantic_equivalence_fallback = lambda *a, **k: 0
    review_service.build_suggestion_llm = lambda *a, **k: "（自动化测试生成建议）"
    try:
        task = review_service.start_review(db, contract.id, snapshot_id=snapshot_id)
        deadline = time.time() + 120
        final = None
        while time.time() < deadline:
            time.sleep(0.5)
            tdb = SessionLocal()
            try:
                t = tdb.get(ReviewTask, task.id)
                if t is not None and t.status in ("completed", "failed"):
                    final = t
                    break
            finally:
                tdb.close()

        if final is None:
            check(f"{scenario['id']} 审查任务在 120s 内未完成", False)
            return
        if final.status != "completed":
            check(f"{scenario['id']} 审查任务状态={final.status} error={final.error}", False)
            return

        tdb = SessionLocal()
        try:
            from sqlalchemy import select
            rows = list(
                tdb.execute(
                    select(ReviewResult).where(ReviewResult.task_id == task.id)
                ).scalars().all()
            )
        finally:
            tdb.close()

        failures = assert_scenario_results(scenario, rows)
        check(f"{scenario['id']} 完整链路图谱结果断言", not failures, f"failures={failures}")
        if failures:
            for f in failures:
                print(f"      ! {f}")
    finally:
        llm_mod.review_unstructured_rules = orig_unstructured
        llm_mod.semantic_equivalence_fallback = orig_semantic
        review_service.build_suggestion_llm = orig_suggest


# ============ 无图谱边界（TC-08） ============

def run_negative_check(db) -> None:
    print(f"\n=== {NEGATIVE_SCENARIO['id']} {NEGATIVE_SCENARIO['name']} ===")
    rs = create_rule_set(db, _unique_name("测试-无图谱边界"))
    try:
        scenario = {"id": "TC-08", "name": "neg", "docs": SCENARIOS[0]["docs"]}
        contract, docs = create_contract_with_docs(
            db, rs.id, scenario, contract_no="TEST-TC-08"
        )
        try:
            run_graph_review_with_contract(db, contract, docs)
            check("TC-08 无快照时抛 ValueError", False, "未抛出异常，静默执行了审查")
        except ValueError:
            check("TC-08 无快照时抛 ValueError", True)
    finally:
        rs_obj = db.get(RuleSet, rs.id)
        if rs_obj:
            db.delete(rs_obj)
            db.commit()


# ============ 清理 ============

def cleanup(db, rule_set_id, graph_id) -> None:
    """删除测试规则集（PG 级联清理规则/快照/合同/任务/结果）+ 清除 Neo4j 图谱。"""
    if graph_id:
        try:
            removed = get_neo4j_client().clear_graph(graph_id)
            print(f"  已清理 Neo4j 测试图谱 {graph_id}（剩余节点 {removed}）")
        except Exception:
            print("  Neo4j 测试图谱清理失败（不影响 PG 清理）")
    rs = db.get(RuleSet, rule_set_id)
    if rs:
        db.delete(rs)
        db.commit()
        print(f"  已删除测试规则集 {rule_set_id}（PG 级联清理）")


# ============ 主流程 ============

def main() -> int:
    global _PASS, _FAIL
    db = SessionLocal()
    rule_set_id = None
    graph_id = None
    try:
        print("=" * 72)
        print("图谱规则自动测试：最简测试集")
        print("=" * 72)

        # 1. 规则集 + 规则
        rs = create_rule_set(db, _unique_name("测试-图谱规则"))
        rule_set_id = rs.id
        insert_rules(db, rule_set_id)
        check("创建测试规则集并灌入 4 条结构化规则", True)

        # 2. 构建图谱（程序化转换，不调 LLM）
        print("\n--- 构建规则图谱 ---")
        resp = build_graph(db, rule_set_id, auto_confirm_all=True, operator="auto-test")
        graph_id = resp.graph_id
        snapshot_id = resp.snapshot_id
        print(f"  graph_id={graph_id} snapshot_id={snapshot_id}")
        print(f"  节点 {resp.node_count} / 关系 {resp.edge_count} / 规则 {resp.rule_count}")
        check("图谱构建完成（快照已保存）", bool(snapshot_id) and bool(graph_id))

        # 3. 校验图谱内容：三类关系正确写入
        print("\n--- 校验图谱内容（三类关系） ---")
        neo4j = get_neo4j_client()
        required = neo4j.get_required_docs(graph_id)
        stamp = neo4j.get_stamp_requirements(graph_id)
        compares = neo4j.get_compare_relationships(graph_id)
        check(
            "REQUIRED 关系包含 出口报关单",
            any(rec.get("target") == "出口报关单" for rec in required),
            f"targets={[r.get('target') for r in required]}",
        )
        check(
            "MUST_STAMP 关系包含 代理协议",
            any(rec.get("source") == "代理协议" for rec in stamp),
            f"sources={[r.get('source') for r in stamp]}",
        )
        compare_ops = [rec.get("rel_props", {}).get("operator") for rec in compares]
        check(
            "COMPARE_TO 关系包含 总额等于 / 时间不晚于",
            "总额等于" in compare_ops and "时间不晚于" in compare_ops,
            f"operators={compare_ops}",
        )
        check("图谱无重复 REQUIRED/MUST_STAMP 关系", len(required) == 1 and len(stamp) == 1)

        # 4. 逐场景执行图谱审查
        print("\n--- 图谱规则审查场景 ---")
        for scenario in SCENARIOS:
            run_scenario_checks(db, rule_set_id, snapshot_id, scenario)

        # 5. 完整链路集成
        run_pipeline_integration(db, rule_set_id, snapshot_id, PIPELINE_SCENARIO)

        # 6. 无图谱边界
        run_negative_check(db)

    except Exception as e:
        _FAIL += 1
        check("测试执行未出现异常", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        print("\n--- 清理 ---")
        if rule_set_id:
            cleanup(db, rule_set_id, graph_id)
        db.close()

    print("\n" + "=" * 72)
    print(f"测试结果：PASS={_PASS}  FAIL={_FAIL}")
    print("=" * 72)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
