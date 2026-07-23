"""审查相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewStartRequest(BaseModel):
    contract_id: UUID
    snapshot_id: UUID | None = None  # 不传则用最新快照


class ReviewTaskListItem(BaseModel):
    """审查任务列表项（含合同号便于展示）。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    contract_no: str | None = None
    status: str
    progress: int
    stage: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    error: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class ReviewTaskStatus(BaseModel):
    """审查任务进度查询。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    status: str
    progress: int
    stage: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    error: str | None = None


class ReviewTaskSummary(ReviewTaskStatus):
    summary: dict[str, Any] = Field(default_factory=dict)


class ReviewResultItem(BaseModel):
    """单条审查结果。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_id: UUID | None = None
    rule_text: str | None = None
    doc_type: str | None = None
    check_category: str | None = None
    doc_id: UUID | None = None
    doc_name: str | None = None
    result: str  # pass / fail / unverifiable
    issue_desc: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    suggestion: str | None = None


class ReviewResultByRule(BaseModel):
    """按规则维度视图。"""
    task_id: UUID
    results: list[ReviewResultItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ReviewResultByDoc(BaseModel):
    """按文档维度视图。"""
    task_id: UUID
    docs: list[dict] = Field(default_factory=list)  # [{document, results: [...]}]
    summary: dict[str, Any] = Field(default_factory=dict)
