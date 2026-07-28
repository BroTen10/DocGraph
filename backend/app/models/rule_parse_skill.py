"""规则解析 Skill 模型：用户编写/系统内置的解析控制指令。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class RuleParseSkill(Base):
    """规则解析 Skill：控制 LLM 解析规则的行为。

    rule_set_id = NULL → 系统内置默认（全局）
    rule_set_id ≠ NULL → 该规则集自定义 Skill
    """

    __tablename__ = "rule_parse_skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_sets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    """NULL = 系统内置默认（全局），非 NULL = 该规则集专用"""

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_parse_skills.id"),
        nullable=True,
    )
    """编辑内置默认产生的副本指向原始内置 Skill，用于版本追溯"""

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    """True = 系统内置默认，不可删除（但可编辑产生副本）"""
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)

    # Skill 内容（YAML 解析后的 dict）
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    """包含所有能力：prompt_instructions / field_mappings / defaults / validations / text_preprocessing / term_normalization / domain_context"""

    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RuleParseSkill {'[BUILTIN] ' if self.is_builtin else ''}{self.name}>"
