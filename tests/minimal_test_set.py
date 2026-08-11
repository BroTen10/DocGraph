# -*- coding: utf-8 -*-
"""最简图谱规则测试集（纯数据，无逻辑）。

目标：验证"图谱规则是否正确被调用对文档进行检查"。

覆盖的图谱关系类型：
1. REQUIRED      -> 齐套性检查（_check_required_docs）
2. MUST_STAMP    -> 基础判断/印章检查（_check_stamp_requirements）
3. COMPARE_TO    -> 字段比对（_check_compare_relationships），
                    本测试集覆盖两个算子：总额等于（信息准确性）、时间不晚于（时间逻辑）

所有规则均为结构化规则（structure.assertion），图谱构建走确定性程序化转换，
不依赖 LLM；文档为合成文档（extracted_fields 直接预设，跳过 OCR），保证测试快速且可复现。
"""

from __future__ import annotations

from typing import Any

# ============ 规则集（4 条，覆盖 3 类图谱关系） ============
# 规则结构说明：
# - 齐套性/基础判断：check_category 固定，图谱构建时程序化生成 REQUIRED / MUST_STAMP 关系
# - 信息准确性/时间逻辑：structure.assertion 声明源/目标字段与算子，
#   图谱构建时程序化生成 COMPARE_TO 关系（确定性，不调 LLM）

RULES: list[dict[str, Any]] = [
    {
        "doc_type": "出口报关单",
        "check_category": "齐套性",
        "rule_text": "出口报关单为必备文件，缺失则审查不通过。",
        "tolerance": {},
        "priority": 10,
    },
    {
        "doc_type": "代理协议",
        "check_category": "基础判断",
        "rule_text": "代理协议应双方回签用印，未检测到印章则不通过。",
        "tolerance": {},
        "priority": 20,
    },
    {
        "doc_type": "出口报关单",
        "check_category": "信息准确性",
        "rule_text": "出口报关单总价应等于委托出口确认单金额（容差5%）。",
        "tolerance": {"amount_percent": 5},
        "priority": 30,
        "structure": {
            "assertion": {
                "source": {"doc_type": "出口报关单", "field": "总价", "aggregate": "SUM"},
                "target": {"doc_type": "委托出口确认单", "field": "金额", "aggregate": "SUM"},
                "operator": "总额等于",
                "tolerance": 5,
            }
        },
    },
    {
        "doc_type": "出口报关单",
        "check_category": "时间逻辑",
        "rule_text": "委托出口确认单签订日期应不晚于出口报关单申报日期。",
        "tolerance": {},
        "priority": 40,
        "structure": {
            "assertion": {
                "source": {"doc_type": "委托出口确认单", "field": "签订日期"},
                "target": {"doc_type": "出口报关单", "field": "申报日期"},
                "operator": "时间不晚于",
                "tolerance": 0,
            }
        },
    },
]

# 各规则期望对应的图谱节点名（COMPARE_TO 断言的 source/target）
EXPECTED_GRAPH_NODES: dict[str, tuple[str, str]] = {
    "信息准确性": ("出口报关单.总价|SUM", "委托出口确认单.金额|SUM"),
    "时间逻辑": ("委托出口确认单.签订日期", "出口报关单.申报日期"),
}


# ============ 测试场景（合成文档 + 期望结果） ============

def doc(doc_type: str, fields: dict | None = None, has_stamp: bool | None = None) -> dict:
    """合成文档规格：doc_type + extracted_fields + 印章判断。"""
    return {"doc_type": doc_type, "fields": fields or {}, "has_stamp": has_stamp}


# 基准文档：三份文档齐全、字段一致、印章存在、日期顺序正确
_BASE_DOCS = [
    doc("代理协议", {"协议方": "甲公司"}, has_stamp=True),
    doc("委托出口确认单", {"金额": 10000, "签订日期": "2026-01-01"}),
    doc("出口报关单", {"总价": 10000, "申报日期": "2026-01-05"}),
]

