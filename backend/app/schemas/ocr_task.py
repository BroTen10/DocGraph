"""OCR 任务相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OcrTaskOut(BaseModel):
    """OCR 任务完整信息（触发后返回 + 进度查询）。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_set_id: UUID
    scope: str
    doc_id: UUID | None = None
    contract_id: UUID | None = None
    status: str
    progress: int
    stage: str | None = None
    total_count: int
    done_count: int
    success_count: int
    failed_count: int
    failures: list[dict[str, Any]] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime | None = None
    error: str | None = None
    created_at: datetime


class OcrTaskBrief(BaseModel):
    """OCR 任务精简信息（列表视图）。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scope: str
    contract_id: UUID | None = None
    status: str
    progress: int
    stage: str | None = None
    total_count: int
    done_count: int
    success_count: int
    failed_count: int
    start_time: datetime
    end_time: datetime | None = None
