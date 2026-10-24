from __future__ import annotations

import uuid

import pytest

from conftest import authenticate
from app.models import Rule, RuleParseSkill, RuleSet
from app.services import rule_import_task


@pytest.fixture
def rule_a(migrated_db, admin_auth):
    assert admin_auth.tenant_id is not None
    rule_set = RuleSet(
        tenant_id=admin_auth.tenant_id,
        name=f"规则隔离-{uuid.uuid4().hex[:8]}",
        next_rule_no=2,
    )
    migrated_db.add(rule_set)
    migrated_db.flush()
    rule = Rule(
        tenant_id=admin_auth.tenant_id,
        rule_set_id=rule_set.id,
        rule_no=1,
        rule_text="出口报关单金额应等于委托单金额",
        status="confirmed",
        enabled=True,
    )
    migrated_db.add(rule)
    migrated_db.commit()
    return rule


def test_rule_and_import_task_are_not_visible_across_tenants(
    client,
    admin_auth,
    admin_b,
    rule_a,
):
    headers = authenticate(client, admin_b)
    assert client.get(
        "/api/rules",
        params={"rule_set_id": rule_a.rule_set_id},
    ).status_code == 404
    assert client.delete(
        f"/api/rules/{rule_a.id}",
        headers=headers,
    ).status_code == 404

    task = rule_import_task.create_task(
        rule_set_id=rule_a.rule_set_id,
        tenant_id=admin_auth.tenant_id,
        user_id=admin_auth.user_id,
        file_name="rules.xlsx",
    )
    assert rule_import_task.get_task(task.task_id, admin_auth.tenant_id) is task
    assert rule_import_task.get_task(task.task_id, admin_b.tenant_id) is None


def test_skill_object_ids_are_hidden_across_tenants(
    client,
    migrated_db,
    admin_auth,
    admin_b,
    rule_a,
):
    assert admin_auth.tenant_id is not None
    skill = RuleParseSkill(
        tenant_id=admin_auth.tenant_id,
        rule_set_id=rule_a.rule_set_id,
        name="甲租户 Skill",
        is_builtin=False,
        content={},
    )
    migrated_db.add(skill)
    migrated_db.commit()

    authenticate(client, admin_b)
    response = client.get(
        f"/api/rule-sets/{rule_a.rule_set_id}/skills/{skill.id}"
    )
    assert response.status_code == 404
