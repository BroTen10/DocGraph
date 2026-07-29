"""规则解析 Skill 相关 Pydantic schemas。

Skill 内容对外统一使用 YAML 文本（content_yaml）描述：
- 创建/更新：前端提交 content_yaml（YAML 字符串），后端解析并校验后落库为 JSONB。
- 读取：后端把 JSONB 内容序列化回 YAML 文本返回（content_yaml），同时保留结构化 content 供程序消费。
- 兼容：仍接受结构化 content（dict），二者取其一，content_yaml 优先。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class RuleParseSkillContent(BaseModel):
    """Skill 内容（YAML 解析后的结构）。"""
    prompt_instructions: list[str] = Field(default_factory=list)
    field_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    validations: list[dict[str, Any]] = Field(default_factory=list)
    text_preprocessing: list[dict[str, Any]] = Field(default_factory=list)
    term_normalization: dict[str, list[str]] = Field(default_factory=dict)
    domain_context: dict[str, Any] = Field(default_factory=dict)


def parse_content_yaml(text: str) -> RuleParseSkillContent:
    """解析并校验 YAML 文本为 Skill 内容。

    Raises:
        ValueError: YAML 语法错误或结构不符合 schema，message 为可读中文。
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）" if mark else ""
        raise ValueError(f"YAML 语法错误{loc}: {getattr(e, 'problem', e)}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("YAML 顶层必须是键值映射（mapping），不能是列表或标量")
    # 容错：如果用户把完整创建体（含 name/content）粘进来，取其中的 content
    if "content" in data and isinstance(data["content"], dict):
        data = data["content"]
    try:
        return RuleParseSkillContent(**data)
    except Exception as e:
        raise ValueError(f"Skill 内容结构不合法: {e}")


def dump_content_yaml(content: dict | RuleParseSkillContent) -> str:
    """将 Skill 内容序列化为 YAML 文本（中文可读，不排序键）。"""
    if isinstance(content, RuleParseSkillContent):
        content = content.model_dump(mode="json")
    return yaml.safe_dump(
        content, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120
    )


class RuleParseSkillBase(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True
    priority: int = 100


class RuleParseSkillCreate(RuleParseSkillBase):
    """创建 Skill（仅对当前规则集）。content_yaml 与 content 二选一，前者优先。"""
    content_yaml: str | None = None
    content: RuleParseSkillContent | None = None

    @model_validator(mode="after")
    def _require_content(self) -> "RuleParseSkillCreate":
        if self.content_yaml is None and self.content is None:
            raise ValueError("必须提供 content_yaml（YAML 文本）或 content（结构化内容）")
        return self

    def resolved_content(self) -> RuleParseSkillContent:
        if self.content_yaml is not None:
            return parse_content_yaml(self.content_yaml)
        return self.content  # type: ignore[return-value]


class RuleParseSkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    content_yaml: str | None = None
    content: RuleParseSkillContent | None = None

    def resolved_content(self) -> RuleParseSkillContent | None:
        """返回本次更新携带的内容（若有）。"""
        if self.content_yaml is not None:
            return parse_content_yaml(self.content_yaml)
        return self.content


class RuleParseSkillOut(RuleParseSkillBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_set_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    is_builtin: bool = False
    content: RuleParseSkillContent
    version: int = 1
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[misc]
    @property
    def content_yaml(self) -> str:
        return dump_content_yaml(self.content)


class SkillLearnRequest(BaseModel):
    """人工修正规则后，将修正经验写回 Skill 的请求。"""
    rule_id: uuid.UUID | None = None
    skill_id: uuid.UUID | None = None
    """目标 Skill；不传则写入（或自动创建）规则集下的『经验修正（自动累积）』Skill"""
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    """用户补充说明（可选）"""
