# -*- coding: utf-8 -*-
"""字段解析健壮性回归测试（批次 13）。

背景（验收 BX-2026-0312）：规则要求「报销金额达到5000元及以上的报销单，应同时具备
部门负责人与财务负责人签字审批」。单据实际内容完全可核验：
    报销金额（元）= 4,860.00、审批签字 = "部门负责人 李强"
但字段名字面不同（「报销金额」vs「报销金额（元）」、「部门负责人签字」vs「审批签字」），
确定性引擎记 field_missing → 规则停在「无法核验」，LLM 兜底也判 unverifiable。

本测试验证三层修复：
1. 写时字段键归一：单位限定后缀（报销金额（元）→ 报销金额）只剥"单位类"限定词，
   语义限定词（单价（不含税））不动，歧义规范名不猜，备注类字段不归一；
2. 读时兜底：_get_field 支持单位限定后缀 + 签字类字段的角色解析
   （审批签字="部门负责人 李强" → 满足"部门负责人签字"）；
3. LLM 兜底输入：裁决项带上 operator 与等价字段候选；条件不成立时按"规则不适用"落 pass。

运行：backend/.venv/bin/python tests/test_field_resolution.py
前置：无需数据库 / 网络（LLM 客户端用替身替换）。
"""

from __future__ import annotations

import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.models import Document  # noqa: E402
from app.services import doc_normalizer as dn  # noqa: E402
from app.services import graph_review_service as graph_svc  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


# 报销单类型的规范字段（与 document_types.key_fields 一致：样例分析按原文登记，
# 带单位后缀；归一后应能被规则侧的「报销金额」命中）
_BAOXIAO_CANON = [
    "报销单号", "所属公司", "申请人", "所属部门", "费用发生人", "费用类型", "费用事由",
    "费用发生日期", "费用发生年度", "提交日期", "报销金额（元）", "报销年度", "币别",
    "收款账户", "审批签字", "备注",
]

_BAOXIAO_FIELDS = {
    "报销金额（元）": 4860.0,
    "审批签字": "部门负责人 李强",
    "申请人": "张伟",
    "备注": "本报销单为 DocGraph 端到端测试用模拟单据。",
    "__inferred_doc_type__": "报销单",
}


def _baoxiao_doc() -> Document:
    return Document(
        doc_type="报销单",
        file_name="BX-2026-0312_费用报销单.docx",
        extracted_fields=dict(_BAOXIAO_FIELDS),
    )


# ============ 1. 写时字段键归一 ============

def test_unit_suffix_canonicalization() -> None:
    print("1. 写时字段键归一（单位限定后缀）")
    out = dn.normalize_extracted_keys(
        None, "报销单", dict(_BAOXIAO_FIELDS),
        canonical_fields=_BAOXIAO_CANON, aliases={},
    )
    check("报销金额（元）→ 报销金额", out.get("报销金额") == 4860.0, str(out.get("报销金额")))
    check("单位后缀键不再残留", "报销金额（元）" not in out, str(list(out)))
    check("非单位后缀字段保持原样", out.get("审批签字") == "部门负责人 李强")
    check("内部键保持原样", out.get("__inferred_doc_type__") == "报销单")

    check(
        "split_field_qualifier",
        dn.split_field_qualifier("报销金额（元）") == ("报销金额", "元"),
        str(dn.split_field_qualifier("报销金额（元）")),
    )
    check(
        "语义限定词不剥离（单价（不含税））",
        dn.strip_unit_qualifier("单价（不含税）") == "单价（不含税）",
        dn.strip_unit_qualifier("单价（不含税）"),
    )
    check(
        "非单位括号不归一",
        dn.normalize_extracted_keys(
            None, "报销单", {"报销金额（不含税）": 1},
            canonical_fields=_BAOXIAO_CANON, aliases={},
        )
        == {"报销金额（不含税）": 1},
    )

    print("2. 归一的安全边界")
    # 歧义：规范字段集里两个字段只差单位 → 不猜（避免把两个字段并成一个丢数据）
    ambiguous = dn.normalize_extracted_keys(
        None, "发票", {"金额（元）": 1, "金额（美元）": 2, "金额": 3},
        canonical_fields=["金额（元）", "金额（美元）"], aliases={},
    )
    check("歧义 base 不合并", set(ambiguous) == {"金额（元）", "金额（美元）", "金额"}, str(ambiguous))
    # 备注类字段（FR-3：备注原文语义不稳定，不归一）
    remark = dn.normalize_extracted_keys(
        None, "报销单", {"备注-共计金额（元）": 1, "报销金额（元）": 2},
        canonical_fields=_BAOXIAO_CANON, aliases={},
    )
    check("备注类字段不归一", "备注-共计金额（元）" in remark, str(remark))
    check("同时命中别名表", dn.normalize_extracted_keys(
        None, "出口报关单", {"币制": "美元", "金额": 100},
        canonical_fields=["币别", "总价"], aliases={"币制": "币别", "金额": "总价"},
    ) == {"币别": "美元", "总价": 100})
    check(
        "规范键优先于别名键（与键顺序无关）",
        dn.normalize_extracted_keys(
            None, "出口报关单", {"金额": 100, "总价": 200},
            canonical_fields=["总价"], aliases={"金额": "总价"},
        )
        == {"总价": 200},
        str(dn.normalize_extracted_keys(
            None, "出口报关单", {"金额": 100, "总价": 200},
            canonical_fields=["总价"], aliases={"金额": "总价"},
        )),
    )


