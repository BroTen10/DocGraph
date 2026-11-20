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
    import_rules_with_skills,
)


class ImportSkillScopeTests(unittest.TestCase):
    def test_default_skill_selection_resolves_ruleset_before_compile(self) -> None:
        tenant_id = uuid.uuid4()
        rule_set_id = uuid.uuid4()
        rule_set = SimpleNamespace(
            id=rule_set_id, scenario=None, use_default_skill=True
        )
        expected = {
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "rules": [],
            "errors": [],
        }

        class FakeSession:
            def __init__(self) -> None:
                self.statements = []

            def execute(self, statement):
                self.statements.append(statement)
                return SimpleNamespace(scalar_one_or_none=lambda: rule_set)

        db = FakeSession()
        directive = SimpleNamespace()
        # 基线编译（通用契约 + 场景包 + 自定义 Skill）由 compile_directive_with_skills 负责，
        # 未指定 skill_ids 时它就是唯一的装配入口。
        with (
            patch(
                "app.services.rule_parse_engine.compile_directive_with_skills",
                return_value=(directive, []),
            ) as compile_directive,
            patch(
                "app.services.rule_import_service.import_rules_from_text",
                return_value=expected,
            ) as import_rules,
        ):
            result = import_rules_with_skills(
                db,
                tenant_id,
                rule_set_id,
                "报关单申报金额应与合同金额一致",
            )

        self.assertIs(result, expected)
        self.assertEqual(len(db.statements), 1)
        compile_directive.assert_called_once_with(
            db, tenant_id, rule_set_id, scenario=None
        )
        import_rules.assert_called_once_with(
            db,
            tenant_id,
            rule_set_id,
            "报关单申报金额应与合同金额一致",
            directive=directive,
            progress=None,
            source_rows=None,
            source_meta=None,
            import_context=None,
            scenario="trade",
        )

    def test_explicit_skill_ids_use_preset_only_assembly(self) -> None:
        """显式 skill_ids = 自定义场景通道：只装配系统预设 + 勾选的 Skill。"""
        tenant_id = uuid.uuid4()
        rule_set_id = uuid.uuid4()
        rule_set = SimpleNamespace(
            id=rule_set_id, scenario="trade", use_default_skill=True
        )
        baseline_skill = SimpleNamespace(id=uuid.uuid4(), name="通用规则解析契约", enabled=True)
        extra_skill = SimpleNamespace(
            id=uuid.uuid4(),
            name="客户纠偏",
            enabled=True,
            priority=100,
            is_builtin=False,
            content={"prompt_instructions": ["本规则集的金额一律以合同币别为准。"]},
        )

        class FakeSession:
            def execute(self, statement):
                text = str(statement)
                if "rule_sets" in text:
                    return SimpleNamespace(scalar_one_or_none=lambda: rule_set)
                return SimpleNamespace(
                    scalar_one_or_none=lambda: None,
                    scalars=lambda: SimpleNamespace(
                        all=lambda: [baseline_skill, extra_skill]
                    ),
                )

        directive = SimpleNamespace(prompt_additions=[])
        merged: list[str] = []
        with (
            patch(
                "app.services.rule_parse_engine.compile_directive_with_skills",
                return_value=(directive, [baseline_skill]),
            ) as compile_directive,
            patch(
                "app.services.rule_parse_engine._merge_content",
                side_effect=lambda _d, content: merged.extend(
                    content.get("prompt_instructions") or []
                ),
            ),
            patch(
                "app.services.rule_import_service.import_rules_from_text",
                return_value={
                    "total": 0,
                    "imported": 0,
                    "skipped": 0,
                    "rules": [],
                    "errors": [],
                },
            ),
        ):
            result = import_rules_with_skills(
                FakeSession(),
                tenant_id,
                rule_set_id,
                "报关单申报金额应与合同金额一致",
                skill_ids=[baseline_skill.id, extra_skill.id],
            )

        compile_directive.assert_called_once()
        # 预设里的 Skill 不重复合并，只有勾选的那个被叠加
        self.assertEqual(merged, ["本规则集的金额一律以合同币别为准。"])
        # 自定义场景通道不加载场景包，因此不回显场景
        self.assertIsNone(result["scenario"])
        self.assertEqual(
            result["applied_skills"], ["通用规则解析契约", "客户纠偏"]
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


class ExcelPreambleAndFallbackSheetTests(unittest.TestCase):
    """回归：规则矩阵贴在「说明」表 / 表头前有标题说明段落时不得判定为空文件。"""

    def _write_workbook(self, sheets) -> str:
        """sheets: list of (title, rows)。"""
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb = Workbook()
        wb.remove(wb.active)
        for title, rows in sheets:
            ws = wb.create_sheet(title=title)
            for row in rows:
                ws.append(row)
        wb.save(path)
        wb.close()
        return path

    # 标题/说明段落（单列）+ 真正的表头行，是客户文件最常见的排版
    _PREAMBLE_ROWS = [
        ["抽检合同（合同号）清单以附件形式入系统，已知需要校验的合同是哪些"],
        ["校验合同资料文件先用清单里的合同号匹配，确保抽取合同有提供资料"],
        ["贸易类型", "过程文件", "强控是否必备", "不看单据内容，单据本身基础判断"],
        ["出口", "代理协议", "是", "需为双方回签用印版本"],
        ["出口", "出口报关单", "是", "需有报关行用印"],
    ]

    def test_only_sheet_named_instructions_is_parsed_as_rules(self) -> None:
        """唯一的工作表名叫「说明」但内容是规则矩阵时，必须兜底解析出来。"""
        path = self._write_workbook([("说明", self._PREAMBLE_ROWS)])
        try:
            document = extract_document_from_file(path, "贸易合同过程文件校验说明.xlsx")
        finally:
            os.unlink(path)

        # 三行说明段落不是规则行，只有两行规则数据被提取
        self.assertEqual(len(document.source_rows), 2)
        self.assertEqual([row["row"] for row in document.source_rows], [4, 5])
        self.assertEqual(document.metadata["sheets"][0]["header_rows"], [3])
        self.assertEqual(document.metadata["skipped_sheets"], [])
        self.assertEqual(document.metadata["fallback_sheets"], ["说明"])
        self.assertEqual(document.warnings, [])
        self.assertIn("[SOURCE_ROW=说明!4]", document.text)
        values = document.source_rows[1]["values"]
        self.assertEqual(values["过程文件"], "出口报关单")
        self.assertEqual(values["不看单据内容，单据本身基础判断"], "需有报关行用印")

    def test_preamble_rows_are_skipped_when_rules_sheet_has_title(self) -> None:
        """规则表里有标题行时，表头要落在真正的表头行上。"""
        path = self._write_workbook([("规则清单", self._PREAMBLE_ROWS)])
        try:
            document = extract_document_from_file(path, "rules.xlsx")
        finally:
            os.unlink(path)

        self.assertEqual(len(document.source_rows), 2)
        headers = list(document.source_rows[0]["values"].keys())
        self.assertEqual(headers[0], "贸易类型")
        self.assertNotIn("列2", headers)

    def test_single_column_instruction_sheet_is_not_parsed_as_rules(self) -> None:
        """单列「填写说明」没有可识别表头，仍应报空文件而不是产出垃圾规则。"""
        path = self._write_workbook([
            ("说明", [["规则描述文档模板 · 填写说明"], ["一、先看这里"], ["每条规则一行"]]),
        ])
        try:
            document = extract_document_from_file(path, "rules.xlsx")
        finally:
            os.unlink(path)

        self.assertEqual(document.source_rows, [])
        self.assertEqual(document.text, "")
        self.assertEqual(document.metadata["skipped_sheets"], ["说明"])
        self.assertEqual(document.metadata["fallback_sheets"], [])

    def test_instruction_sheet_is_skipped_when_rules_sheet_exists(self) -> None:
        """存在真正的规则表时，说明表仍按原逻辑跳过。"""
        path = self._write_workbook([
            ("填写说明", [["填写要求"], ["每条规则一行"]]),
            ("规则清单", [["规则编号", "规则正文"], ["R-001", "发票金额应等于报销单金额"]]),
        ])
        try:
            document = extract_document_from_file(path, "rules.xlsx")
        finally:
            os.unlink(path)

        self.assertEqual(len(document.source_rows), 1)
        self.assertEqual(document.metadata["skipped_sheets"], ["填写说明"])
        self.assertEqual(document.metadata["fallback_sheets"], [])


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

    def test_missing_scope_is_tolerated_for_identical_text(self) -> None:
        """同一规则一次填了适用单据、一次留空（极简填写）时不应重复入库。"""
        text = "发票价税合计应与报销单报销金额一致"
        existing = self._rule(text, scope={"doc_types": ["发票", "报销单"]})

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=None,
        )

        self.assertIs(matched, existing)

    def test_missing_scope_does_not_merge_different_rules(self) -> None:
        """scope 留空只在文本几乎一致时才容忍，不能变成"什么都合并"。"""
        existing = self._rule(
            "发票价税合计应与报销单报销金额一致",
            scope={"doc_types": ["发票", "报销单"]},
        )

        text = "发票购买方名称应与报销单所属公司名称一致"
        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=None,
        )

        self.assertIsNone(matched)

    def test_missing_scope_does_not_merge_across_conditions(self) -> None:
        """condition 或 exceptions 不同时，即使 scope 留空也不合并。"""
        text = "报销单应具备双签"
        existing = self._rule(
            text,
            scope={"doc_types": ["报销单"]},
            structure={
                "condition": {"field": "金额", "operator": "大于等于", "value": 5000}
            },
        )

        matched = _find_similar_rule(
            text,
            _normalize_text(text),
            [existing],
            new_scope=None,
            new_structure={
                "condition": {"field": "金额", "operator": "大于等于", "value": 10000}
            },
        )

        self.assertIsNone(matched)

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

    def test_same_scope_different_assertion_field_is_not_merged(self) -> None:
        """同句式异字段（数量 vs 金额）不得被文本相似度误合并。"""
        scope = {"doc_types": ["报关单", "委托出口确认单"]}
        existing = self._rule(
            "报关单数量应不大于委托出口确认单数量",
            scope=scope,
            structure={
                "assertion": {
                    "source": {"doc_type": "报关单", "field": "数量"},
                    "operator": "不大于",
                    "target": {"doc_type": "委托出口确认单", "field": "数量"},
                }
            },
        )

        matched = _find_similar_rule(
            "报关单金额应不大于委托出口确认单金额",
            _normalize_text("报关单金额应不大于委托出口确认单金额"),
            [existing],
            new_scope=scope,
            new_structure={
                "assertion": {
                    "source": {"doc_type": "报关单", "field": "金额"},
                    "operator": "不大于",
                    "target": {"doc_type": "委托出口确认单", "field": "金额"},
                }
            },
        )

        self.assertIsNone(matched)

    def test_same_assertion_subject_with_loose_wording_is_merged(self) -> None:
        """断言主体一致时，措辞不同仍视为同一规则（允许合并）。"""
        scope = {"doc_types": ["发票"]}
        existing = self._rule(
            "发票金额应与报销单金额一致",
            scope=scope,
            structure={
                "assertion": {
                    "source": {"doc_type": "发票", "field": "金额"},
                    "operator": "等于",
                    "target": {"doc_type": "报销单", "field": "金额"},
                }
            },
        )

        matched = _find_similar_rule(
            "发票金额应等于报销单金额",
            _normalize_text("发票金额应等于报销单金额"),
            [existing],
            new_scope=scope,
            new_structure={
                "assertion": {
                    "source": {"doc_type": "发票", "field": "金额"},
                    "operator": "等于",
                    "target": {"doc_type": "报销单", "field": "金额"},
                }
            },
        )

        self.assertIs(matched, existing)


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
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDB:
    def execute(self, _statement):
        from app.models import RuleSet

        if any(
            description.get("entity") is RuleSet
            for description in _statement.column_descriptions
        ):
            return _FakeResult(
                scalar=SimpleNamespace(tenant_id=uuid.UUID(int=1))
            )
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


