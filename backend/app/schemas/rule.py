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
    # LLM 置信度 (0-1)，null 表示未评估
    confidence: float | None = None
    # 确认状态：pending = 待确认, confirmed = 已确认
    status: str = "pending"


class RuleCreate(RuleBase):
    pass


class RuleUpdate(BaseModel):
    doc_type: str | None = None
    check_category: str | None = None
    rule_text: str | None = None
    tolerance: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = None
    confidence: float | None = None
    status: str | None = None


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
    rule_set_id: UUID
    status: str
    confidence: float | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    updated_at: datetime
    created_at: datetime


class RuleBatchConfirmRequest(BaseModel):
    """批量确认规则。ids 不传则确认该规则集下所有 pending 规则。"""
    ids: list[UUID] | None = None