# 期望结果以 check_category 为键（每条规则对应一个唯一类别）。
# 断言时校验：结果值、source=="graph"、rule_text、COMPARE_TO 的图谱节点名。
SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "TC-01",
        "name": "文档齐全且字段一致：四类图谱规则全部 pass",
        "docs": _BASE_DOCS,
        "expected": {
            "齐套性": "pass",
            "基础判断": "pass",
            "信息准确性": "pass",
            "时间逻辑": "pass",
        },
    },
    {
        "id": "TC-02",
        "name": "缺失必备文件：齐套性 fail，其余受缺失影响变为 unverifiable",
        "docs": [
            doc("代理协议", {"协议方": "甲公司"}, has_stamp=True),
            doc("委托出口确认单", {"金额": 10000, "签订日期": "2026-01-01"}),
            # 缺少 出口报关单
        ],
        "expected": {
            "齐套性": "fail",
            "基础判断": "pass",
            "信息准确性": "unverifiable",
            "时间逻辑": "unverifiable",
        },
    },
    {
        "id": "TC-03",
        "name": "代理协议无印章：基础判断 fail",
        "docs": [
            doc("代理协议", {"协议方": "甲公司"}, has_stamp=False),
            doc("委托出口确认单", {"金额": 10000, "签订日期": "2026-01-01"}),
            doc("出口报关单", {"总价": 10000, "申报日期": "2026-01-05"}),
        ],
        "expected": {
            "齐套性": "pass",
            "基础判断": "fail",
            "信息准确性": "pass",
            "时间逻辑": "pass",
        },
    },
    {
        "id": "TC-04",
        "name": "总价 12000 vs 金额 10000（偏差20% > 容差5%）：信息准确性 fail",
        "docs": [
            doc("代理协议", {"协议方": "甲公司"}, has_stamp=True),
            doc("委托出口确认单", {"金额": 10000, "签订日期": "2026-01-01"}),
            doc("出口报关单", {"总价": 12000, "申报日期": "2026-01-05"}),
        ],
        "expected": {
            "齐套性": "pass",
            "基础判断": "pass",
            "信息准确性": "fail",
            "时间逻辑": "pass",
        },
    },
    {
        "id": "TC-05",
        "name": "申报日期早于签订日期：时间逻辑 fail",
        "docs": [
            doc("代理协议", {"协议方": "甲公司"}, has_stamp=True),
            doc("委托出口确认单", {"金额": 10000, "签订日期": "2026-01-01"}),
            doc("出口报关单", {"总价": 10000, "申报日期": "2025-12-20"}),
        ],
        "expected": {
            "齐套性": "pass",
            "基础判断": "pass",
            "信息准确性": "pass",
            "时间逻辑": "fail",
        },
    },
    {
        "id": "TC-06",
        "name": "委托出口确认单缺少金额字段：信息准确性 unverifiable",
        "docs": [
            doc("代理协议", {"协议方": "甲公司"}, has_stamp=True),
            doc("委托出口确认单", {"签订日期": "2026-01-01"}),  # 金额缺失
            doc("出口报关单", {"总价": 10000, "申报日期": "2026-01-05"}),
        ],
        "expected": {
            "齐套性": "pass",
            "基础判断": "pass",
            "信息准确性": "unverifiable",
            "时间逻辑": "pass",
        },
    },
]


# ============ 完整审查链路集成场景（走 review_service.start_review） ============
# 复用 TC-01 的文档规格，验证 review_service 在存在图谱快照时确实调用图谱规则
# （而非 fallback 到旧逻辑），结果全部标记 source="graph"。
PIPELINE_SCENARIO = {
    "id": "TC-07",
    "name": "完整审查链路（start_review + snapshot）：结果全部来自图谱",
    "docs": _BASE_DOCS,
    "expected": {
        "齐套性": "pass",
        "基础判断": "pass",
        "信息准确性": "pass",
        "时间逻辑": "pass",
    },
}


# ============ 无图谱快照的边界场景（TC-08） ============
# 规则集没有快照/图谱时，run_graph_review 应抛 ValueError（调用方走 fallback），
# 反向证明"有图谱时确实由图谱驱动审查"。
NEGATIVE_SCENARIO = {
    "id": "TC-08",
    "name": "无图谱快照：图谱审查入口应抛出 ValueError（不静默走旧逻辑）",
}
