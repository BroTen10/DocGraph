# -*- coding: utf-8 -*-
"""规则导入颗粒度回归测试。

覆盖三类问题：
1. Excel 合并单元格必须展开到每个数据行；
2. 相同描述但 scope/condition/exceptions 不同的规则不得被去重合并；
3. 源表行覆盖率校验能识别漏行。

运行：backend/.venv/Scripts/python.exe tests/test_rule_import_granularity.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from openpyxl import Workbook  # noqa: E402

from app.services.rule_document_import_service import extract_document_from_file  # noqa: E402
from app.services.rule_import_service import (  # noqa: E402
    _compute_source_coverage,
    _find_similar_rule,
    _normalize_text,
    _split_text,
    import_rules_from_text,
)


class ExcelMergedCellTests(unittest.TestCase):
    def _write_workbook(self, rows, merges) -> str:
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb = Workbook()
        ws = wb.active
        ws.title = "规则"
        for row in rows:
            ws.append(row)
        for cell_range in merges:
            ws.merge_cells(cell_range)
        wb.save(path)
        wb.close()
        return path

    def test_vertical_merged_values_are_repeated_for_every_row(self) -> None:
        path = self._write_workbook(
            [
                ["文件类型", "业务条件", "规则描述"],
                ["报关单", "出口/一般贸易", "申报金额应与合同金额一致"],
                [None, None, "申报日期不得晚于合同日期"],
            ],
            ["A2:A3", "B2:B3"],
        )
        try:
            document = extract_document_from_file(path, "rules.xlsx")
        finally:
            os.unlink(path)

        self.assertEqual(len(document.source_rows), 2)
        self.assertEqual(document.source_rows[0]["values"]["文件类型"], "报关单")
        self.assertEqual(document.source_rows[1]["values"]["文件类型"], "报关单")
        self.assertEqual(document.source_rows[1]["values"]["业务条件"], "出口/一般贸易")
        self.assertEqual(document.source_rows[1]["merged_from"]["文件类型"], "A2")
        self.assertIn("[SOURCE_ROW=规则!3]", document.text)
        self.assertEqual(document.text.count("[SOURCE_ROW="), 2)

    def test_multilevel_header_is_flattened(self) -> None:
        path = self._write_workbook(
            [
                ["文件类型", "业务条件", None, "规则描述"],
                [None, "贸易类型", "业务模式", None],
                ["报关单", "出口", "代理", "金额应一致"],
            ],
            ["B1:C1"],
        )
        try:
            document = extract_document_from_file(path, "rules.xlsx")
        finally:
            os.unlink(path)

        self.assertEqual(len(document.source_rows), 1)
        values = document.source_rows[0]["values"]
        self.assertEqual(values["文件类型"], "报关单")
        self.assertEqual(values["业务条件/贸易类型"], "出口")
        self.assertEqual(values["业务条件/业务模式"], "代理")


class DedupSemanticBoundaryTests(unittest.TestCase):
    @staticmethod
    def _rule(text: str, scope: dict | None = None, structure: dict | None = None):
        return SimpleNamespace(rule_text=text, scope=scope, structure=structure)

    def test_same_text_different_scope_is_not_merged(self) -> None:
        text = "申报金额应与合同金额一致"
        existing = self._rule(text, scope={"doc_types": ["报关单"]})

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope={"doc_types": ["装箱单"]},
        )

        self.assertIsNone(matched)

    def test_same_text_same_scope_is_merged(self) -> None:
        text = "申报金额应与合同金额一致"
        scope = {"doc_types": ["报关单"]}
        existing = self._rule(text, scope=scope)

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=scope,
        )

        self.assertIs(matched, existing)

    def test_same_text_different_condition_is_not_merged(self) -> None:
        text = "金额应一致"
        scope = {"doc_types": ["报关单"]}
        existing = self._rule(
            text,
            scope=scope,
            structure={
                "condition": {"field": "贸易类型", "operator": "等于", "value": "出口"}
            },
        )

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=scope,
            new_structure={
                "condition": {"field": "贸易类型", "operator": "等于", "value": "进口"}
            },
        )

        self.assertIsNone(matched)

    def test_same_text_different_exceptions_is_not_merged(self) -> None:
        text = "付款时间不得早于收货时间"
        scope = {"doc_types": ["付款水单"]}
        existing = self._rule(
            text,
            scope=scope,
            structure={"exceptions": [{"text": "进口代理税款除外"}]},
        )

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=scope,
            new_structure={"exceptions": [{"text": "预付款除外"}]},
        )

        self.assertIsNone(matched)

    def test_zero_condition_value_is_not_treated_as_empty(self) -> None:
        text = "金额应一致"
        scope = {"doc_types": ["报关单"]}
        existing = self._rule(
            text,
            scope=scope,
            structure={"condition": {"field": "金额", "operator": "大于", "value": 0}},
        )

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=scope,
            new_structure={"condition": {"field": "金额", "operator": "大于", "value": 1}},
        )

        self.assertIsNone(matched)


class SourceCoverageTests(unittest.TestCase):
    def test_missing_source_row_is_reported(self) -> None:
        source_rows = [
            {"source_ref": "规则!2", "row": 2},
            {"source_ref": "规则!3", "row": 3},
        ]

        coverage, warnings = _compute_source_coverage(source_rows, {"规则!2"})

        self.assertIsNotNone(coverage)
        assert coverage is not None
        self.assertEqual(coverage["expected_rows"], 2)
        self.assertEqual(coverage["covered_rows"], 1)
        self.assertEqual(coverage["missing_rows"], ["规则!3"])
        self.assertTrue(any("未解析出规则" in warning for warning in warnings))

    def test_no_source_rows_has_no_coverage_report(self) -> None:
        coverage, warnings = _compute_source_coverage([], set())
        self.assertIsNone(coverage)
        self.assertEqual(warnings, [])


class TableChunkingTests(unittest.TestCase):
    def test_source_row_json_is_kept_as_one_line(self) -> None:
        line = "[SOURCE_ROW=规则!2] {" + ("描述" * 100) + "}"
        chunks = _split_text(line, max_chars=80)
        self.assertEqual(chunks, [line])


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeDB:
    def execute(self, _statement):
        return _FakeResult()

    def commit(self):
        return None

    def add(self, _obj):
        return None


class _FakeLLM:
    def __init__(self, response):
        self.response = response

    def chat_json(self, **_kwargs):
        return self.response


def _fake_create_rule(_db, _rule_set_id, payload):
    data = payload.model_dump(mode="json")
    return SimpleNamespace(model_dump=lambda mode="json": data)


class ImportPipelineSourceRefTests(unittest.TestCase):
    def test_source_ref_is_written_to_provenance_and_coverage(self) -> None:
        source_rows = [
            {
                "source_ref": "规则!2",
                "sheet": "规则",
                "row": 2,
                "values": {"文件类型": "报关单", "规则描述": "规则A"},
                "merged_from": {},
            },
            {
                "source_ref": "规则!3",
                "sheet": "规则",
                "row": 3,
                "values": {"文件类型": "报关单", "规则描述": "规则B"},
                "merged_from": {"文件类型": "A2"},
            },
        ]
        llm_response = {
            "rules": [
                {"rule_text": "规则A", "source_ref": "规则!2"},
                {"rule_text": "规则B"},
            ]
        }

        with (
            patch(
                "app.services.rule_import_service.get_llm_client",
                return_value=_FakeLLM(llm_response),
            ),
            patch(
                "app.services.rule_import_service.get_prompt",
                side_effect=lambda _db, key: (
                    "{doc_types}{check_categories}{raw_text}"
                    if key == "rule_import.user"
                    else "system"
                ),
            ),
            patch(
                "app.services.rule_import_service._known_doc_types",
                return_value=[],
            ),
            patch(
                "app.services.rule_import_service._known_check_categories",
                return_value=[],
            ),
            patch(
                "app.services.rule_import_service.create_rule",
                side_effect=_fake_create_rule,
            ),
        ):
            result = import_rules_from_text(
                _FakeDB(),
                uuid.uuid4(),
                "[SOURCE_ROW=规则!2] {\"规则描述\":\"规则A\"}\n"
                "[SOURCE_ROW=规则!3] {\"规则描述\":\"规则B\"}",
                source_rows=source_rows,
                source_meta={"filename": "rules.xlsx"},
            )

        self.assertEqual(result["imported"], 2)
        self.assertEqual(
            result["rules"][0]["provenance"]["source_ref"],
            "规则!2",
        )
        self.assertEqual(result["rules"][0]["provenance"]["row"], 2)
        self.assertEqual(result["rules"][0]["provenance"]["source_file"], "rules.xlsx")
        self.assertEqual(result["source_coverage"]["covered_rows"], 1)
        self.assertEqual(result["source_coverage"]["missing_rows"], ["规则!3"])
        self.assertTrue(
            any("未解析出规则" in warning for warning in result["import_warnings"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
