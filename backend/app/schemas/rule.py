"""规则相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RuleBase(BaseModel):
    doc_type: str
    check_category: str
    rule_text: str
    tolerance: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    priority: int = 100


class RuleCreate(RuleBase):
    pass


class RuleUpdate(BaseModel):
    doc_type: str | None = None
    check_category: str | None = None
    rule_text: str | None = None
    tolerance: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = None


class RuleImportRequest(BaseModel):
    """规则批量导入请求。"""
    raw_text: str


class RuleImportResponse(BaseModel):
    """规则批量导入结果。"""
    total: int
    imported: int
    skipped: int
    rules: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RuleOut(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    updated_at: datetime
    created_at: datetime