# ============ 3. 读时字段解析（_get_field） ============

def test_read_time_resolution() -> None:
    print("3. 读时字段解析：单位后缀 + 签字角色")
    doc = _baoxiao_doc()
    check("报销金额（读时兜底）", graph_svc._get_field(doc, "报销金额") == 4860.0)
    check("精确字段不受影响", graph_svc._get_field(doc, "申请人") == "张伟")
    check(
        "部门负责人签字 ← 审批签字=部门负责人 李强",
        graph_svc._get_field(doc, "部门负责人签字") == "部门负责人 李强",
    )
    check("财务负责人签字（单据确实没有）→ None", graph_svc._get_field(doc, "财务负责人签字") is None)
    check("未签字字段不走角色解析", graph_svc._get_field(doc, "费用发生地") is None)
    check(
        "字段名即角色（部门负责人=王五）",
        graph_svc._get_field(
            Document(doc_type="报销单", file_name="y.docx",
                     extracted_fields={"部门负责人": "王五", "审批签字": "李强"}),
            "部门负责人签字",
        )
        == "王五",
    )
    check(
        "角色未写明的签字栏不误配",
        graph_svc._get_field(
            Document(doc_type="报销单", file_name="z.docx",
                     extracted_fields={"审批签字": "李强"}),
            "部门负责人签字",
        )
        is None,
    )
    # 否定表述不得当作已签字（否则比"判不了"更糟：假通过）
    doc_neg = Document(doc_type="报销单", file_name="neg.docx", extracted_fields={
        "审批签字": "部门负责人 李强；财务负责人 未签字",
        "报销金额": 5860.0,
    })
    check(
        "并列签字栏按角色取片段",
        graph_svc._get_field(doc_neg, "部门负责人签字") == "部门负责人 李强",
        str(graph_svc._get_field(doc_neg, "部门负责人签字")),
    )
    check(
        "「财务负责人 未签字」不视为已签字",
        graph_svc._get_field(doc_neg, "财务负责人签字") is None,
        str(graph_svc._get_field(doc_neg, "财务负责人签字")),
    )
    check(
        "并列签字栏里的否定角色不视为已签字（角色解析路径）",
        graph_svc._get_field(
            Document(doc_type="报销单", file_name="n2.docx",
                     extracted_fields={"审批签字": "财务负责人 未签字"}),
            "财务负责人签字",
        )
        is None,
    )
    # 边界：字面同名字段走精确取值（exact match 是权威数据，不做语义改写），
    # 值为"未签字"时由 LLM 兜底裁决按语义判 fail（已在 02-问题报销场景验证）。
    check(
        "精确同名字段保持原值",
        graph_svc._get_field(
            Document(doc_type="报销单", file_name="n3.docx",
                     extracted_fields={"财务负责人签字": "未签字"}),
            "财务负责人签字",
        )
        == "未签字",
    )


# ============ 4. 证据包视角：等价字段候选 ============

def test_equivalent_candidates() -> None:
    print("4. 证据包为规则侧字段名提供等价候选")
    from app.services.review_evidence import build_evidence_bundle, candidate_keys

    bundle = build_evidence_bundle([_baoxiao_doc()])
    got = candidate_keys(bundle, "报销金额")
    check("命中单位后缀候选", len(got) == 1 and got[0]["key"] == "报销金额（元）", str(got))
    check("已同名时不再给候选", candidate_keys(bundle, "审批签字") == [])
    check("无候选返回空", candidate_keys(bundle, "财务负责人签字") == [])


def main() -> None:
    test_unit_suffix_canonicalization()
    test_read_time_resolution()
    test_equivalent_candidates()
    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
