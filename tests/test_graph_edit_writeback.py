# -*- coding: utf-8 -*-
"""图谱人工编辑 → 规则数据写回（规则生命周期闭环，图谱侧）。

背景：图谱是规则的**派生视图**（graph_builder 从 Rule 生成），此前人工在图谱页
的编辑只写 Neo4j，下一次重建就被覆盖——人工修正等于白做。本测试验证：

1. 边上的规则属性（容差/算子/条件/文本）写回 PG 规则数据；
2. 算子等结构字段写进 structure 的正确嵌套位置；
3. 写回同时记入人工修正账本（scope=graph_edit），可被提示词回灌；
4. 派生属性（tolerance 标量等）不写回，避免覆盖完整容差表；
5. 不传 db 时行为与旧版一致（只改图谱）。

运行：需 Postgres（TEST_DATABASE_URL 指向 *_test 库）：pytest tests/test_graph_edit_writeback.py
"""

from __future__ import annotations

import os
import sys
import uuid

from sqlalchemy import select

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from app.models import LlmCorrection, Rule, RuleSet  # noqa: E402
from app.schemas.graph import GraphEditOp  # noqa: E402
from app.services import graph_builder_service  # noqa: E402

SRC = "增值税发票.价税合计"
TGT = "报销单.报销金额"


class _StubNeo4j:
    """只实现写回路径用到的方法：读图（带 rule_id 的边）+ 更新属性。"""

    def __init__(self, rule_code: str = "R0001"):
        self.rule_code = rule_code
        self.updates: list[tuple] = []

    def get_graph_data(self, graph_id, tenant_id):
        return {
            "graph_id": graph_id,
            "nodes": [],
            "edges": [
                {
                    "id": "e1",
                    "source": SRC,
                    "target": TGT,
                    "type": "COMPARE_TO",
                    "properties": {"rule_id": self.rule_code, "layer": "execution"},
                }
            ],
            "node_count": 0,
            "edge_count": 1,
        }

    def update_edge_properties(self, graph_id, tenant_id, source, target, properties):
        self.updates.append((source, target, dict(properties)))

    def update_node_properties(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("本用例不应触发节点更新")

    def delete_node(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("本用例不应触发节点删除")

    def delete_edge(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("本用例不应触发边删除")


def _make_rule(db, tenant_id) -> Rule:
    rule_set = RuleSet(tenant_id=tenant_id, name=f"图谱写回-{uuid.uuid4().hex[:6]}")
    db.add(rule_set)
    db.flush()
    rule = Rule(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        rule_no=1,
        rule_text="发票价税合计应等于报销单报销金额。",
        doc_type="增值税发票",
        check_category="信息准确性",
        enabled=True,
        priority=100,
        tolerance={"amount_percent": 0},
        structure={
            "condition": None,
            "assertion": {
                "operator": "总额等于",
                "source": {"doc_type": "增值税发票", "field": "价税合计"},
                "target": {"doc_type": "报销单", "field": "报销金额"},
            },
            "exceptions": [],
        },
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def _edit(**properties) -> GraphEditOp:
    return GraphEditOp(
        op="update_edge", source=SRC, target=TGT, properties=properties
    )


def test_edge_edit_writes_back_to_rule_and_ledger(db, default_tenant_id) -> None:
    rule = _make_rule(db, default_tenant_id)
    stub = _StubNeo4j()

    graph_builder_service.apply_graph_edits(
        stub,
        default_tenant_id,
        "g1",
        [_edit(tolerance_params={"amount_percent": 5}, rule_text="发票价税合计与报销金额应一致（容差 5%）。")],
        db=db,
        rule_set_id=rule.rule_set_id,
    )

    db.refresh(rule)
    assert rule.tolerance == {"amount_percent": 5}
    assert "容差 5%" in rule.rule_text
    # 图谱侧照旧生效
    assert stub.updates and stub.updates[0][2]["tolerance_params"] == {"amount_percent": 5}

    rows = db.execute(
        select(LlmCorrection).where(LlmCorrection.scope == "graph_edit")
    ).scalars().all()
    fields = {row.field for row in rows}
    assert fields == {"tolerance", "rule_text"}
    assert all(row.target_id == rule.id for row in rows)


def test_operator_edit_lands_in_structure(db, default_tenant_id) -> None:
    rule = _make_rule(db, default_tenant_id)

    graph_builder_service.apply_graph_edits(
        _StubNeo4j(),
        default_tenant_id,
        "g1",
        [_edit(operator="不小于")],
        db=db,
        rule_set_id=rule.rule_set_id,
    )

    db.refresh(rule)
    assert rule.structure["assertion"]["operator"] == "不小于"
    # 原有断言两侧字段不能被写回丢掉
    assert rule.structure["assertion"]["source"]["field"] == "价税合计"
    row = db.execute(
        select(LlmCorrection).where(
            LlmCorrection.field == "structure.assertion.operator"
        )
    ).scalars().one()
    assert (row.before, row.after) == ("总额等于", "不小于")


def test_derived_properties_are_not_written_back(db, default_tenant_id) -> None:
    """tolerance 标量是从 structure.assertion.tolerance 派生的，不能覆盖容差表。"""
    rule = _make_rule(db, default_tenant_id)

    graph_builder_service.apply_graph_edits(
        _StubNeo4j(),
        default_tenant_id,
        "g1",
        [_edit(tolerance=3)],
        db=db,
        rule_set_id=rule.rule_set_id,
    )

    db.refresh(rule)
    assert rule.tolerance == {"amount_percent": 0}
    assert db.execute(select(LlmCorrection)).scalars().all() == []


def test_edit_without_db_stays_graph_only(db, default_tenant_id) -> None:
    """向后兼容：不传 db（旧调用方）时只改图谱，不触碰规则数据。"""
    rule = _make_rule(db, default_tenant_id)
    stub = _StubNeo4j()

    graph_builder_service.apply_graph_edits(
        stub, default_tenant_id, "g1", [_edit(doc_type="代理协议")]
    )

    db.refresh(rule)
    assert rule.doc_type == "增值税发票"
    assert stub.updates and stub.updates[0][2]["doc_type"] == "代理协议"
    assert db.execute(select(LlmCorrection)).scalars().all() == []
