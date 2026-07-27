"""规则集相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RuleSetBase(BaseModel):
    """规则集基础字段。"""

    name: str
    description: str | None = None
    doc_types: list[str] = Field(default_factory=list)
    is_default: bool = False


class RuleSetCreate(RuleSetBase):
    """创建规则集请求。"""

    pass


class RuleSetUpdate(BaseModel):
    """更新规则集请求（所有字段可选）。"""

    name: str | None = None
    description: str | None = None
    doc_types: list[str] | None = None
    is_default: bool | None = None


class RuleSetOut(RuleSetBase):
    """规则集响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
