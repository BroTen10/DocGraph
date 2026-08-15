"""修正建议生成服务。

对每条"不通过"问题给出可操作的修正建议。优先 LLM 生成（以证据链为上下文，批次 10 Phase C），
失败/异常回退规则化模板。
"""

from __future__ import annotations

import json
import logging

from ..llm_client import LLMError, get_llm_client
from ..constants import (
    CHECK_ACCURACY,
    CHECK_COMPLETENESS,
    CHECK_STAMP,
    CHECK_TIME_LOGIC,
)
from .settings_service import get_prompt

logger = logging.getLogger(__name__)


def build_suggestion_llm(
    check_category: str,
    doc_type: str,
    issue_desc: str,
    detail: dict,
    rule_text: str = "",
    db=None,
) -> str:
    """LLM 生成修正建议（以证据链为上下文）。调用失败/低质量时回退模板。"""
    try:
        llm = get_llm_client()
        system_prompt = get_prompt(db, "suggestion.system")
        evidence = json.dumps(detail or {}, ensure_ascii=False)[:1500]
        user_prompt = f"""规则：{rule_text or '-'}
检查项：{check_category or '-'}
涉及文件类型：{doc_type or '-'}
问题：{issue_desc or '-'}
证据：{evidence}

请输出 JSON：{{"suggestion": "..."}}"""
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        suggestion = str(resp.get("suggestion") or "").strip()
        if suggestion:
            return suggestion
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning("LLM 建议生成失败，回退模板: %s", e)
    except Exception as e:
        logger.warning("LLM 建议生成异常，回退模板: %s", e)
    return build_suggestion(check_category, doc_type, issue_desc, detail)


def build_suggestion(
    check_category: str,
    doc_type: str,
    issue_desc: str,
    detail: dict,
) -> str:
    """根据检查项类型与问题描述生成修正建议。"""
    if check_category == CHECK_COMPLETENESS:
        missing = detail.get("missing_doc_type", doc_type)
        return f"缺少{missing}，请补充上传双方回签用印的{missing}后重新审查。"

    if check_category == CHECK_STAMP:
        required = detail.get("required", "用印")
        return f"未检测到{required}，请在{doc_type}上补充用印后重新上传。"

    if check_category == CHECK_ACCURACY:
        # 金额类问题
        if "pay_total" in detail and "vat_total" in detail:
            return (
                f"付款总额 ¥{detail['pay_total']:,.2f} 与增值税发票总额 "
                f"¥{detail['vat_total']:,.2f} 差额异常，请核对付款记录与发票，"
                f"补传缺失的付款水单或更正发票金额。"
            )
        if "receive_total" in detail and "pay_total" in detail:
            return "收款总额大于付款总额，违反收≤付原则，请核查收付款记录。"
        if "pay_count" in detail and detail.get("receive_count") == 0:
            return f"存在 {detail['pay_count']} 笔付款但无对应收款，请补充收款水单或核查付款依据。"
        # 字段不一致
        if "actual" in detail and "expected" in detail:
            return (
                f"{doc_type}字段值 [{detail['actual']}] 与期望值 [{detail['expected']}] 不一致，"
                "请核对原始单据并修正。"
            )
        if "receiver" in detail and "customer" in detail:
            return (
                f"收货方 [{detail['receiver']}] 与委托单客户 [{detail['customer']}] 不一致，"
                "请核对并修正收货方信息。"
            )
        if "customs_qty" in detail and "entrust_qty" in detail:
            return (
                f"报关单数量 {detail['customs_qty']} 大于委托单数量 {detail['entrust_qty']}，"
                "请核对报关数据并修正。"
            )
        return f"{doc_type}信息准确性问题：{issue_desc}，请核对原始单据并修正。"

    if check_category == CHECK_TIME_LOGIC:
        if "earliest_recv" in detail and "earliest_pay" in detail:
            return (
                f"付款日期 {detail['earliest_pay']} 早于收款日期 {detail['earliest_recv']}，"
                "违反先收后付原则，请核查收付款时间并调整。"
            )
        if "agree_start" in detail:
            return (
                f"签订日期 {detail['sign_date']} 不在协议期 "
                f"[{detail['agree_start']}, {detail['agree_end']}] 内，"
                "请核对协议有效期或补充协议。"
            )
        if "contract_date" in detail and "customs_date" in detail:
            return (
                f"报关日期 {detail['customs_date']} 早于合同签订日期 {detail['contract_date']}，"
                "请核查报关时间。"
            )
        if "customs_date" in detail and "receipt_date" in detail:
            return (
                f"签收日期 {detail['receipt_date']} 早于报关日期 {detail['customs_date']}，"
                "请核查物流签收时间。"
            )
        return f"{doc_type}时间逻辑问题：{issue_desc}，请核查相关日期。"

    return f"请核查 {doc_type} 的相关问题：{issue_desc}"
