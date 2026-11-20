# -*- coding: utf-8 -*-
"""人工修正账本（规则生命周期闭环）测试。

验证：
1. 迁移建表（llm_corrections）；
2. 人工改规则 → 自动记账（前后值、操作者、上下文）；
3. 无实际变化不记账；
4. 同一类修正被重复确认 → hit_count 累加（"这类问题又找人了一次"的度量）；
5. 回灌清单：只回放语义字段、命中高的优先、受字符预算约束；
6. 规则导入的解析提示词确实带上历史人工修正（数据化回灌闭环）。

运行：需 Postgres（TEST_DATABASE_URL 指向 *_test 库）：pytest tests/test_correction_ledger.py
"""

from __future__ import annotations

import os
import sys
import uuid

from sqlalchemy import inspect, select

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from app.database import engine  # noqa: E402
from app.models import LlmCorrection, Rule, RuleSet  # noqa: E402
from app.schemas.rule import RuleUpdate  # noqa: E402
from app.services import correction_ledger as ledger  # noqa: E402
from app.services import rule_service  # noqa: E402
from app.services.rule_import_service import _with_correction_digest  # noqa: E402


def _make_rule(db, tenant_id) -> Rule:
    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=f"修正账本测试-{uuid.uuid4().hex[:6]}",
        description="",
        is_default=False,
    )
    db.add(rule_set)
    db.flush()
    rule = Rule(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        rule_no=1,
        rule_text="发票价税合计应等于报销单报销金额。",
        doc_type="增值税发票",
        check_category="信息准确性",
        enabled=True,
        priority=100,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def test_migration_creates_table(migrated_db) -> None:
    assert "llm_corrections" in inspect(engine).get_table_names()


def test_rule_edit_records_correction(db, default_tenant_id) -> None:
    """人工在规则页把 LLM 解析出的文件类型改掉 → 账本自动记下前后值。"""
    rule = _make_rule(db, default_tenant_id)
    rule_service.update_rule(
        db, default_tenant_id, rule.id, RuleUpdate(doc_type="代理协议")
    )
    rows = db.execute(
        select(LlmCorrection).where(LlmCorrection.scope == "rule_edit")
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.field == "doc_type"
    assert row.before == "增值税发票"
    assert row.after == "代理协议"
    assert row.hit_count == 1
    assert row.target_id == rule.id
    assert row.rule_set_id == rule.rule_set_id
    assert row.context and "价税合计" in row.context


def test_noop_edit_records_nothing(db, default_tenant_id) -> None:
    """把值改成原值（前端提交整表）不应产生修正记录。"""
    rule = _make_rule(db, default_tenant_id)
    rule_service.update_rule(
        db, default_tenant_id, rule.id, RuleUpdate(doc_type="增值税发票")
    )
    assert db.execute(select(LlmCorrection)).scalars().all() == []


def test_same_correction_accumulates_hits(db, default_tenant_id) -> None:
    """同一类修正再次发生 → hit_count 累加，而不是新增一条。"""
    rule = _make_rule(db, default_tenant_id)
    rule_service.update_rule(
        db, default_tenant_id, rule.id, RuleUpdate(check_category="基础判断")
    )
    rule_service.update_rule(
        db, default_tenant_id, rule.id, RuleUpdate(check_category="信息准确性")
    )
    rule_service.update_rule(
        db, default_tenant_id, rule.id, RuleUpdate(check_category="基础判断")
    )
    rows = db.execute(
        select(LlmCorrection).where(
            LlmCorrection.field == "check_category",
            LlmCorrection.after == "基础判断",
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].hit_count == 2


def test_digest_filters_fields_and_orders_by_hits(db, default_tenant_id) -> None:
    ledger.record_correction(
        db, default_tenant_id, scope="rule_edit", field="doc_type",
        before="采购合同", after="采购协议",
    )
    for _ in range(3):
        ledger.record_correction(
            db, default_tenant_id, scope="rule_edit", field="check_category",
            before="信息准确性", after="基础判断",
        )
    # 非回放字段（enabled 这类状态位）不应出现在提示词清单里
    ledger.record_correction(
        db, default_tenant_id, scope="rule_edit", field="enabled",
        before="true", after="false",
    )
    db.commit()

    digest = ledger.corrections_digest(db, default_tenant_id)
    lines = digest.splitlines()
    assert lines, "应生成回灌清单"
    assert lines[0].startswith("- 检查项"), digest
    assert "3 次" in lines[0]
    assert "文件类型" in digest and "采购协议" in digest
    assert "enabled" not in digest and "false" not in digest


def test_digest_respects_budget(db, default_tenant_id) -> None:
    for i in range(30):
        ledger.record_correction(
            db, default_tenant_id, scope="rule_edit", field="doc_type",
            before=f"旧类型{i}", after=f"新类型{i}",
        )
    db.commit()
    digest = ledger.corrections_digest(db, default_tenant_id, limit=5, max_chars=100)
    assert len(digest) <= 100
    assert len(digest.splitlines()) <= 5


def test_import_prompt_carries_corrections(db, default_tenant_id) -> None:
    """闭环关键一步：新规则导入时，解析提示词带上历史修正（模型据此不再犯）。"""
    ledger.record_correction(
        db, default_tenant_id, scope="rule_edit", field="doc_type",
        before="采购合同", after="采购协议",
    )
    db.commit()

    prompt = _with_correction_digest(
        "系统提示词", db, default_tenant_id, uuid.uuid4()
    )
    assert prompt.startswith("系统提示词")
    assert "本租户历史人工修正" in prompt
    assert "采购协议" in prompt


def test_import_prompt_unchanged_without_corrections(db, default_tenant_id) -> None:
    prompt = _with_correction_digest(
        "系统提示词", db, default_tenant_id, uuid.uuid4()
    )
    assert prompt == "系统提示词"
