# -*- coding: utf-8 -*-
"""经验归并（LLM 维护规则指令）测试。

背景：人工修正经验以"只增不减"的方式累积进 Skill 的 prompt_instructions，
指令越多越容易重复、近义、自相矛盾。归并交给 LLM 做，人只负责修正。

验证：
1. 归并**替换**而不是追加：5 条杂乱指令 + 账本 → 2 条干净指令；
2. 归并输入同时包含现有指令与人工修正账本（模型能看到"人改过什么"）；
3. 模型输出非法/为空 → 经验保持原样（抛 ValueError，不改版本、不改内容）；
4. 超量输出被截断到上限、单条超长被截短；
5. 没有经验、没有账本时可归并对象不存在 → 明确报错。

运行：需 Postgres（TEST_DATABASE_URL 指向 *_test 库）：pytest tests/test_skill_consolidation.py
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

from app.models import RuleParseSkill, RuleSet  # noqa: E402
from app.services import correction_ledger, rule_parse_skill_service as svc  # noqa: E402


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _patch_llm(monkeypatch, fake) -> None:
    monkeypatch.setattr(svc, "get_llm_client", lambda: fake)


def _make_rule_set(db, tenant_id) -> RuleSet:
    rs = RuleSet(tenant_id=tenant_id, name=f"归并-{uuid.uuid4().hex[:6]}")
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return rs


def _make_skill(db, tenant_id, rule_set_id, instructions: list[str]) -> RuleParseSkill:
    skill = RuleParseSkill(
        tenant_id=tenant_id,
        rule_set_id=rule_set_id,
        name=svc.LEARN_SKILL_NAME,
        description="测试用经验 Skill",
        is_builtin=False,
        enabled=True,
        priority=50,
        content={"prompt_instructions": list(instructions)},
        version=3,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def test_consolidation_replaces_instructions(db, default_tenant_id, monkeypatch) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    messy = [
        "金额要比对",
        "金额应比对（准确）",
        "报销类单据归基础判断",
        "日期顺序要注意",
        "金额比对",
    ]
    skill = _make_skill(db, default_tenant_id, rs.id, messy)
    correction_ledger.record_correction(
        db,
        default_tenant_id,
        scope="rule_edit",
        field="check_category",
        before="信息准确性",
        after="基础判断",
        rule_set_id=rs.id,
        context="发票价税合计应等于报销单报销金额。",
    )
    db.commit()

    fake = _FakeLLM(
        {
            "instructions": ["金额类规则需比对两侧取值", "报销类规则的检查项优先用「基础判断」"],
            "note": "合并重复的金额比对指令，并按人工修正固定检查项口径",
        }
    )
    _patch_llm(monkeypatch, fake)

    out, summary = svc.consolidate_experience_skill(
        db, default_tenant_id, rs.id
    )

    assert summary["before"] == 5 and summary["after"] == 2
    assert out.content.prompt_instructions == [
        "金额类规则需比对两侧取值",
        "报销类规则的检查项优先用「基础判断」",
    ]
    assert out.version == 4  # 内容变化 → 版本 +1；替换而非追加

    user_prompt = fake.calls[0]["messages"][1]["content"]
    assert "金额要比对" in user_prompt  # 现有指令进了输入
    assert "基础判断" in user_prompt and "人工修正" in user_prompt  # 账本也进了输入


def test_invalid_llm_output_keeps_skill_unchanged(db, default_tenant_id, monkeypatch) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    skill = _make_skill(db, default_tenant_id, rs.id, ["原指令一", "原指令二"])
    before_version, before_content = skill.version, dict(skill.content)

    _patch_llm(monkeypatch, _FakeLLM({"instructions": "不是列表"}))
    with pytest.raises(ValueError):
        svc.consolidate_experience_skill(db, default_tenant_id, rs.id)

    db.refresh(skill)
    assert skill.version == before_version
    assert skill.content == before_content


def test_llm_failure_keeps_skill_unchanged(db, default_tenant_id, monkeypatch) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    skill = _make_skill(db, default_tenant_id, rs.id, ["原指令"])
    before_version = skill.version

    _patch_llm(monkeypatch, _FakeLLM(RuntimeError("模型不可用")))
    with pytest.raises(ValueError) as exc:
        svc.consolidate_experience_skill(db, default_tenant_id, rs.id)

    assert "经验保持原样" in str(exc.value)
    db.refresh(skill)
    assert skill.version == before_version
    assert skill.content["prompt_instructions"] == ["原指令"]


def test_output_is_capped_and_trimmed(db, default_tenant_id, monkeypatch) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    skill = _make_skill(db, default_tenant_id, rs.id, ["原指令"])
    long_text = "长" * 500
    _patch_llm(
        monkeypatch,
        _FakeLLM({"instructions": [long_text] + [f"指令{i}" for i in range(30)], "note": ""}),
    )

    out, summary = svc.consolidate_experience_skill(
        db, default_tenant_id, rs.id, max_instructions=5, max_instruction_chars=50
    )

    instructions = out.content.prompt_instructions
    assert len(instructions) == 5
    assert len(instructions[0]) == 50
    assert summary["after"] == 5


def test_nothing_to_consolidate(db, default_tenant_id, monkeypatch) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    _patch_llm(monkeypatch, _FakeLLM({"instructions": ["x"]}))
    with pytest.raises(ValueError):
        svc.consolidate_experience_skill(db, default_tenant_id, rs.id)
