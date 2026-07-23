"""种子规则数据：基于 PRD 第 2.3 节"出口代理"场景规则矩阵。

调用 init_seed_rules(db) 在首次启动时插入默认规则（若 rules 表为空）。
规则按"文件类型 × 检查项"二维组织，自然语言描述 + 容差参数。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import (
    CHECK_ACCURACY,
    CHECK_COMPLETENESS,
    CHECK_STAMP,
    CHECK_TIME_LOGIC,
    DOC_AGENCY_AGREEMENT,
    DOC_CUSTOMS_DECLARATION,
    DOC_ENTRUST_CONFIRM,
    DOC_RECEIPT,
    DOC_RECEIVE_VOUCHER,
    DOC_PAY_VOUCHER,
    DOC_WAREHOUSE_INOUT,
    DOC_DISPATCH,
    DOC_WAYBILL,
    OPTIONAL_DOC_TYPES,
    REQUIRED_DOC_TYPES,
    STAMP_REQUIREMENTS,
)
from ..models import Rule

logger = logging.getLogger(__name__)


def _rule(
    doc_type: str,
    check_category: str,
    rule_text: str,
    tolerance: dict | None = None,
    priority: int = 100,
) -> dict:
    return {
        "doc_type": doc_type,
        "check_category": check_category,
        "rule_text": rule_text,
        "tolerance": tolerance or {},
        "enabled": True,
        "priority": priority,
    }


# ============ 齐套性规则：每个必备文件类型一条 ============
# 齐套性检查的规则文本描述该文件类型的必备性
_COMPLETENESS_RULES = [
    _rule(
        DOC_AGENCY_AGREEMENT,
        CHECK_COMPLETENESS,
        "代理协议为必备文件，缺失则审查不通过。",
        priority=10,
    ),
    _rule(
        DOC_ENTRUST_CONFIRM,
        CHECK_COMPLETENESS,
        "委托出口确认单为必备文件，缺失则审查不通过。",
        priority=10,
    ),
    _rule(
        DOC_CUSTOMS_DECLARATION,
        CHECK_COMPLETENESS,
        "出口报关单为必备文件，缺失则审查不通过。",
        priority=10,
    ),
    _rule(
        DOC_WAYBILL,
        CHECK_COMPLETENESS,
        "运单为必备文件，缺失则审查不通过。",
        priority=10,
    ),
    _rule(
        DOC_RECEIPT,
        CHECK_COMPLETENESS,
        "签收单为必备文件，缺失则审查不通过。",
        priority=10,
    ),
    _rule(
        DOC_RECEIVE_VOUCHER,
        CHECK_COMPLETENESS,
        "收款水单为必备文件，缺失则审查不通过。",
        priority=10,
    ),
    _rule(
        DOC_PAY_VOUCHER,
        CHECK_COMPLETENESS,
        "付款水单为必备文件，缺失则审查不通过。",
        priority=10,
    ),
]

# ============ 基础判断（印章有无）规则 ============
_STAMP_RULES = [
    _rule(
        doc_type,
        CHECK_STAMP,
        f"{doc_type}应{req}，未检测到印章则不通过；OCR 无法识别则判无法核验。",
        tolerance={},
        priority=20,
    )
    for doc_type, req in STAMP_REQUIREMENTS.items()
    if req is not None
]

# ============ 信息准确性规则 ============
_ACCURACY_RULES = [
    _rule(
        DOC_AGENCY_AGREEMENT,
        CHECK_ACCURACY,
        "代理协议的协议方应与委托出口确认单的委托方一致；协议时间范围应覆盖合同签订时间。",
        tolerance={},
        priority=30,
    ),
    _rule(
        DOC_ENTRUST_CONFIRM,
        CHECK_ACCURACY,
        "委托出口确认单的合同号、委托方、客户、产品名称、规格、数量、金额、结算条款应与其他单据保持一致。",
        tolerance={"amount_percent": 5},
        priority=30,
    ),
    _rule(
        DOC_CUSTOMS_DECLARATION,
        CHECK_ACCURACY,
        "出口报关单的合同协议号应与委托单合同号归一化后对齐；"
        "商品名称、规格型号、数量、总价应不大于委托单对应字段（在容差内）。",
        tolerance={"amount_percent": 5, "weight_kg": 0.5},
        priority=30,
    ),
    _rule(
        DOC_WAYBILL,
        CHECK_ACCURACY,
        "运单的收货方应等于委托单的客户；数量应不大于委托单数量，且等于报关单数量（容差内）。",
        tolerance={"amount_percent": 5},
        priority=30,
    ),
    _rule(
        DOC_RECEIPT,
        CHECK_ACCURACY,
        "签收单的收货方应等于委托单的客户；签收数量应不大于委托单数量，且等于报关单数量（容差内）。",
        tolerance={"amount_percent": 5},
        priority=30,
    ),
    _rule(
        DOC_WAREHOUSE_INOUT,
        CHECK_ACCURACY,
        "出入仓单的产品名称、数量应与委托单/报关单比对；入库数量应不小于出库数量。",
        tolerance={"amount_percent": 5},
        priority=30,
    ),
    _rule(
        DOC_RECEIVE_VOUCHER,
        CHECK_ACCURACY,
        "收款水单的收款方/付款方应与代理协议/委托单对齐；收款金额应与结算条款比对；"
        "一笔合同可对应多笔收款，做总额比对。",
        tolerance={"amount_percent": 5},
        priority=30,
    ),
    _rule(
        DOC_PAY_VOUCHER,
        CHECK_ACCURACY,
        "付款水单的付款方/收款方应与代理协议/委托单对齐；付款总额应与收款总额/发票金额比对；"
        "一笔合同对应多笔付款时做总额比对。收款总额应不小于付款总额（收≤付原则）。",
        tolerance={"amount_percent": 5},
        priority=30,
    ),
]

# ============ 时间逻辑规则 ============
_TIME_RULES = [
    _rule(
        DOC_AGENCY_AGREEMENT,
        CHECK_TIME_LOGIC,
        "代理协议的协议时间范围应覆盖合同签订时间（协议开始 ≤ 合同签订 ≤ 协议结束）。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_ENTRUST_CONFIRM,
        CHECK_TIME_LOGIC,
        "委托出口确认单的签订日期应在代理协议期内，且不晚于报关/提单/签收日期。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_CUSTOMS_DECLARATION,
        CHECK_TIME_LOGIC,
        "出口报关单的出口日期/申报日期应晚于合同签订日期，且早于提单/签收日期。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_WAYBILL,
        CHECK_TIME_LOGIC,
        "运单的起运日期应不早于合同签订日期。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_RECEIPT,
        CHECK_TIME_LOGIC,
        "签收单的签收日期应不早于合同签订日期，且不早于运单起运日期。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_WAREHOUSE_INOUT,
        CHECK_TIME_LOGIC,
        "出仓日期应不早于入仓日期（入仓 ≥ 出库）。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_RECEIVE_VOUCHER,
        CHECK_TIME_LOGIC,
        "收款日期应不晚于付款日期（收 ≤ 付，允许同日 T+0）；收款日期应与结算条款约定一致。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
    _rule(
        DOC_PAY_VOUCHER,
        CHECK_TIME_LOGIC,
        "付款日期应不早于收款日期（收 ≤ 付，允许同日 T+0）。",
        tolerance={"allow_same_day": True},
        priority=40,
    ),
]

ALL_SEED_RULES: list[dict] = (
    _COMPLETENESS_RULES + _STAMP_RULES + _ACCURACY_RULES + _TIME_RULES
)


def init_seed_rules(db: Session) -> int:
    """首次启动时插入种子规则。返回插入条数（已存在则返回 0）。"""
    existing = db.execute(select(Rule)).scalars().first()
    if existing is not None:
        return 0
    count = 0
    for r in ALL_SEED_RULES:
        db.add(Rule(**r))
        count += 1
    db.commit()
    logger.info("已插入 %d 条种子规则", count)
    return count
