# -*- coding: utf-8 -*-
"""人工纠正判定 → 回灌判定提示词（规则生命周期闭环，判定侧）。

验证：
1. 人工改判（PATCH 判定的 result）落库并留痕（detail.human_override、source=human）；
2. 改判写进人工修正账本（scope=review_judge）；
3. 同一类改判重复发生 → hit_count 累加；
4. 改判清掉"需人工确认"标记（人工已给结论）；
5. 同名结论（无变化）不记账；
6. 非法结论被拒；
7. 判定系统提示词确实带上人工纠正清单（数据化回灌闭环）。

运行：需 Postgres（TEST_DATABASE_URL 指向 *_test 库）：pytest tests/test_judge_correction_feedback.py
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest
from sqlalchemy import select

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from app.models import Contract, LlmCorrection, ReviewResult, ReviewTask  # noqa: E402
from app.services import correction_ledger as ledger  # noqa: E402
from app.services import review_service  # noqa: E402
from app.services import review_judge  # noqa: E402


def _make_result(db, tenant_id, result: str = "fail") -> ReviewResult:
    contract = Contract(
        tenant_id=tenant_id,
        rule_set_id=None,
        contract_no=f"T-{uuid.uuid4().hex[:6]}",
        status="uploaded",
    )
    # rule_set_id 非空约束：借一个已存在的规则集（测试库每个用例都是干净 schema）
    from app.models import RuleSet

    rule_set = RuleSet(tenant_id=tenant_id, name=f"判定纠正-{uuid.uuid4().hex[:6]}")
    db.add(rule_set)
    db.flush()
    contract.rule_set_id = rule_set.id
    db.add(contract)
    db.flush()
    task = ReviewTask(
        tenant_id=tenant_id,
        contract_id=contract.id,
        status="completed",
        progress=100,
        summary={},
    )
    db.add(task)
    db.flush()
    row = ReviewResult(
        tenant_id=tenant_id,
        task_id=task.id,
        rule_text="发票价税合计应等于报销单报销金额。",
        doc_type="增值税发票",
        check_category="信息准确性",
        result=result,
        status="open",
        status_history=[],
        source="judge",
        issue_desc="不符合规则要求（依据：金额不一致）",
        detail={"manual_review_required": True, "manual_review_reason": "判定未通过验证门"},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_human_verdict_override_records_and_sediments(db, default_tenant_id) -> None:
    row = _make_result(db, default_tenant_id)

    updated = review_service.update_result_status(
        db,
        default_tenant_id,
        row.id,
        None,
        note="人工复核：两值在容差内，应为通过",
        verdict="pass",
        by_user_id=None,
        by_display_name="审查员",
    )

    assert updated.result == "pass"
    assert updated.source == "human"
    assert updated.severity is None and updated.deviation is None
    detail = updated.detail or {}
    assert detail["human_override"]["from"] == "fail"
    assert detail["human_override"]["to"] == "pass"
    assert "manual_review_required" not in detail

    rows = db.execute(
        select(LlmCorrection).where(LlmCorrection.scope == "review_judge")
    ).scalars().all()
    assert len(rows) == 1
    assert (rows[0].field, rows[0].before, rows[0].after) == ("result", "fail", "pass")
    assert rows[0].doc_type == "增值税发票"
    assert rows[0].context and "价税合计" in rows[0].context


def test_same_verdict_correction_accumulates(db, default_tenant_id) -> None:
    first = _make_result(db, default_tenant_id)
    second = _make_result(db, default_tenant_id)
    for row in (first, second):
        review_service.update_result_status(
            db, default_tenant_id, row.id, None, verdict="pass"
        )
    rows = db.execute(
        select(LlmCorrection).where(
            LlmCorrection.scope == "review_judge", LlmCorrection.after == "pass"
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].hit_count == 2


def test_noop_verdict_records_nothing(db, default_tenant_id) -> None:
    row = _make_result(db, default_tenant_id, result="pass")
    updated = review_service.update_result_status(
        db, default_tenant_id, row.id, None, verdict="pass"
    )
    assert updated.result == "pass"
    assert db.execute(select(LlmCorrection)).scalars().all() == []


def test_illegal_verdict_rejected(db, default_tenant_id) -> None:
    row = _make_result(db, default_tenant_id)
    with pytest.raises(ValueError):
        review_service.update_result_status(
            db, default_tenant_id, row.id, None, verdict="maybe"
        )


def test_status_only_still_works(db, default_tenant_id) -> None:
    """向后兼容：只做状态流转（不纠正结论）时行为不变。"""
    row = _make_result(db, default_tenant_id)
    updated = review_service.update_result_status(
        db, default_tenant_id, row.id, "confirmed", note="已确认"
    )
    assert updated.status == "confirmed"
    assert updated.result == "fail"
    assert updated.status_history[-1]["note"] == "已确认"
    assert db.execute(select(LlmCorrection)).scalars().all() == []


def test_judge_prompt_carries_human_corrections(db, default_tenant_id) -> None:
    row = _make_result(db, default_tenant_id)
    review_service.update_result_status(
        db, default_tenant_id, row.id, None, verdict="pass", note="容差内"
    )

    prompt = review_judge.judge_system_prompt(db, default_tenant_id, "基础判定提示词")
    assert prompt.startswith("基础判定提示词")
    assert "人工纠正过的判定" in prompt
    assert "不通过" in prompt and "通过" in prompt
    assert "价税合计" in prompt


def test_judge_prompt_unchanged_without_corrections(db, default_tenant_id) -> None:
    prompt = review_judge.judge_system_prompt(db, default_tenant_id, "基础判定提示词")
    assert prompt == "基础判定提示词"
