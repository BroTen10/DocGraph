# -*- coding: utf-8 -*-
"""场景知识包（scenario pack）机制测试。

覆盖：
1. 启动时内置「通用契约包 + 场景包」都被 seed；
2. 场景解析优先级：显式参数 > 规则集默认；
3. 场景选择只加载匹配的场景包，通用契约包始终生效；
4. 关闭 use_default_skill 后内置包全部不生效。
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from conftest import authenticate
from app.models import RuleParseSkill, RuleSet
from app.services.rule_import_service import import_rules_with_skills
from app.services.rule_parse_engine import compile_directive, resolve_scenario

# 通用契约包与贸易场景包中各取一条可识别的指令
_GENERIC_MARK = "原子规则以一个可独立得出"
_TRADE_MARK = "文件类型列和原文明确涉及的实际单据"


def _make_rule_set(db, tenant_id, **kwargs) -> RuleSet:
    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=f"场景包-{uuid.uuid4().hex[:8]}",
        next_rule_no=1,
        **kwargs,
    )
    db.add(rule_set)
    db.commit()
    return rule_set


def _instructions(directive) -> str:
    return "\n".join(directive.prompt_additions)


def test_builtin_packs_are_seeded_with_scenarios(migrated_db) -> None:
    rows = migrated_db.execute(
        select(RuleParseSkill).where(RuleParseSkill.is_builtin.is_(True))
    ).scalars().all()

    scenarios = {row.scenario for row in rows}
    assert None in scenarios, "必须存在通用契约包（scenario 为 NULL）"
    assert "trade" in scenarios, "必须存在 trade 场景包"


def test_trade_scenario_loads_generic_and_trade_packs(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")

    directive = compile_directive(migrated_db, admin_auth.tenant_id, rule_set.id)
    text = _instructions(directive)

    assert _GENERIC_MARK in text
    assert _TRADE_MARK in text


def test_other_scenario_does_not_load_trade_pack(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="expense")

    directive = compile_directive(migrated_db, admin_auth.tenant_id, rule_set.id)
    text = _instructions(directive)

    assert _GENERIC_MARK in text
    assert _TRADE_MARK not in text, "非贸易场景不应注入贸易领域知识"


def test_explicit_scenario_overrides_rule_set_default(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="expense")

    directive = compile_directive(
        migrated_db, admin_auth.tenant_id, rule_set.id, scenario="trade"
    )

    assert _TRADE_MARK in _instructions(directive)


def test_undeclared_scenario_falls_back_to_trade(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)

    assert resolve_scenario(rule_set) == "trade"
    assert resolve_scenario(rule_set, "supplier") == "supplier"
    assert _TRADE_MARK in _instructions(
        compile_directive(migrated_db, admin_auth.tenant_id, rule_set.id)
    )


def test_use_default_skill_false_disables_builtin_packs(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(
        migrated_db,
        admin_auth.tenant_id,
        scenario="trade",
        use_default_skill=False,
    )

    directive = compile_directive(migrated_db, admin_auth.tenant_id, rule_set.id)

    assert directive.prompt_additions == []
    assert directive.domain_context == {}


def test_rule_set_skill_still_overrides_builtin(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    migrated_db.add(
        RuleParseSkill(
            tenant_id=admin_auth.tenant_id,
            rule_set_id=rule_set.id,
            name="客户纠偏",
            is_builtin=False,
            enabled=True,
            priority=100,
            content={
                "prompt_instructions": ["本规则集的金额一律以合同币别为准。"],
                "defaults": {"tolerance": {"amount_percent": 2}},
            },
        )
    )
    migrated_db.commit()

    directive = compile_directive(migrated_db, admin_auth.tenant_id, rule_set.id)

    assert "本规则集的金额一律以合同币别为准。" in _instructions(directive)
    assert directive.defaults["tolerance"]["amount_percent"] == 2
    # 通用契约包的其它容差默认值仍在（深合并而非整体替换）
    assert directive.defaults["tolerance"]["weight_kg"] == 0


_EMPTY_RESULT = {"total": 0, "imported": 0, "skipped": 0, "rules": [], "errors": []}


def test_unknown_scenario_emits_import_warning(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(
        migrated_db, admin_auth.tenant_id, scenario="not-registered"
    )

    with patch(
        "app.services.rule_import_service.import_rules_from_text",
        return_value=dict(_EMPTY_RESULT),
    ):
        result = import_rules_with_skills(
            migrated_db,
            admin_auth.tenant_id,
            rule_set.id,
            "报销单金额应与发票金额一致",
        )

    assert result["scenario"] == "not-registered"
    assert any("未登记场景知识包" in item for item in result["import_warnings"])


def test_registered_scenario_has_no_warning(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="expense")

    with patch(
        "app.services.rule_import_service.import_rules_from_text",
        return_value=dict(_EMPTY_RESULT),
    ):
        result = import_rules_with_skills(
            migrated_db,
            admin_auth.tenant_id,
            rule_set.id,
            "报销单金额应与发票金额一致",
        )

    assert result["scenario"] == "expense"
    assert not result.get("import_warnings")


# ============ 场景清单 API 与规则集场景字段（前端下拉依赖）============


def test_scenarios_endpoint_lists_builtin_packs(client, admin_auth) -> None:
    headers = authenticate(client, admin_auth)
    resp = client.get("/api/rule-sets/scenarios", headers=headers)

    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()}
    assert {"trade", "expense"}.issubset(keys)
    # 通用契约包（scenario 为 null）不作为"可选择的业务场景"
    assert None not in keys


def test_rule_set_scenario_round_trip(client, admin_auth) -> None:
    headers = authenticate(client, admin_auth)
    created = client.post(
        "/api/rule-sets",
        headers=headers,
        json={"name": f"场景回环-{uuid.uuid4().hex[:8]}", "scenario": "expense"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scenario"] == "expense"

    updated = client.put(
        f"/api/rule-sets/{body['id']}",
        headers=headers,
        json={"scenario": "supplier"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["scenario"] == "supplier"

    cleared = client.put(
        f"/api/rule-sets/{body['id']}",
        headers=headers,
        json={"scenario": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["scenario"] is None


# ============ 导入弹窗：出厂场景 / 自定义场景两条互斥通道 ============

_EXPENSE_MARK = "费用报销领域按"
_CUSTOM_INSTRUCTION = "本规则集的金额一律以合同币别为准。"


def _import_capture_directive(db, tenant_id, rule_set_id: uuid.UUID, **kwargs):
    """调用 import_rules_with_skills 并截获真正送给 LLM 的 directive。"""
    captured: dict = {}

    def _fake(db_, tenant_id_, rule_set_id_, raw_text, **rest):
        captured["directive"] = rest.get("directive")
        return dict(_EMPTY_RESULT)

    with patch(
        "app.services.rule_import_service.import_rules_from_text",
        side_effect=_fake,
    ):
        result = import_rules_with_skills(
            db, tenant_id, rule_set_id, "报销单金额应与发票金额一致", **kwargs
        )
    return result, captured["directive"]


def _add_custom_skill(db, tenant_id, rule_set_id, *, priority=100, enabled=True) -> RuleParseSkill:
    skill = RuleParseSkill(
        tenant_id=tenant_id,
        rule_set_id=rule_set_id,
        name=f"客户自定义场景-{uuid.uuid4().hex[:6]}",
        is_builtin=False,
        enabled=enabled,
        priority=priority,
        content={"prompt_instructions": [_CUSTOM_INSTRUCTION]},
    )
    db.add(skill)
    db.commit()
    return skill


def _builtin_by_scenario(db, scenario) -> RuleParseSkill:
    stmt = select(RuleParseSkill).where(
        RuleParseSkill.is_builtin.is_(True),
        RuleParseSkill.tenant_id.is_(None),
    )
    if scenario is None:
        stmt = stmt.where(RuleParseSkill.scenario.is_(None))
    else:
        stmt = stmt.where(RuleParseSkill.scenario == scenario)
    return db.execute(stmt).scalars().one()


# -------- 通道 A：出厂场景（不勾 Skill）--------


def test_scenario_channel_keeps_generic_scenario_and_custom(
    migrated_db, admin_auth
) -> None:
    """不勾 Skill：通用契约 + 场景包 + 规则集自定义 Skill 全部生效。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    _add_custom_skill(migrated_db, admin_auth.tenant_id, rule_set.id)

    result, directive = _import_capture_directive(
        migrated_db, admin_auth.tenant_id, rule_set.id
    )
    text = _instructions(directive)

    assert result["scenario"] == "trade"
    assert _GENERIC_MARK in text
    assert _TRADE_MARK in text
    assert _CUSTOM_INSTRUCTION in text


