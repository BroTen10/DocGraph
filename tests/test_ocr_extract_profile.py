# -*- coding: utf-8 -*-
"""一期「OCR 增强点」单元测试。

覆盖需求方案 docs/OCR提示词与增强点治理-需求方案.md（v1.2）一期功能点：
- FR-1/FR-2：类型级增强点（专属指令 + 备注类补充字段）与提示词字段清单合成；
- FR-3：备注类字段（前缀「备注-」）不做金额/日期/数量/重量/币别/合同号归一；
- D6：备注类字段不参与「文件名 ground truth 覆盖」；
- FR-5：任务生效快照 build_applied_config 的结构。

运行：backend/.venv/Scripts/python.exe tests/test_ocr_extract_profile.py
前置：无需数据库 / Neo4j（用桩对象替代 db）。
"""

from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services import ocr_service  # noqa: E402
from app.services.field_extraction_service import (  # noqa: E402
    cross_validate_contract_no,
    normalize_fields,
)
from app.services.ocr_service import (  # noqa: E402
    OCR_PROFILE_BASE,
    OCR_PROFILE_TYPE,
    build_applied_config,
    compose_field_list,
    render_extra_instructions,
    resolve_ocr_profile,
)

_FAILED: list[str] = []
TENANT_ID = uuid.UUID(int=1)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class _StubDB:
    """只提供 resolve_* 所需的最小 execute() 语义。"""

    def __init__(self, doc_type):
        self._doc_type = doc_type

    def execute(self, *_args, **_kwargs):
        return _StubResult(self._doc_type)


def _vat_type(**overrides):
    base = dict(
        key_fields=["合同号", "发票号码", "价税合计", "开票日期"],
        ocr_extra_fields=["备注-合同号", "备注-项目名称", "备注-共计美元"],
        ocr_extract_instructions="发票备注栏也要逐字扫描；备注中的合同号、项目名称需单独抽取。",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def main() -> None:
    vat = _vat_type()

    # ---- FR-1/FR-2：字段清单合成与增强点解析 ----
    merged = compose_field_list(["合同号", "金额"], ["金额", "备注-合同号", "", " 备注-项目名称 "])
    check(
        "compose_field_list 去重保序 + 去空白",
        merged == ["合同号", "金额", "备注-合同号", "备注-项目名称"],
        str(merged),
    )

    prof = resolve_ocr_profile(
        _StubDB(vat),
        TENANT_ID,
        "增值税发票",
        OCR_PROFILE_TYPE,
    )
    check(
        "type 档：基础字段 + 备注类补充字段合并",
        prof["field_list"]
        == [
            "合同号",
            "发票号码",
            "价税合计",
            "开票日期",
            "备注-合同号",
            "备注-项目名称",
            "备注-共计美元",
        ],
        str(prof["field_list"]),
    )
    check(
        "type 档：专属指令被解析出来",
        bool(prof["instructions"]) and "备注栏" in prof["instructions"],
    )
    check("type 档：profile 标记为 type", prof["profile"] == OCR_PROFILE_TYPE)

    base_prof = resolve_ocr_profile(
        _StubDB(vat),
        TENANT_ID,
        "增值税发票",
        OCR_PROFILE_BASE,
    )
    check(
        "base 档：忽略增强点，仅基础字段",
        base_prof["extra_fields"] == []
        and base_prof["instructions"] is None
        and base_prof["field_list"] == ["合同号", "发票号码", "价税合计", "开票日期"],
        str(base_prof["field_list"]),
    )

    empty_prof = resolve_ocr_profile(
        _StubDB(None),
        TENANT_ID,
        "未配置类型",
    )
    check(
        "未配置增强点的类型：与升级前一致（无补充字段/指令）",
        empty_prof["extra_fields"] == [] and empty_prof["instructions"] is None,
    )

    # ---- 指令包装模板 ----
    text = render_extra_instructions(None, TENANT_ID, "增值税发票", prof)
    check(
        "指令被包进占位符模板（含类型名与指令正文）",
        bool(text) and "增值税发票" in text and "备注栏" in text,
        (text or "").splitlines()[0] if text else "",
    )
    check(
        "无指令时不注入",
        render_extra_instructions(
            None,
            TENANT_ID,
            "增值税发票",
            {"instructions": None},
        ) is None,
    )

    original_get_prompt = ocr_service.get_prompt
    try:
        ocr_service.get_prompt = lambda db, tenant_id, key: "{doc_type} {不存在的占位符}"  # type: ignore[assignment]
        broken = render_extra_instructions(None, TENANT_ID, "增值税发票", prof)
        check(
            "包装模板占位符损坏时退回指令正文（不阻断识别）",
            broken == prof["instructions"],
            str(broken)[:40],
        )
    finally:
        ocr_service.get_prompt = original_get_prompt  # type: ignore[assignment]

    # ---- FR-5：生效快照 ----
    snap = build_applied_config(OCR_PROFILE_TYPE, {"增值税发票": prof})
    node = snap["doc_types"]["增值税发票"]
    check(
        "生效快照记录 profile / 基础字段 / 补充字段 / 指令",
        snap["profile"] == OCR_PROFILE_TYPE
        and node["extra_fields"] == ["备注-合同号", "备注-项目名称", "备注-共计美元"]
        and node["instructions"] == prof["instructions"]
        and node["field_list"] == prof["field_list"],
    )

    # ---- FR-3：备注类字段不做值归一 ----
    normalized = normalize_fields(
        "增值税发票",
        {
            "金额": "1,234.50",
            "开票日期": "2024年7月18日",
            "合同号": "24HCSP000000123",
            "备注-共计美元": "USD 1,234.50",
            "备注-合同号": "PO-2024-0001",
            "备注-开票日期": "2024年7月18日",
            "备注-币别": "USD",
        },
    )
    check("普通金额字段仍归一为数字", normalized["金额"] == 1234.5, repr(normalized["金额"]))
    check(
        "普通日期字段仍归一为 ISO",
        normalized["开票日期"] == "2024-07-18",
        repr(normalized["开票日期"]),
    )
    check(
        "备注- 前缀字段原样保留（金额/日期/合同号/币别均不归一）",
        normalized["备注-共计美元"] == "USD 1,234.50"
        and normalized["备注-开票日期"] == "2024年7月18日"
        and normalized["备注-合同号"] == "PO-2024-0001"
        and normalized["备注-币别"] == "USD",
        repr(
            [
                normalized["备注-共计美元"],
                normalized["备注-开票日期"],
                normalized["备注-合同号"],
                normalized["备注-币别"],
            ]
        ),
    )
    check("备注类字段不产生 __aliases 附属键", "备注-合同号__aliases" not in normalized)

    # ---- D6：备注类字段不参与文件名 ground truth 覆盖 ----
    crossed = cross_validate_contract_no(
        "采购合同 24HCSP000000123.pdf",
        {"合同号": "24HCSP000000999", "备注-合同号": "PO-2024-0001"},
    )
    check(
        "普通合同号仍按文件名覆盖（防末位误识）",
        crossed["合同号"] == "24HCSP000000123"
        and crossed.get("合同号__source") == "filename_override",
        repr(crossed.get("合同号")),
    )
    check(
        "备注-合同号 不被文件名覆盖（可能是订单号/分批号）",
        crossed["备注-合同号"] == "PO-2024-0001",
        repr(crossed["备注-合同号"]),
    )

    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
