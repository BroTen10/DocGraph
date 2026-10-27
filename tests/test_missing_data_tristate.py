#!/usr/bin/env python3
"""Regression tests for missing values in multi-document comparisons."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models import Document  # noqa: E402
from app.services.graph_review_service import (  # noqa: E402
    _cmp_date,
    _cmp_eq,
    _cmp_numeric,
    _cmp_total_eq,
    _dispatch_compare,
)


def doc(
    doc_type: str,
    fields: dict,
    name: str,
) -> Document:
    return Document(
        id=uuid.uuid4(),
        contract_id=uuid.uuid4(),
        file_name=name,
        file_path=name,
        file_type="pdf",
        doc_type=doc_type,
        is_required=False,
        ocr_status="done",
        extracted_fields=fields,
        ocr_layout={},
    )


class MissingDataTriStateTests(unittest.TestCase):
    def test_blank_string_is_missing(self) -> None:
        docs = [
            doc("代理协议", {"协议方": "Example Ltd."}, "a.pdf"),
            doc("委托出口确认单", {"委托方": "  "}, "b.pdf"),
        ]
        verdict, _, detail = _cmp_eq(
            "代理协议", "协议方", "委托出口确认单", "委托方", docs
        )
        self.assertEqual(verdict, "unverifiable")
        self.assertEqual(detail["reason"], "field_data_missing")

    def test_partial_total_is_unverifiable(self) -> None:
        docs = [
            doc("付款水单", {"金额": "300"}, "pay-1.pdf"),
            doc("付款水单", {"金额": None}, "pay-2.pdf"),
            doc("收款水单", {"金额": "1000"}, "recv.pdf"),
        ]
        verdict, _, detail = _cmp_total_eq(
            "付款水单", "金额", "收款水单", "金额", docs
        )
        self.assertEqual(verdict, "unverifiable")
        self.assertEqual(detail["reason"], "incomplete_field_data")
        self.assertEqual(detail["missing_fields"][0]["doc_files"], ["pay-2.pdf"])

    def test_partial_numeric_comparison_is_unverifiable(self) -> None:
        docs = [
            doc("发货单", {"数量": "10"}, "ship-1.pdf"),
            doc("发货单", {"数量": ""}, "ship-2.pdf"),
            doc("合同", {"数量": "100"}, "contract.pdf"),
        ]
        verdict, _, detail = _cmp_numeric(
            "发货单", "数量", "合同", "数量", docs, "le", 0
        )
        self.assertEqual(verdict, "unverifiable")
        self.assertEqual(detail["reason"], "incomplete_field_data")

    def test_partial_date_comparison_is_unverifiable(self) -> None:
        docs = [
            doc("委托出口确认单", {"签订日期": "2026-01-01"}, "a.pdf"),
            doc("委托出口确认单", {"签订日期": None}, "b.pdf"),
            doc("出口报关单", {"申报日期": "2026-01-02"}, "c.pdf"),
        ]
        verdict, _, detail = _cmp_date(
            "委托出口确认单",
            "签订日期",
            "出口报关单",
            "申报日期",
            docs,
            allow_same_day=True,
        )
        self.assertEqual(verdict, "unverifiable")
        self.assertEqual(detail["reason"], "incomplete_field_data")

    def test_any_aggregation_can_use_complete_pair(self) -> None:
        docs = [
            doc("发货单", {"数量": "10"}, "ship-1.pdf"),
            doc("发货单", {"数量": None}, "ship-2.pdf"),
            doc("合同", {"数量": "50"}, "contract.pdf"),
        ]
        verdict, _, _ = _cmp_numeric(
            "发货单",
            "数量",
            "合同",
            "数量",
            docs,
            "le",
            0,
            aggregate="ANY",
        )
        self.assertEqual(verdict, "pass")

    def test_not_before_time_operator(self) -> None:
        docs = [
            doc("付款水单", {"付款日期": "2026-01-03"}, "pay.pdf"),
            doc("收款水单", {"收款日期": "2026-01-02"}, "recv.pdf"),
        ]
        verdict, _, _ = _dispatch_compare(
            "时间不早于",
            "付款水单",
            "付款日期",
            "收款水单",
            "收款日期",
            docs,
            tolerance=0,
        )
        self.assertEqual(verdict, "pass")

    def test_unknown_operator_is_unverifiable(self) -> None:
        docs = [
            doc("付款水单", {"金额": "10"}, "pay.pdf"),
            doc("收款水单", {"金额": "10"}, "recv.pdf"),
        ]
        verdict, _, detail = _dispatch_compare(
            "大约相同",
            "付款水单",
            "金额",
            "收款水单",
            "金额",
            docs,
            tolerance=0,
        )
        self.assertEqual(verdict, "unverifiable")
        self.assertEqual(detail["reason"], "unsupported_operator")


if __name__ == "__main__":
    unittest.main(verbosity=2)
