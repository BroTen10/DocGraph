# -*- coding: utf-8 -*-
"""规则描述模板的文档级声明解析测试。

覆盖：
1. Excel「场景与术语」元数据表 → 场景 + 术语别名，且不产生规则行；
2. 说明类工作表被跳过，不进入规则解析；
3. 文本类文档的「场景与术语」声明块被剥离并解析；
4. import_context 渲染。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from openpyxl import Workbook  # noqa: E402

from app.services.rule_document_import_service import (  # noqa: E402
    build_import_context,
    extract_document_from_file,
)

_TEMPLATE_ROWS = [
    ["规则编号", "规则正文", "适用文件", "触发条件", "检查类别"],
    ["R-001", "发票金额应等于报销单金额", "发票", "一般报销", "信息准确性"],
]


def _write_workbook(sheets) -> str:
    """sheets: list of (title, rows)。返回临时文件路径。"""
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


class TemplateMetadataTests(unittest.TestCase):
    def test_metadata_sheet_declares_scenario_and_glossary(self) -> None:
        path = _write_workbook([
            ("填写说明", [["填写要求"], ["每条规则一行"]]),
            ("场景与术语", [
                ["项目", "值"],
                ["场景", "expense"],
                ["场景名称", "费用报销审查"],
                ["术语", "报销单 = 费用报销单、报销凭单"],
                ["术语", "发票 = 增值税发票"],
            ]),
            ("规则清单", _TEMPLATE_ROWS),
        ])
        try:
            document = extract_document_from_file(path, "规则描述文档模板.xlsx")
        finally:
            os.unlink(path)

        metadata = document.metadata
        self.assertEqual(metadata["scenario"], "expense")
        self.assertEqual(metadata["scene_label"], "费用报销审查")
        glossary = {item["canonical"]: item["aliases"] for item in metadata["glossary"]}
        self.assertEqual(glossary["报销单"], ["费用报销单", "报销凭单"])
        self.assertEqual(glossary["发票"], ["增值税发票"])
        self.assertEqual(metadata["skipped_sheets"], ["填写说明"])

        # 说明表与元数据表都不产生规则行，只有规则清单表产生 1 行
        self.assertEqual(len(document.source_rows), 1)
        self.assertEqual(document.source_rows[0]["sheet"], "规则清单")
        self.assertNotIn("填写说明", document.text)
        self.assertNotIn("expense", document.text)

    def test_import_context_renders_declaration(self) -> None:
        context = build_import_context({
            "scenario": "expense",
            "scene_label": "费用报销审查",
            "glossary": [{"canonical": "报销单", "aliases": ["费用报销单"]}],
            "notes": ["适用说明：仅覆盖境内差旅"],
        })

        self.assertIn("业务场景：费用报销审查 / scenario=expense", context)
        self.assertIn("报销单 ← 费用报销单", context)
        self.assertIn("仅覆盖境内差旅", context)

    def test_empty_declaration_renders_nothing(self) -> None:
        self.assertEqual(build_import_context({}), "")
        self.assertEqual(build_import_context({"scenario": None, "glossary": []}), "")


class TextDeclarationTests(unittest.TestCase):
    def _write_text(self, text: str, suffix: str = ".md") -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_declaration_block_is_parsed_and_removed(self) -> None:
        content = """场景与术语
场景：supplier
场景名称：供应商准入审查
术语：营业执照 = 三证合一执照
（备注行会被忽略）

# 规则清单

1. 营业执照应在有效期内。
"""
        path = self._write_text(content)
        try:
            document = extract_document_from_file(path, "rules.md")
        finally:
            os.unlink(path)

        self.assertEqual(document.metadata["scenario"], "supplier")
        self.assertEqual(document.metadata["scene_label"], "供应商准入审查")
        self.assertEqual(
            document.metadata["glossary"],
            [{"canonical": "营业执照", "aliases": ["三证合一执照"]}],
        )
        self.assertNotIn("场景与术语", document.text)
        self.assertIn("营业执照应在有效期内", document.text)

    def test_document_without_declaration_keeps_text(self) -> None:
        path = self._write_text("1. 报销单金额应与发票金额一致。\n")
        try:
            document = extract_document_from_file(path, "rules.txt")
        finally:
            os.unlink(path)

        self.assertIsNone(document.metadata["scenario"])
        self.assertIn("报销单金额应与发票金额一致", document.text)

    def test_html_comment_is_stripped(self) -> None:
        content = """# 规则文档

<!-- 使用说明：本段不会被解析
场景：expense
-->

场景与术语
场景：expense
术语：报销单 = 费用报销单

1. 报销单金额应与发票金额一致。
"""
        path = self._write_text(content)
        try:
            document = extract_document_from_file(path, "rules.md")
        finally:
            os.unlink(path)

        self.assertNotIn("使用说明", document.text)
        self.assertEqual(document.metadata["scenario"], "expense")
        self.assertIn("报销单金额应与发票金额一致", document.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
