# -*- coding: utf-8 -*-
"""内置场景知识包（通用契约 + 场景包）的资源、文档镜像与启动同步测试。"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from app.database import _seed_builtin_skill
from app.default_rule_parse_skill import (
    DEFAULT_SCENARIO,
    GENERIC_SCENARIO,
    load_builtin_skill_packs,
    load_default_rule_parse_skill_content,
    packs_for_scenario,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _FakeSession:
    def __init__(self, existing=None):
        if existing is None:
            existing = []
        elif not isinstance(existing, list):
            existing = [existing]
        self.existing = existing
        self.added = []
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _statement):
        return _ScalarResult(self.existing)

    def add(self, value):
        self.added.append(value)
        self.existing.append(value)

    def commit(self):
        self.commit_count += 1


def test_builtin_packs_are_valid_and_cover_generic_plus_scenario() -> None:
    packs = load_builtin_skill_packs()

    assert packs, "至少应存在一个内置场景知识包"
    generic = [p for p in packs if p.scenario is GENERIC_SCENARIO]
    assert len(generic) == 1, "通用契约包必须唯一（scenario 为 null）"
    assert len(generic[0].content["prompt_instructions"]) >= 20

    trade = [p for p in packs if p.scenario == DEFAULT_SCENARIO]
    assert len(trade) == 1, "trade 场景包必须存在（存量规则集默认场景）"
    # 通用契约包必须场景无关：不应出现贸易专有名词
    generic_text = "\n".join(generic[0].content["prompt_instructions"])
    for domain_word in ("报关单", "印章", "贸易", "齐套性"):
        assert domain_word not in generic_text, f"通用契约包不应包含领域词：{domain_word}"


def test_scenario_selection_falls_back_to_generic_only() -> None:
    trade_keys = [p.key for p in packs_for_scenario("trade")]
    unknown_keys = [p.key for p in packs_for_scenario("unknown-domain")]

    assert "generic" in trade_keys and "trade" in trade_keys
    assert unknown_keys == ["generic"], "未登记的未知场景只加载通用契约包"


def test_default_skill_content_matches_docs_mirror() -> None:
    content = load_default_rule_parse_skill_content()
    docs_path = _REPO_ROOT / "docs" / "通用贸易默认Skill.yaml"
    docs_content = yaml.safe_load(docs_path.read_text(encoding="utf-8"))

    assert docs_content == content
    assert len(content["prompt_instructions"]) >= 20
    assert content["defaults"]["tolerance"] == {
        "amount_percent": 0,
        "weight_kg": 0,
        "quantity": 0,
        "time_days": 0,
    }
    assert all(step["type"] == "replace" for step in content["text_preprocessing"])
    assert content["term_normalization"] == {}


def test_generic_scenario_has_no_domain_glossary() -> None:
    content = load_default_rule_parse_skill_content("unknown-domain")

    assert content["domain_context"].get("glossary") in (None, {})
    assert content["field_mappings"] == {}


def test_seed_builtin_skill_updates_existing_records() -> None:
    packs = load_builtin_skill_packs()
    existing = [
        SimpleNamespace(
            name="通用贸易规则解析配置",
            description="旧描述",
            content={"prompt_instructions": ["旧指令"]},
            version=3,
            scenario=None,
            priority=100,
        )
    ]
    session = _FakeSession(existing=existing)

    with patch("app.database.SessionLocal", return_value=session):
        _seed_builtin_skill()

    # 存量 NULL 场景记录被通用契约包接管，其余场景包新建
    assert len(session.added) == len(packs) - 1
    legacy = existing[0]
    generic_pack = next(p for p in packs if p.scenario is None)
    assert legacy.name == generic_pack.name
    assert legacy.content == generic_pack.content
    assert legacy.version == 4
    assert session.commit_count == len(packs)


def test_seed_builtin_skill_is_idempotent_when_content_matches() -> None:
    packs = load_builtin_skill_packs()
    existing = [
        SimpleNamespace(
            name=pack.name,
            description=pack.description,
            content=copy.deepcopy(pack.content),
            version=4,
            scenario=pack.scenario,
            priority=pack.priority,
        )
        for pack in packs
    ]
    session = _FakeSession(existing=existing)

    with patch("app.database.SessionLocal", return_value=session):
        _seed_builtin_skill()

    assert session.commit_count == 0
    assert session.added == []
    assert all(row.version == 4 for row in existing)


def test_seed_builtin_skill_creates_records_when_missing() -> None:
    packs = load_builtin_skill_packs()
    session = _FakeSession(existing=[])

    with patch("app.database.SessionLocal", return_value=session):
        _seed_builtin_skill()

    assert len(session.added) == len(packs)
    assert all(row.is_builtin is True for row in session.added)
    assert all(row.version == 1 for row in session.added)
    assert {row.scenario for row in session.added} == {p.scenario for p in packs}
    assert session.commit_count == len(packs)
