# -*- coding: utf-8 -*-
"""存量不完整结构断言的图谱构建兼容测试。"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from app.schemas.graph import EdgeData, EntityData, RuleGraphConvertResult  # noqa: E402
from app.services.graph_builder_service import build_graph  # noqa: E402


class _EmptyScalars:
    def all(self):
        return []


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()


class _SnapshotDB:
    def execute(self, _statement):
        return _EmptyResult()

    def add(self, value):
        value.id = uuid.uuid4()

    def commit(self):
        return None

    def refresh(self, _value):
        return None


class _GraphWriter:
    def __init__(self):
        self.written = None

    def write_rule_graph(self, **kwargs):
        self.written = kwargs

    def get_graph_data(self, _graph_id, _tenant_id):
        return {"node_count": 3, "edge_count": 2, "nodes": [], "edges": []}


class InvalidStoredAssertionTests(unittest.TestCase):
    def test_build_falls_back_to_text_when_stored_assertion_is_incomplete(self) -> None:
        rule = SimpleNamespace(
            id=uuid.uuid4(),
            rule_code="R0431",
            rule_text="派车单实际交货地址应与订单约定交货地址一致",
            doc_type=None,
            check_category="信息准确性",
            tolerance={},
            priority=100,
            enabled=True,
            intents=["信息准确性"],
            scope=None,
            structure={
                "assertion": {
                    "source": {"field": "实际交货地址"},
                    "operator": "等于",
                }
            },
        )
        fallback_result = RuleGraphConvertResult(
            entities=[
                EntityData(
                    name="派车单.实际交货地址",
                    type="Field",
                    attributes={"doc_type": "派车单", "rule_id": "R0431"},
                )
            ],
            relationships=[
                EdgeData(
                    source="派车单.实际交货地址",
                    target="订单.约定交货地址",
                    type="COMPARE_TO",
                    attributes={"rule_id": "R0431", "operator": "等于"},
                )
            ],
            confidence=1.0,
            auto_confirmed=False,
        )
        fallback_calls = []

        def fallback(*args, **kwargs):
            fallback_calls.append((args, kwargs))
            return fallback_result

        writer = _GraphWriter()
        tenant_id = uuid.uuid4()
        rule_set_id = uuid.uuid4()
        with self.assertLogs(
            "app.services.graph_builder_service",
            level="WARNING",
        ) as logs:
            with (
                patch(
                    "app.services.graph_builder_service.get_rule_set",
                    return_value=SimpleNamespace(id=rule_set_id),
                ),
                patch(
                    "app.services.graph_builder_service.get_enabled_rules_for_snapshot",
                    return_value=[rule],
                ),
                patch(
                    "app.services.graph_builder_service.get_setting",
                    return_value=0.9,
                ),
                patch(
                    "app.services.graph_builder_service._convert_one_rule",
                    side_effect=fallback,
                ),
            ):
                response = build_graph(
                    _SnapshotDB(),
                    tenant_id,
                    rule_set_id,
                    neo4j=writer,
                    auto_confirm_all=True,
                )

        self.assertEqual(len(fallback_calls), 1)
        self.assertIn("R0431", logs.output[0])
        self.assertEqual(response.rule_count, 1)
        self.assertEqual(response.node_count, 3)
        self.assertEqual(writer.written["graph_id"], response.graph_id)
        self.assertTrue(
            any(
                edge["target"] == "订单.约定交货地址"
                for edge in writer.written["relationships"]
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