# -------- 通道 B：自定义场景（勾了 Skill）--------


def test_skill_ids_switch_to_custom_channel_and_drop_scenario_pack(
    migrated_db, admin_auth
) -> None:
    """勾了 Skill：只保留系统预设（通用契约）+ 勾选的 Skill，不加载场景包。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    custom = _add_custom_skill(migrated_db, admin_auth.tenant_id, rule_set.id)

    result, directive = _import_capture_directive(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        skill_ids=[custom.id],
    )
    text = _instructions(directive)

    assert _GENERIC_MARK in text, "系统预设（通用解析契约）必须始终生效"
    assert _TRADE_MARK not in text, "自定义场景不应被出厂场景包污染"
    assert _CUSTOM_INSTRUCTION in text, "勾选的 Skill 必须生效"
    # 自定义场景通道没有加载场景包，不再回显场景
    assert result["scenario"] is None
    assert result["applied_skills"] == ["通用规则解析契约", custom.name]


def test_skill_ids_ignore_scenario_param_and_file_declaration(
    migrated_db, admin_auth
) -> None:
    """自定义场景通道下，导入参数声明的场景不再参与装配。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    custom = _add_custom_skill(migrated_db, admin_auth.tenant_id, rule_set.id)

    result, directive = _import_capture_directive(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        skill_ids=[custom.id],
        scenario="expense",
    )
    text = _instructions(directive)

    assert result["scenario"] is None
    assert _EXPENSE_MARK not in text
    assert _TRADE_MARK not in text
    assert _CUSTOM_INSTRUCTION in text


