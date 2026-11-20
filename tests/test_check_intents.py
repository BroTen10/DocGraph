# -*- coding: utf-8 -*-
"""检查项注册表（CheckIntent）机制测试。

覆盖：
1. 内置四类检查项启动即 seed；
2. 导入发现的新检查项登记为 pending_review，并回传 new_check_categories；
3. 未确认的检查项不回灌提示词（避免自造名称被自我强化）；
4. 别名写时归一 / 改名改写存量规则 / 合并保留别名 / 被引用时拒绝删除；
5. 未登记字段在导入期给出 info 缺陷与导入告警；
6. 规则写入业务场景标签 scenario。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from conftest import authenticate

from app import constants as C
from app.models import CheckIntent, Rule, RuleSet
from app.services import check_intent_service
from app.services.rule_import_service import _known_check_categories, import_rules_from_text


@pytest.fixture
def seeded_tenant(migrated_db, admin_auth):
    """为测试租户补齐内置检查项。

    生产环境租户经审批流程创建时会自动 seed（platform_service），
    测试里的租户是直接建表插入的，因此这里显式 seed 一次。
    """
    check_intent_service.seed_builtin_intents(migrated_db, admin_auth.tenant_id)
    migrated_db.commit()
    return admin_auth.tenant_id


def _make_rule_set(db, tenant_id, **kwargs) -> RuleSet:
    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=f"检查项-{uuid.uuid4().hex[:8]}",
        next_rule_no=1,
        **kwargs,
    )
    db.add(rule_set)
    db.commit()
    return rule_set


def _add_rule(db, tenant_id, rule_set_id, *, check_category=None, intents=None, scope=None) -> Rule:
    rule = Rule(
        tenant_id=tenant_id,
        rule_set_id=rule_set_id,
        rule_no=1,
        rule_text="测试规则",
        check_category=check_category,
        intents=intents or [],
        scope=scope,
    )
    db.add(rule)
    db.commit()
    return rule


# ============ 种子与基础语义 ============


def test_builtin_intents_are_seeded(migrated_db, seeded_tenant) -> None:
    rows = migrated_db.execute(
        select(CheckIntent).where(CheckIntent.tenant_id == seeded_tenant)
    ).scalars().all()

    names = {row.name for row in rows}
    assert set(C.CHECK_CATEGORIES).issubset(names)
    assert all(row.status == "active" for row in rows if row.source == "seed")
    # 内置四类按固定顺序排列
    seeded = sorted(
        (row for row in rows if row.source == "seed"), key=lambda r: r.sort_order
    )
    assert [r.name for r in seeded] == list(C.CHECK_CATEGORIES)


def test_resolve_or_register_defaults_to_pending_review(migrated_db, admin_auth) -> None:
    name, is_new = check_intent_service.resolve_or_register(
        migrated_db, admin_auth.tenant_id, "金额一致性"
    )

    assert (name, is_new) == ("金额一致性", True)
    row = check_intent_service.find_intent(migrated_db, admin_auth.tenant_id, name)
    assert row is not None and row.status == "pending_review" and row.source == "rule_import"

    # 已登记的名称不会重复登记
    again, is_new_again = check_intent_service.resolve_or_register(
        migrated_db, admin_auth.tenant_id, "金额一致性"
    )
    assert (again, is_new_again) == ("金额一致性", False)


def test_resolve_or_register_can_auto_confirm(migrated_db, admin_auth) -> None:
    check_intent_service.resolve_or_register(
        migrated_db, admin_auth.tenant_id, "形式合规", auto_confirm=True
    )

    row = check_intent_service.find_intent(migrated_db, admin_auth.tenant_id, "形式合规")
    assert row is not None and row.status == "active"


def test_alias_maps_to_canonical(migrated_db, admin_auth) -> None:
    target = check_intent_service.create_intent(
        migrated_db, admin_auth.tenant_id, name="金额一致性", aliases=["金额校验", "金额核对"]
    )

    for alias in ("金额校验", "金额核对"):
        assert (
            check_intent_service.normalize_intent_name(
                migrated_db, alias, admin_auth.tenant_id
            )
            == target.name
        )
    # 未登记的名称原样返回
    assert (
        check_intent_service.normalize_intent_name(
            migrated_db, "从不存在的检查项", admin_auth.tenant_id
        )
        == "从不存在的检查项"
    )


# ============ 治理：改名 / 合并 / 删除 ============


def test_rename_rewrites_existing_rules(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)
    intent = check_intent_service.create_intent(
        migrated_db, admin_auth.tenant_id, name="形式审查"
    )
    rule = _add_rule(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        check_category="形式审查",
        intents=["形式审查", "其他意图"],
        scope={"doc_types": ["代理协议"], "intents": ["形式审查"]},
    )

    check_intent_service.update_intent(
        migrated_db, admin_auth.tenant_id, intent.id, name="形式合规"
    )
    migrated_db.refresh(rule)

    assert rule.check_category == "形式合规"
    assert rule.intents == ["形式合规", "其他意图"]
    assert rule.scope["intents"] == ["形式合规"]


def test_merge_intent_rewrites_refs_and_keeps_alias(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)
    source = check_intent_service.create_intent(
        migrated_db, admin_auth.tenant_id, name="形式合规"
    )
    target = check_intent_service.create_intent(
        migrated_db, admin_auth.tenant_id, name="基础判断"
    )
    rule = _add_rule(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        check_category="形式合规",
        intents=["形式合规"],
    )

    merged = check_intent_service.merge_intent(
        migrated_db, admin_auth.tenant_id, source.id, target.id
    )
    migrated_db.refresh(rule)
    migrated_db.refresh(source)

    assert merged is not None and merged.name == "基础判断"
    assert rule.check_category == "基础判断"
    assert rule.intents == ["基础判断"]
    # 旧名留作别名，后续导入会写时归一到 target
    assert "形式合规" in (merged.aliases or [])
    assert source.status == "rejected"
    assert (
        check_intent_service.normalize_intent_name(
            migrated_db, "形式合规", admin_auth.tenant_id
        )
        == "基础判断"
    )


def test_delete_intent_refuses_when_referenced(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)
    intent = check_intent_service.create_intent(
        migrated_db, admin_auth.tenant_id, name="审批层级"
    )
    _add_rule(
        migrated_db, admin_auth.tenant_id, rule_set.id, check_category="审批层级"
    )

    with pytest.raises(ValueError, match="仍被"):
        check_intent_service.delete_intent(
            migrated_db, admin_auth.tenant_id, intent.id
        )


def test_active_intent_names_excludes_pending(migrated_db, seeded_tenant) -> None:
    check_intent_service.create_intent(
        migrated_db, seeded_tenant, name="待确认项", status="pending_review"
    )

    active = check_intent_service.active_intent_names(migrated_db, seeded_tenant)

    assert "待确认项" not in active
    assert set(C.CHECK_CATEGORIES).issubset(set(active))


def test_known_check_categories_feeds_only_active(migrated_db, seeded_tenant) -> None:
    """未确认的检查项不能回灌提示词，否则自造名称会被自我强化。"""
    rule_set = _make_rule_set(migrated_db, seeded_tenant)
    check_intent_service.create_intent(
        migrated_db, seeded_tenant, name="自造检查项", status="pending_review"
    )

    names = _known_check_categories(migrated_db, seeded_tenant, rule_set.id)

    assert "自造检查项" not in names
    assert set(C.CHECK_CATEGORIES).issubset(set(names))


# ============ 导入集成（假 LLM + 真库）============


class _StubLLM:
    """返回固定解析结果的 LLM 桩。"""

    def __init__(self, rules: list[dict]) -> None:
        self._rules = rules
        self.system_prompt = ""

    def chat_json(self, messages, **_kwargs):
        self.system_prompt = messages[0]["content"]
        return {"rules": self._rules, "ontology": {}, "defects": []}


def _import_with_stub(db, tenant_id, rule_set_id, rules: list[dict], **kwargs):
    from unittest.mock import patch

    llm = _StubLLM(rules)
    with patch(
        "app.services.rule_import_service.get_llm_client", return_value=llm
    ):
        return import_rules_from_text(
            db, tenant_id, rule_set_id, "增值税发票的价税合计应与报销单金额一致。", **kwargs
        ), llm


def test_import_registers_new_check_intent_as_pending(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)
    result, _ = _import_with_stub(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        [
            {
                "rule_text": "发票价税合计应与报销单金额一致。",
                "doc_type": "增值税发票",
                "check_category": "金额一致性",
                "scope": {"doc_types": ["增值税发票"], "intents": ["金额一致性"]},
            }
        ],
    )

    assert result["new_check_categories"] == ["金额一致性"]
    row = check_intent_service.find_intent(
        migrated_db, admin_auth.tenant_id, "金额一致性"
    )
    assert row is not None and row.status == "pending_review"


def test_import_writes_scenario_tag(migrated_db, admin_auth) -> None:
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id, scenario="expense")
    _import_with_stub(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        [
            {
                "rule_text": "发票价税合计应与报销单金额一致。",
                "doc_type": "增值税发票",
                "check_category": "信息准确性",
            }
        ],
        scenario="expense",
    )

    rule = migrated_db.execute(
        select(Rule).where(Rule.rule_set_id == rule_set.id)
    ).scalars().first()
    assert rule is not None and rule.scenario == "expense"


def test_import_flags_unknown_assertion_field(migrated_db, admin_auth) -> None:
    """结构化断言引用了未登记字段 → info 缺陷 + 导入告警（不阻塞自动确认）。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)
    result, _ = _import_with_stub(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        [
            {
                "rule_text": "发票号码应与备案号一致。",
                "doc_type": "增值税发票",
                "check_category": "信息准确性",
                "structure": {
                    "assertion": {
                        "source": {"doc_type": "增值税发票", "field": "备案号"},
                        "target": {"doc_type": "增值税发票", "field": "发票号码"},
                        "operator": "eq",
                    }
                },
            }
        ],
    )

    assert any("未登记" in w for w in result["import_warnings"])
    assert "增值税发票.备案号" in " ".join(result["import_warnings"])

    rule = migrated_db.execute(
        select(Rule).where(Rule.rule_set_id == rule_set.id)
    ).scalars().first()
    assert rule is not None
    types = {d.get("type") for d in (rule.defects or [])}
    assert "unknown_field" in types
    # info 级缺陷不阻塞自动确认
    severities = {
        d.get("severity") for d in (rule.defects or []) if d.get("type") == "unknown_field"
    }
    assert severities == {"info"}
    assert rule.enabled is True


