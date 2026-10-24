from __future__ import annotations

import tempfile
import uuid

import pytest

from conftest import authenticate
from app.models import Rule, RuleParseSkill, RuleSet
from app.schemas.rule_parse_skill import RuleParseSkillContent, RuleParseSkillUpdate
from app.services import (
    rule_import_service,
    rule_import_task,
    rule_parse_skill_service,
)
from app.services.rule_parse_engine import compile_directive


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


def test_builtin_skill_edits_create_isolated_tenant_copies(
    migrated_db,
    admin_auth,
    admin_b,
    rule_a,
):
    assert admin_auth.tenant_id is not None
    assert admin_b.tenant_id is not None
    builtin = RuleParseSkill(
        tenant_id=None,
        rule_set_id=None,
        name="全局只读模板",
        is_builtin=True,
        enabled=True,
        priority=100,
        content={"prompt_instructions": ["template"]},
        version=1,
    )
    rule_set_b = RuleSet(
        tenant_id=admin_b.tenant_id,
        name=f"乙租户规则集-{uuid.uuid4().hex[:8]}",
        next_rule_no=1,
    )
    migrated_db.add_all([builtin, rule_set_b])
    migrated_db.commit()

    copy_a = rule_parse_skill_service.update_skill(
        migrated_db,
        admin_auth.tenant_id,
        rule_a.rule_set_id,
        builtin.id,
        RuleParseSkillUpdate(enabled=False, priority=10),
    )
    copy_a_again = rule_parse_skill_service.update_skill(
        migrated_db,
        admin_auth.tenant_id,
        rule_a.rule_set_id,
        builtin.id,
        RuleParseSkillUpdate(priority=5),
    )
    copy_b = rule_parse_skill_service.update_skill(
        migrated_db,
        admin_b.tenant_id,
        rule_set_b.id,
        builtin.id,
        RuleParseSkillUpdate(enabled=True, priority=20),
    )

    assert copy_a is not None and copy_b is not None and copy_a_again is not None
    assert copy_a.id == copy_a_again.id
    assert copy_a.id != copy_b.id
    assert copy_a.enabled is False and copy_b.enabled is True
    assert copy_a.priority == 10
    assert copy_a_again.priority == 5
    assert copy_b.priority == 20
    stored_copy_a = migrated_db.get(RuleParseSkill, copy_a.id)
    migrated_db.refresh(stored_copy_a)
    assert stored_copy_a.priority == 5

    migrated_db.refresh(builtin)
    assert builtin.enabled is True
    assert builtin.priority == 100

    visible_a = rule_parse_skill_service.list_skills(
        migrated_db, admin_auth.tenant_id, rule_a.rule_set_id
    )
    visible_b = rule_parse_skill_service.list_skills(
        migrated_db, admin_b.tenant_id, rule_set_b.id
    )
    assert builtin.id not in {skill.id for skill in visible_a}
    assert builtin.id not in {skill.id for skill in visible_b}
    assert copy_a.id in {skill.id for skill in visible_a}
    assert copy_b.id in {skill.id for skill in visible_b}

    rule_parse_skill_service.update_skill(
        migrated_db,
        admin_auth.tenant_id,
        rule_a.rule_set_id,
        builtin.id,
        RuleParseSkillUpdate(
            enabled=True,
            content=RuleParseSkillContent(prompt_instructions=["tenant-copy"]),
        ),
    )
    directive_a = compile_directive(
        migrated_db, admin_auth.tenant_id, rule_a.rule_set_id
    )
    assert "tenant-copy" in directive_a.prompt_additions
    assert "template" not in directive_a.prompt_additions


def test_synchronous_import_foreign_ruleset_is_404_without_service_call(
    client,
    admin_b,
    rule_a,
    monkeypatch,
):
    headers = authenticate(client, admin_b)
    monkeypatch.setattr(
        rule_import_service,
        "import_rules_with_skills",
        lambda *args, **kwargs: pytest.fail("foreign ruleset reached import service"),
    )
    response = client.post(
        "/api/rules/import-batch",
        params={"rule_set_id": rule_a.rule_set_id},
        json={"raw_text": "测试规则", "skill_ids": []},
        headers=headers,
    )
    assert response.status_code == 404


def test_rule_document_rejects_foreign_ruleset_before_temp_file(
    client,
    admin_b,
    rule_a,
    monkeypatch,
):
    headers = authenticate(client, admin_b)

    def fail_tempfile(*args, **kwargs):
        pytest.fail("temporary file created before ruleset ownership check")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_tempfile)
    response = client.post(
        "/api/rules/import-document",
        params={"rule_set_id": rule_a.rule_set_id},
        files={"file": ("rules.pdf", b"%PDF-test", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 404


def test_rule_document_rejects_disallowed_suffix_before_temp_file(
    client,
    admin_auth,
    rule_a,
    monkeypatch,
):
    headers = authenticate(client, admin_auth)

    def fail_tempfile(*args, **kwargs):
        pytest.fail("temporary file created for a disallowed suffix")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_tempfile)
    response = client.post(
        "/api/rules/import-document",
        params={"rule_set_id": rule_a.rule_set_id},
        files={"file": ("rules.exe", b"not-a-rule-document", "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 400
