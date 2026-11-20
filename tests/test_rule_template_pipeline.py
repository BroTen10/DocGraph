# -*- coding: utf-8 -*-
"""规则描述模板 → 解析链路 的端到端（离线）回归测试。

用仓库内的示例模板 templates/规则描述文档模板-示例-费用报销.xlsx 走一遍：
提取 → 场景与术语声明 → 提示词装配 → LLM 解析（桩）→ 入库前处理 → 来源行覆盖率。

不依赖真实 LLM 与数据库，用于保证「客户按模板填写 → 系统拆解」这条路径不退化。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_repo_root() -> Path:
    """从当前文件与工作目录向上定位仓库根（pytest 会 chdir 到 backend）。"""
    for base in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in (base, *base.parents):
            if (parent / "backend" / "app").is_dir() and (parent / "templates").is_dir():
                return parent
    raise RuntimeError("无法定位仓库根目录")


_REPO_ROOT = _find_repo_root()
_BACKEND_DIR = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.default_rule_parse_skill import merge_skill_contents, packs_for_scenario  # noqa: E402
from app.services.rule_document_import_service import (  # noqa: E402
    build_import_context,
    extract_document_from_file,
)
from app.services.rule_import_service import import_rules_from_text  # noqa: E402

_TEMPLATE = _REPO_ROOT / "templates" / "规则描述文档模板-示例-费用报销.xlsx"
_TRADE_MARK = "文件类型列和原文明确涉及的实际单据"
_GENERIC_MARK = "原子规则以一个可独立得出"

_SOURCE_ROW_RE = re.compile(r"\[SOURCE_ROW=([^\]]+)\]\s*(\{.*\})")


def _pick(values: dict, *prefixes: str) -> str:
    """按表头主名取值（表头带（必填）等档位标注，用前缀匹配保持稳健）。"""
    for key, value in values.items():
        if any(str(key).startswith(prefix) for prefix in prefixes):
            return value or ""
    return ""


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def scalar_one_or_none(self):
        return SimpleNamespace(tenant_id=uuid.UUID(int=1))


class _FakeDB:
    def execute(self, _statement):
        return _FakeResult()

    def commit(self):
        return None

    def add(self, _obj):
        return None

    def flush(self):
        # 检查项登记在同一事务内依赖 flush 才能被后续查询看到
        return None


class _EchoLLM:
    """把用户提示词里的每个 [SOURCE_ROW=...] 行原样变成一条规则。

    模拟"人只填必填列、其余由模型推断"：留空的列不产生缺陷，只是从正文取 rule_text。
    """

    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_prompt = ""

    def chat_json(self, messages, **_kwargs):
        self.system_prompt = messages[0]["content"]
        self.user_prompt = messages[-1]["content"]
        rules = []
        for ref, payload in _SOURCE_ROW_RE.findall(self.user_prompt):
            values = json.loads(payload)
            intents = [x for x in [_pick(values, "检查类别")] if x]
            rules.append({
                "rule_text": _pick(values, "规则正文"),
                "source_ref": ref.strip(),
                "scope": {
                    "doc_types": [d for d in re.split(r"[、,]", _pick(values, "适用单据")) if d.strip()],
                    "intents": intents,
                },
            })
        return {"rules": rules}


def _fake_create_rule(_db, _tenant_id, _rule_set_id, payload):
    data = payload.model_dump(mode="json")
    return SimpleNamespace(model_dump=lambda mode="json": data)


@unittest.skipUnless(_TEMPLATE.exists(), "示例模板不存在，请先运行 scripts/build_rule_template.py")
class TemplatePipelineTests(unittest.TestCase):
    def _run(self, scenario: str):
        document = extract_document_from_file(str(_TEMPLATE), _TEMPLATE.name)
        merged = merge_skill_contents([p.content for p in packs_for_scenario(scenario)])
        directive = SimpleNamespace(
            prompt_additions=merged["prompt_instructions"],
            field_mappings=merged["field_mappings"],
            defaults=merged["defaults"],
            validations=[],
            text_preprocessing=[],
            term_normalization=merged["term_normalization"],
            domain_context=merged["domain_context"],
        )
        llm = _EchoLLM()
        with (
            patch("app.services.rule_import_service.get_llm_client", return_value=llm),
            patch(
                "app.services.rule_import_service.get_prompt",
                side_effect=lambda _db, _tenant_id, key: (
                    "{import_context}{doc_types}{check_categories}{raw_text}"
                    if key == "rule_import.user"
                    else "base-system"
                ),
            ),
            patch("app.services.rule_import_service._known_doc_types", return_value=[]),
            patch("app.services.rule_import_service._known_check_categories", return_value=[]),
            patch("app.services.rule_import_service.create_rule", side_effect=_fake_create_rule),
        ):
            result = import_rules_from_text(
                _FakeDB(),
                uuid.uuid4(),
                uuid.uuid4(),
                document.text,
                directive=directive,
                source_rows=document.source_rows,
                source_meta={"filename": _TEMPLATE.name, **document.metadata},
                import_context=build_import_context(document.metadata),
            )
        return document, llm, result

    def test_template_rows_are_all_parsed_and_covered(self) -> None:
        document, llm, result = self._run("expense")

        self.assertEqual(len(document.source_rows), 10)
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["imported"], 10)
        self.assertEqual(result["source_coverage"]["covered_rows"], 10)
        self.assertEqual(result["source_coverage"]["missing_rows"], [])
        # 每条规则都能回填到来源行（Excel 行号），便于人工核对
        self.assertTrue(all(r["provenance"]["row"] for r in result["rules"]))

    def test_scenario_selects_domain_pack_and_skips_other_domains(self) -> None:
        _, llm, _ = self._run("expense")
        self.assertIn(_GENERIC_MARK, llm.system_prompt)
        self.assertNotIn(_TRADE_MARK, llm.system_prompt)

        _, trade_llm, _ = self._run("trade")
        self.assertIn(_TRADE_MARK, trade_llm.system_prompt)

    def test_declaration_is_injected_into_user_prompt(self) -> None:
        _, llm, _ = self._run("expense")

        self.assertIn("### 本次导入声明", llm.user_prompt)
        self.assertIn("scenario=expense", llm.user_prompt)
        self.assertIn("报销单 ← 费用报销单、报销凭单", llm.user_prompt)

    def test_empty_optional_columns_are_not_treated_as_missing(self) -> None:
        """极简填写（只填必填列）必须被明确告知"留空=交给模型推断"。"""
        document, llm, result = self._run("expense")

        # 提示词层面的契约：系统注入 + 通用契约包都声明了留空语义
        self.assertIn("留空的列表示交由你从规则正文推断，不是信息缺失", llm.system_prompt)
        self.assertIn("不得因某列留空而报缺陷", llm.system_prompt)
        # 示例文件里第 2 行只填了必填列 + 依据来源，其余列留空
        second_row = document.source_rows[1]["values"]
        self.assertEqual(_pick(second_row, "触发条件"), "")
        self.assertEqual(_pick(second_row, "例外"), "")
        self.assertEqual(_pick(second_row, "容差"), "")
        # 留空不影响入库：10 行全部成功，且没有缺陷
        self.assertEqual(result["imported"], 10)
        self.assertTrue(all(not r["defects"] for r in result["rules"]))

    def test_header_tier_markers_are_visible_and_consistent(self) -> None:
        document, _, _ = self._run("expense")

        headers = list(document.source_rows[0]["values"].keys())
        self.assertEqual(len(headers), 9)
        self.assertTrue(headers[0].endswith("（必填）"), headers[0])
        # 只有第一列是必填，其余都必须标注为建议填/可留空
        self.assertFalse(any(h.endswith("（必填）") for h in headers[1:]))
        self.assertTrue(all(h.endswith(("（建议填）", "（可留空）")) for h in headers[1:]))


class MinimalTemplateTests(unittest.TestCase):
    """只填「规则正文」一列的最小模板也要能跑通。"""

    def test_single_column_template_is_enough(self) -> None:
        from openpyxl import Workbook

        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb = Workbook()
        ws = wb.active
        ws.title = "规则清单"
        ws.append(["规则正文（判定内容）（必填）", "适用单据/对象（建议填）"])
        ws.append(["发票开票日期应不晚于报销单提交日期。", None])
        ws.append(["报销单申请人应为费用实际发生人。", None])
        wb.save(path)
        wb.close()

        try:
            document = extract_document_from_file(path, "最简规则.xlsx")
        finally:
            os.unlink(path)

        self.assertEqual(len(document.source_rows), 2)
        self.assertEqual(_pick(document.source_rows[0]["values"], "适用单据"), "")
        self.assertIn("[SOURCE_ROW=规则清单!2]", document.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
