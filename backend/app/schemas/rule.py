"""规则相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DefectItem(BaseModel):
    """规则缺陷/问题项。"""
    type: str = Field(description="缺陷类型：ambiguous_reference|incomplete_condition|missing_value|contradiction|uncertainty|format_violation|logical_contradiction|boundary_overlap|redundant")
    severity: str = Field(description="严重程度：error|warning|info")
    description: str = Field(description="问题描述")
    rule_index: int | None = Field(default=None, description="对应 rules 数组中的索引")
    related_rule_ids: list[str] | None = Field(default=None, description="关联的冲突规则 ID")


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
    # LLM 检测到的缺陷列表
    defects: list[DefectItem] = Field(default_factory=list)


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
    defects: list[DefectItem] | None = None


class RuleImportRequest(BaseModel):
    """规则批量导入请求。"""
    raw_text: str
    skill_ids: list[uuid.UUID] | None = Field(default=None, description="指定应用的 Skill ID，不传则使用默认")


class ConflictReport(BaseModel):
    """冲突报告汇总。"""
    total_defects: int = 0
    by_severity: dict[str, int] = Field(default_factory=lambda: {"error": 0, "warning": 0, "info": 0})
    defects: list[DefectItem] = Field(default_factory=list)


class RuleImportResponse(BaseModel):
    """规则批量导入结果。"""
    total: int
    imported: int
    skipped: int
    rules: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    conflict_report: ConflictReport | None = None


class RuleOut(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_set_id: UUID
    status: str
    confidence: float | None = None
    defects: list[DefectItem] = Field(default_factory=list)
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    updated_at: datetime
    created_at: datetime


class RuleBatchConfirmRequest(BaseModel):
    """批量确认规则。ids 不传则确认该规则集下所有 pending 规则。"""
    ids: list[UUID] | None = None


class ConflictItem(BaseModel):
    """规则间冲突项。"""
    rule_ids: list[str] = Field(description="参与冲突的规则 ID 列表")
    type: str = Field(description="冲突类型")
    severity: str = Field(description="严重程度")
    description: str = Field(description="冲突描述")


class ConflictDetectionResponse(BaseModel):
    """冲突检测响应。"""
    total_conflicts: int = 0
    affected_rules: int = 0
    conflicts: list[ConflictItem] = Field(default_factory=list)