def test_import_does_not_flag_known_fields(migrated_db, admin_auth) -> None:
    """引用已登记字段不应产生未登记字段告警。"""
    rule_set = _make_rule_set(migrated_db, admin_auth.tenant_id)
    result, _ = _import_with_stub(
        migrated_db,
        admin_auth.tenant_id,
        rule_set.id,
        [
            {
                "rule_text": "发票号码应与合同号一致。",
                "doc_type": "增值税发票",
                "check_category": "信息准确性",
                "structure": {
                    "assertion": {
                        "source": {"doc_type": "增值税发票", "field": "发票号码"},
                        "target": {"doc_type": "增值税发票", "field": "合同号"},
                        "operator": "eq",
                    }
                },
            }
        ],
    )

    assert not any("未登记字段" in w for w in result["import_warnings"])


# ============ HTTP 层：检查项管理 API ============


def test_check_intent_api_crud(client, admin_auth) -> None:
    headers = authenticate(client, admin_auth)

    created = client.post(
        "/api/check-intents",
        headers=headers,
        json={"name": "金额一致性", "aliases": ["金额校验"], "sort_order": 50},
    )
    assert created.status_code == 201, created.text
    intent_id = created.json()["id"]
    assert created.json()["status"] == "active"

    listed = client.get("/api/check-intents", headers=headers)
    assert listed.status_code == 200
    assert any(x["name"] == "金额一致性" for x in listed.json())

    renamed = client.put(
        f"/api/check-intents/{intent_id}",
        headers=headers,
        json={"name": "金额核对"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "金额核对"

    target = client.post(
        "/api/check-intents", headers=headers, json={"name": "信息准确性"}
    ).json()
    merged = client.post(
        f"/api/check-intents/{intent_id}/merge",
        headers=headers,
        json={"target_id": target["id"]},
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["name"] == "信息准确性"
    assert "金额核对" in merged.json()["aliases"]

    # 合并后源检查项已弃用且无引用 → 可删除
    assert client.delete(
        f"/api/check-intents/{intent_id}", headers=headers
    ).status_code == 200


def test_check_intent_api_rejects_duplicate_name(client, admin_auth) -> None:
    headers = authenticate(client, admin_auth)
    client.post("/api/check-intents", headers=headers, json={"name": "重复项"})

    again = client.post("/api/check-intents", headers=headers, json={"name": "重复项"})

    assert again.status_code == 400
    assert "已存在" in again.json()["detail"]
