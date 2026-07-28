"""规则解析 Skill 相关 Pydantic schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuleParseSkillContent(BaseModel):
    """Skill 内容（YAML 解析后的结构）。"""
    prompt_instructions: list[str] = Field(default_factory=list)
    field_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    validations: list[dict[str, Any]] = Field(default_factory=list)
    text_preprocessing: list[dict[str, Any]] = Field(default_factory=list)
    term_normalization: dict[str, list[str]] = Field(default_factory=dict)
    domain_context: dict[str, Any] = Field(default_factory=dict)


class RuleParseSkillBase(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    priority: int = 100
    content: RuleParseSkillContent


class RuleParseSkillCreate(RuleParseSkillBase):
    """创建 Skill（仅对当前规则集）。"""
    pass


class RuleParseSkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    content: RuleParseSkillContent | None = None


class RuleParseSkillOut(RuleParseSkillBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_set_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    is_builtin: bool = False
    version: int = 1
    created_at: datetime
    updated_at: datetime