def test_skill_ids_do_not_auto_apply_unselected_custom_skills(
    migrated_db, admin_auth
) -> None:
    """自定义场景 = 完全由用户勾选决定：没勾的自定义 Skill 不生效。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    selected = _add_custom_skill(migrated_db, admin_auth.tenant_id, rule_set.id)
    unselected = _add_custom_skill(migrated_db, admin_auth.tenant_id, rule_set.id)
    unselected.content = {"prompt_instructions": ["未被勾选的指令不应出现。"]}
    migrated_db.commit()

    _, directive = _import_capture_directive(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        skill_ids=[selected.id],
    )
    text = _instructions(directive)

    assert _CUSTOM_INSTRUCTION in text
    assert "未被勾选的指令不应出现。" not in text


def test_explicit_scenario_pack_as_skill_still_works(migrated_db, admin_auth) -> None:
    """兼容：API 直接点名某个场景包时，它作为被勾选的 Skill 生效（但仍不带其它场景包）。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    expense_pack = _builtin_by_scenario(migrated_db, "expense")

    _, directive = _import_capture_directive(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        skill_ids=[expense_pack.id],
    )
    text = _instructions(directive)

    assert _GENERIC_MARK in text
    assert _EXPENSE_MARK in text
    assert _TRADE_MARK not in text


def test_skill_ids_already_in_preset_is_not_double_merged(
    migrated_db, admin_auth
) -> None:
    """勾选系统预设本身不报错、不重复合并。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    generic_pack = _builtin_by_scenario(migrated_db, None)

    result, directive = _import_capture_directive(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        skill_ids=[generic_pack.id],
    )
    additions = directive.prompt_additions
    # 列表合并本身按内容去重，这里兜住"未重复追加"
    assert len(additions) == len(set(additions))
    assert result["applied_skills"] == ["通用规则解析契约"]


def test_skill_ids_unknown_skill_still_errors(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")

    with pytest.raises(ValueError, match="指定的 Skill 不存在"):
        import_rules_with_skills(
            migrated_db,
            admin_auth.tenant_id,
            rule_set.id,
            "报销单金额应与发票金额一致",
            skill_ids=[uuid.uuid4()],
        )


def test_skill_ids_disabled_skill_still_errors(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")
    disabled = _add_custom_skill(
        migrated_db, admin_auth.tenant_id, rule_set.id, enabled=False
    )

    with pytest.raises(ValueError, match="已停用"):
        import_rules_with_skills(
            migrated_db,
            admin_auth.tenant_id,
            rule_set.id,
            "报销单金额应与发票金额一致",
            skill_ids=[disabled.id],
        )


def test_unknown_scenario_warning_not_raised_in_custom_channel(
    migrated_db, admin_auth
) -> None:
    """自定义场景通道不解析场景，因此不该报"场景未登记"。"""
    rule_set = _make_rule_set(
        migrated_db, admin_auth.tenant_id, scenario="not-registered"
    )
    custom = _add_custom_skill(migrated_db, admin_auth.tenant_id, rule_set.id)

    result, _ = _import_capture_directive(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        skill_ids=[custom.id],
    )

    assert result["scenario"] is None
    assert not any("未登记场景知识包" in item for item in (result.get("import_warnings") or []))


def test_unknown_scenario_warning_still_raised_in_scenario_channel(
    migrated_db, admin_auth
) -> None:
    rule_set = _make_rule_set(
        migrated_db, admin_auth.tenant_id, scenario="not-registered"
    )

    result, _ = _import_capture_directive(
        migrated_db, admin_auth.tenant_id, rule_set.id
    )

    assert result["scenario"] == "not-registered"
    assert any("未登记场景知识包" in item for item in result["import_warnings"])


# ============ 内置包优先级必须与 YAML 一致（合并顺序回归）============


def test_builtin_pack_priority_matches_yaml(migrated_db) -> None:
    """回归：启动同步曾漏写 priority，导致通用契约排在场景包之后合并。"""
    from app.default_rule_parse_skill import load_builtin_skill_packs

    for pack in load_builtin_skill_packs():
        row = _builtin_by_scenario(migrated_db, pack.scenario)
        assert row.priority == pack.priority, (
            f"{pack.name} 的 DB priority={row.priority} 与 YAML={pack.priority} 不一致"
        )


def test_generic_contract_merges_before_scenario_pack(migrated_db, admin_auth) -> None:
    """通用契约是总纲，必须先于场景包合并（场景包在同名键上覆盖通用契约）。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="trade")

    directive = compile_directive(migrated_db, admin_auth.tenant_id, rule_set.id)
    additions = directive.prompt_additions

    first_generic = next(i for i, a in enumerate(additions) if _GENERIC_MARK in a)
    first_trade = next(i for i, a in enumerate(additions) if _TRADE_MARK in a)

    assert first_generic < first_trade
