# -*- coding: utf-8 -*-
"""内置规则解析 Skill 的资源、文档镜像与启动同步测试。"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from app.database import _seed_builtin_skill
from app.default_rule_parse_skill import (
    DEFAULT_RULE_PARSE_SKILL_DESCRIPTION,
    DEFAULT_RULE_PARSE_SKILL_NAME,
    load_default_rule_parse_skill_content,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _FakeSession:
    def __init__(self, existing=None):
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

    def commit(self):
        self.commit_count += 1


def test_default_skill_resource_is_valid_and_matches_docs_mirror() -> None:
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


def test_seed_builtin_skill_updates_existing_record() -> None:
    content = load_default_rule_parse_skill_content()
    existing = SimpleNamespace(
        name="旧默认",
        description="旧描述",
        content={"prompt_instructions": ["旧指令"]},
        version=3,
    )
    session = _FakeSession(existing=existing)

    with patch("app.database.SessionLocal", return_value=session):
        _seed_builtin_skill()

    assert existing.name == DEFAULT_RULE_PARSE_SKILL_NAME
    assert existing.description == DEFAULT_RULE_PARSE_SKILL_DESCRIPTION
    assert existing.content == content
    assert existing.version == 4
    assert session.commit_count == 1
    assert session.added == []


def test_seed_builtin_skill_is_idempotent_when_content_matches() -> None:
    content = load_default_rule_parse_skill_content()
    existing = SimpleNamespace(
        name=DEFAULT_RULE_PARSE_SKILL_NAME,
        description=DEFAULT_RULE_PARSE_SKILL_DESCRIPTION,
        content=copy.deepcopy(content),
        version=4,
    )
    session = _FakeSession(existing=existing)

    with patch("app.database.SessionLocal", return_value=session):
        _seed_builtin_skill()

    assert existing.version == 4
    assert session.commit_count == 0
    assert session.added == []


def test_seed_builtin_skill_creates_record_when_missing() -> None:
    session = _FakeSession(existing=None)

    with patch("app.database.SessionLocal", return_value=session):
        _seed_builtin_skill()

    assert len(session.added) == 1
    assert session.added[0].is_builtin is True
    assert session.added[0].version == 1
    assert session.commit_count == 1