def _fake_create_rule(_db, _tenant_id, _rule_set_id, payload):
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
                side_effect=lambda _db, _tenant_id, key: (
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


class InvalidAssertionImportTests(unittest.TestCase):
    def test_incomplete_assertion_requires_confirmation(self) -> None:
        llm_response = {
            "rules": [
                {
                    "rule_text": "派车单实际交货地址应与订单约定交货地址一致",
                    "structure": {
                        "assertion": {
                            "source": {
                                "doc_type": "派车单",
                                "field": "实际交货地址",
                            },
                            "operator": "等于",
                        }
                    },
                }
            ]
        }
        captured_payloads = []

        def capture_rule(_db, _tenant_id, _rule_set_id, payload):
            captured_payloads.append(payload)
            return _fake_create_rule(_db, _tenant_id, _rule_set_id, payload)

        with (
            patch(
                "app.services.rule_import_service.get_llm_client",
                return_value=_FakeLLM(llm_response),
            ),
            patch(
                "app.services.rule_import_service.get_prompt",
                side_effect=lambda _db, _tenant_id, key: (
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
                side_effect=capture_rule,
            ),
        ):
            result = import_rules_from_text(
                _FakeDB(),
                uuid.uuid4(),
                uuid.uuid4(),
                "派车单实际交货地址应与订单约定交货地址一致",
            )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(captured_payloads), 1)
        payload = captured_payloads[0]
        self.assertIsNone(payload.structure)
        self.assertEqual(payload.status, "pending")
        self.assertFalse(payload.enabled)
        self.assertEqual(payload.defects[0].type, "incomplete_condition")
        self.assertIn("target", payload.defects[0].description)


if __name__ == "__main__":
    unittest.main(verbosity=2)
