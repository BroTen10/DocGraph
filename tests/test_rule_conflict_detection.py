# -*- coding: utf-8 -*-
"""规则冲突检测：分组口径与"检测失败不得误清除"回归测试。

覆盖：
1. 分组按适用范围（业务对象），不再受 check_category 影响；
2. 未限定适用范围的规则单独成组，单组超限截断并告警；
3. 提示词包含适用范围/条件/断言/容差/例外；
4. 同一规则对在多个分组被检出时按 (规则对, 类型) 去重；
5. **LLM 调用失败时不清除既有冲突、不自动启用规则**；
6. 检测成功且无冲突时才清除冲突并恢复规则状态。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.llm_client import LLMError
from app.models import Rule, RuleSet
from app.services import rule_conflict_detector
from app.services.rule_conflict_detector import (
    _MAX_GROUP_RULES,
    _dedupe_conflicts,
    _group_rules,
    _rule_doc_types,
    detect_all_conflicts,
    render_rule_line,
    run_conflict_detection,
)
from app.services import rule_defects

_CONFLICT_DEFECT = {
    "type": "logical_contradiction",
    "severity": "error",
    "description": "涉及规则 R0001、R0002：金额应不大于与应不小于矛盾",
}


def _fake_rule(
    rule_no: int = 1,
    rule_text: str = "规则",
    scope=None,
    doc_type=None,
    check_category=None,
    structure=None,
    tolerance=None,
    priority: int = 100,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        rule_no=rule_no,
        rule_code=f"R{rule_no:04d}",
        rule_text=rule_text,
        scope=scope,
        doc_type=doc_type,
        check_category=check_category,
        structure=structure,
        tolerance=tolerance or {},
        priority=priority,
    )


# ============ 分组口径（纯函数）============


def test_groups_by_scope_doc_types_not_check_category() -> None:
    rules = [
        _fake_rule(1, "发票A", scope={"doc_types": ["发票"]}, check_category="信息准确性"),
        _fake_rule(2, "发票B", scope={"doc_types": ["发票"]}, check_category="时间逻辑"),
        _fake_rule(3, "报销单C", scope={"doc_types": ["报销单"]}),
    ]

    groups, warnings, truncated = _group_rules(rules)
    by_key = {key: group for key, group in groups}

    # 同一适用对象下的规则必须同组，即使检查类别不同
    assert len(by_key["适用范围：发票"]) == 2
    # 单成员分组也会返回：无需调用 LLM，但视为已完整检测（可回收历史冲突）
    assert len(by_key["适用范围：报销单"]) == 1
    assert warnings == [] and truncated == set()


def test_rule_with_multiple_doc_types_enters_each_group() -> None:
    rules = [
        _fake_rule(1, "跨单据规则", scope={"doc_types": ["发票", "报销单"]}),
        _fake_rule(2, "发票规则", scope={"doc_types": ["发票"]}),
        _fake_rule(3, "报销单规则", scope={"doc_types": ["报销单"]}),
    ]

    groups, _warnings, _truncated = _group_rules(rules)
    keys = [key for key, _ in groups]

    assert "适用范围：发票" in keys and "适用范围：报销单" in keys


def test_all_scope_and_missing_scope_are_unbound_group() -> None:
    rules = [
        _fake_rule(1, "整批规则", scope={"doc_types": "ALL"}),
        _fake_rule(2, "无范围规则", scope=None),
        _fake_rule(3, "按标签规则", doc_type="发票"),
    ]

    groups, _warnings, _truncated = _group_rules(rules)
    by_key = {key: group for key, group in groups}

    # 整批/ALL 与未限定范围归入同一组；有标签的规则进入各自对象分组
    assert len(by_key["适用范围：未限定（整批/全部）"]) == 2
    assert [rule.rule_no for rule in by_key["适用范围：发票"]] == [3]
    assert _rule_doc_types(rules[0]) == ()
    assert _rule_doc_types(rules[2]) == ("发票",)


def test_oversized_group_is_truncated_with_warning() -> None:
    rules = [
        _fake_rule(i + 1, f"发票规则{i}", scope={"doc_types": ["发票"]})
        for i in range(_MAX_GROUP_RULES + 5)
    ]

    groups, warnings, truncated = _group_rules(rules)

    assert len(groups) == 1
    assert len(groups[0][1]) == _MAX_GROUP_RULES
    assert any("超过单组上限" in item for item in warnings)
    # 被截断的规则不得进入"可回收历史冲突"的集合
    assert len(truncated) == 5


def test_render_rule_line_includes_scope_condition_assertion() -> None:
    rule = _fake_rule(
        1,
        "发票开票日期应不晚于报销单提交日期",
        scope={"doc_types": ["发票", "报销单"]},
        structure={
            "condition": {"text": "境内差旅", "field": "费用类型", "operator": "等于", "value": "差旅"},
            "assertion": {
                "source": {"doc_type": "发票", "field": "开票日期"},
                "operator": "时间不晚于",
                "target": {"doc_type": "报销单", "field": "提交日期"},
            },
            "exceptions": [{"text": "电子发票除外"}],
        },
        tolerance={"time_days": 0, "allow_same_day": True},
    )

    line = render_rule_line(0, rule)

    assert "适用=发票、报销单" in line
    assert "条件=境内差旅（费用类型 等于 差旅）" in line
    assert "断言=发票.开票日期 时间不晚于 报销单.提交日期" in line
    assert "容差=时间容差0天，允许同日=是" in line
    assert "例外=电子发票除外" in line


def test_duplicate_conflicts_across_groups_are_deduped() -> None:
    conflicts = [
        {"rule_ids": ["a", "b"], "type": "logical_contradiction", "description": "x"},
        {"rule_ids": ["b", "a"], "type": "logical_contradiction", "description": "x"},
        {"rule_ids": ["a", "b"], "type": "redundant", "description": "y"},
        {"rule_ids": ["a"], "type": "redundant", "description": "z"},
    ]

    unique = _dedupe_conflicts(conflicts)

    assert len(unique) == 2
    assert {item["type"] for item in unique} == {"logical_contradiction", "redundant"}


# ============ 提示词（桩 LLM）============


class _CapturingLLM:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response if response is not None else {"conflicts": []}
        self.error = error
        self.user_prompt = ""

    def chat_json(self, messages, **_kwargs):
        self.user_prompt = messages[-1]["content"]
        if self.error is not None:
            raise self.error
        return self.response


# ============ 端到端（数据库）============


@pytest.fixture
def conflicting_rules(migrated_db, admin_auth):
    """两条处于「冲突 → 待确认 + 禁用」状态的规则。"""
    rule_set = RuleSet(
        tenant_id=admin_auth.tenant_id,
        name=f"冲突检测-{uuid.uuid4().hex[:8]}",
        next_rule_no=3,
    )
    migrated_db.add(rule_set)
    migrated_db.flush()

    rules = []
    for rule_no, text in ((1, "发票金额应不大于报销单金额"), (2, "发票金额应不小于报销单金额")):
        rule = Rule(
            tenant_id=admin_auth.tenant_id,
            rule_set_id=rule_set.id,
            rule_no=rule_no,
            rule_text=text,
            scope={"doc_types": ["发票"], "intents": ["信息准确性"]},
            status="pending",
            enabled=False,
            defects=[dict(_CONFLICT_DEFECT)],
        )
        migrated_db.add(rule)
        rules.append(rule)
    migrated_db.commit()
    return rule_set, rules


def _reload(db, rules):
    for rule in rules:
        db.refresh(rule)
    return rules


def test_llm_failure_keeps_conflicts_and_rule_state(
    migrated_db,
    admin_auth,
    conflicting_rules,
) -> None:
    rule_set, rules = conflicting_rules
    llm = _CapturingLLM(error=LLMError("upstream 503"))

    with patch.object(rule_conflict_detector, "get_llm_client", return_value=llm):
        result = run_conflict_detection(migrated_db, admin_auth.tenant_id, str(rule_set.id))

    _reload(migrated_db, rules)
    assert result["detection_incomplete"] is True
    assert result["failed_groups"] == 1
    assert result["cleared_rules"] == 0
    assert result["total_conflicts"] == 0
    assert any("未完成" in item for item in result["warnings"])
    # 关键：既有冲突与"待确认 + 禁用"状态必须原样保留
    for rule in rules:
        assert rule.status == "pending"
        assert rule.enabled is False
        assert any(rule_defects.is_conflict_defect(d) for d in rule.defects)
        assert rule.defects[0].get("resolution") is None


def test_success_without_conflicts_clears_and_enables(
    migrated_db,
    admin_auth,
    conflicting_rules,
) -> None:
    rule_set, rules = conflicting_rules
    llm = _CapturingLLM(response={"conflicts": []})

    with patch.object(rule_conflict_detector, "get_llm_client", return_value=llm):
        result = run_conflict_detection(migrated_db, admin_auth.tenant_id, str(rule_set.id))

    _reload(migrated_db, rules)
    assert result["detection_incomplete"] is False
    assert result["cleared_rules"] == 2
    for rule in rules:
        assert not any(rule_defects.is_conflict_defect(d) for d in rule.defects)
        assert rule.status == "confirmed"
        assert rule.enabled is True
    # 提示词必须带上分组范围与结构化断言
    assert "适用范围分组：适用范围：发票" in llm.user_prompt
    assert "R0001" in llm.user_prompt


def test_partial_failure_does_not_touch_failed_group(
    migrated_db,
    admin_auth,
) -> None:
    """一组成功、一组失败：失败组的规则状态保持不变。"""
    rule_set = RuleSet(
        tenant_id=admin_auth.tenant_id,
        name=f"冲突检测-部分失败-{uuid.uuid4().hex[:8]}",
        next_rule_no=5,
    )
    migrated_db.add(rule_set)
    migrated_db.flush()

    rules = []
    for rule_no, text, doc_type in (
        (1, "发票金额应不大于报销单金额", "发票"),
        (2, "发票金额应不小于报销单金额", "发票"),
        (3, "派车单日期应早于签收日期", "派车单"),
        (4, "派车单日期应晚于签收日期", "派车单"),
    ):
        rule = Rule(
            tenant_id=admin_auth.tenant_id,
            rule_set_id=rule_set.id,
            rule_no=rule_no,
            rule_text=text,
            scope={"doc_types": [doc_type]},
            status="pending",
            enabled=False,
            defects=[dict(_CONFLICT_DEFECT)],
        )
        migrated_db.add(rule)
        rules.append(rule)
    migrated_db.commit()

    calls: list[str] = []

    class _PartlyFailingLLM:
        def chat_json(self, messages, **_kwargs):
            prompt = messages[-1]["content"]
            if "派车单" in prompt:
                calls.append("fail")
                raise LLMError("timeout")
            calls.append("ok")
            return {"conflicts": []}

    with patch.object(
        rule_conflict_detector, "get_llm_client", return_value=_PartlyFailingLLM()
    ):
        result = run_conflict_detection(migrated_db, admin_auth.tenant_id, str(rule_set.id))

    _reload(migrated_db, rules)
    assert result["total_groups"] == 2
    assert result["failed_groups"] == 1
    assert result["detection_incomplete"] is True

    invoice_rules, dispatch_rules = rules[:2], rules[2:]
    for rule in invoice_rules:  # 成功分组：冲突清除并恢复启用
        assert rule.status == "confirmed" and rule.enabled is True
        assert not any(rule_defects.is_conflict_defect(d) for d in rule.defects)
    for rule in dispatch_rules:  # 失败分组：原样保留
        assert rule.status == "pending" and rule.enabled is False
        assert any(rule_defects.is_conflict_defect(d) for d in rule.defects)


def test_detect_all_conflicts_reports_grouping(
    migrated_db,
    admin_auth,
    conflicting_rules,
) -> None:
    rule_set, _rules = conflicting_rules
    llm = _CapturingLLM(response={"conflicts": []})

    with patch.object(rule_conflict_detector, "get_llm_client", return_value=llm):
        detection = detect_all_conflicts(migrated_db, admin_auth.tenant_id, str(rule_set.id))

    assert detection.complete is True
    assert detection.total_groups == 1
    assert detection.groups[0].key == "适用范围：发票"
    assert detection.groups[0].rule_count == 2


def test_lone_rule_with_stale_conflict_is_recycled(
    migrated_db,
    admin_auth,
) -> None:
    """对手规则被删除后，孤立规则的历史冲突应能被回收（不能永远卡在冲突页签）。"""
    rule_set = RuleSet(
        tenant_id=admin_auth.tenant_id,
        name=f"冲突回收-{uuid.uuid4().hex[:8]}",
        next_rule_no=2,
    )
    migrated_db.add(rule_set)
    migrated_db.flush()
    rule = Rule(
        tenant_id=admin_auth.tenant_id,
        rule_set_id=rule_set.id,
        rule_no=1,
        rule_text="发票金额应不大于报销单金额",
        scope={"doc_types": ["发票"]},
        status="pending",
        enabled=False,
        defects=[dict(_CONFLICT_DEFECT)],
    )
    migrated_db.add(rule)
    migrated_db.commit()

    llm = _CapturingLLM(response={"conflicts": []})
    with patch.object(rule_conflict_detector, "get_llm_client", return_value=llm):
        result = run_conflict_detection(migrated_db, admin_auth.tenant_id, str(rule_set.id))

    migrated_db.refresh(rule)
    assert result["detection_incomplete"] is False
    assert result["cleared_rules"] == 1
    assert not any(rule_defects.is_conflict_defect(d) for d in rule.defects)
    assert rule.status == "confirmed" and rule.enabled is True
    assert llm.user_prompt == "", "单成员分组不应调用 LLM"
