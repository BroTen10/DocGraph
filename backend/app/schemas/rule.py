"""规则相关 Pydantic schemas。"""

from __future__ import annotations

import uuid
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
    rule_code: str | None = Field(default=None, description="所属规则流水号，如 R0001")
    related_rule_ids: list[str] | None = Field(default=None, description="关联的冲突规则 ID")
    related_rule_codes: list[str] | None = Field(default=None, description="关联的冲突规则流水号")
    # ----- 方案 A：缺陷处理状态 -----
    # resolved = 用户/系统已认可该缺陷（规则确认即视为认可）；缺省/null = 未处理。
    # 已处理缺陷保留在列表中用于审计，仅参与「已处理」展示，不再计入问题页签。
    resolution: str | None = Field(default=None, description="处理状态：resolved=已处理；缺省=未处理")
    resolved_at: str | None = Field(default=None, description="处理时间（ISO8601）")
    resolved_by: str | None = Field(default=None, description="处理来源：用户确认/导入自动确认/冲突消除/历史迁移")
    resolution_note: str | None = Field(default=None, description="处理说明")


class RuleBase(BaseModel):
    # 批次 10 起为可选派生标签：明确指向单一文件类型/检查项时填写，跨文件或整批规则可为空
    doc_type: str | None = None
    check_category: str | None = None
    # 业务场景标签（导入时写入的场景知识包键）
    scenario: str | None = None
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
    # 结构化审查意图（可选）：{"condition": {...}, "assertion": {...}, "exceptions": [...]}
    # 为 null 表示未结构化（旧规则）
    structure: dict[str, Any] | None = None
    # 批次 10：规则自描述
    # 适用范围：{"doc_types": [...]} 或 "ALL" 或 None
    scope: dict[str, Any] | None = None
    # 检查意图标签（派生，可多条）
    intents: list[str] = Field(default_factory=list)
    # 来源追溯
    provenance: dict[str, Any] | None = None


class RuleCreate(RuleBase):
    pass


class RuleUpdate(BaseModel):
    doc_type: str | None = None
    check_category: str | None = None
    scenario: str | None = None
    rule_text: str | None = None
    tolerance: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = None
    confidence: float | None = None
    status: str | None = None
    defects: list[DefectItem] | None = None
    structure: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    intents: list[str] | None = None
    provenance: dict[str, Any] | None = None


class RuleImportRequest(BaseModel):
    """规则批量导入请求。"""
    raw_text: str
    skill_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="自定义场景通道：勾选的 Skill。传了则不加载场景知识包，"
        "生效 = 系统预设（通用契约包）+ 勾选的 Skill",
    )
    scenario: str | None = Field(
        default=None,
        description="出厂场景通道的业务场景键（对应内置场景知识包，如 trade/expense/supplier）；"
        "不传则用导入文件声明或规则集默认场景。仅在未指定 skill_ids 时生效",
    )


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
    # 批次 10：本次导入新发现的文件类型（前端据此提示用户做样本分析）
    new_doc_types: list[str] = Field(default_factory=list)
    # 本次导入新登记的检查项（默认 pending_review，需在「检查项」页确认）
    new_check_categories: list[str] = Field(default_factory=list)
    # 表格行覆盖率与导入告警（合并单元格展开/漏行/来源行校验）
    import_warnings: list[str] = Field(default_factory=list)
    source_coverage: dict[str, Any] | None = None
    # 本次解析实际使用的业务场景键（选择场景知识包）；自定义场景通道下为 None
    scenario: str | None = None
    # 本次实际参与解析的 Skill 名称（按合并顺序，含系统预设）
    applied_skills: list[str] | None = None


class RuleOut(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_set_id: UUID
    rule_no: int
    rule_code: str
    status: str
    confidence: float | None = None
    defects: list[DefectItem] = Field(default_factory=list)
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    updated_at: datetime
    created_at: datetime


# pydantic v2 + `from __future__ import annotations`：路由请求体 TypeAdapter 需要
# 在导入期完成前向引用解析，否则首次请求报 "not fully defined"
for _rule_model in (RuleImportRequest, RuleImportResponse, ConflictReport, RuleCreate, RuleUpdate, RuleOut):
    _rule_model.model_rebuild()


class RuleBatchConfirmRequest(BaseModel):
    """批量确认规则。ids 不传则确认该规则集下所有 pending 规则。"""
    ids: list[UUID] | None = None


class ConflictItem(BaseModel):
    """规则间冲突项。"""
    rule_ids: list[str] = Field(description="参与冲突的规则 ID 列表")
    rule_codes: list[str] = Field(default_factory=list, description="参与冲突的规则流水号列表")
    type: str = Field(description="冲突类型")
    severity: str = Field(description="严重程度")
    description: str = Field(description="冲突描述")


class ConflictDetectionResponse(BaseModel):
    """冲突检测响应。"""
    total_conflicts: int = 0
    affected_rules: int = 0
    conflicts: list[ConflictItem] = Field(default_factory=list)
    # 检测完成度：只要有一组失败，就不把"没检出冲突"当成"冲突已消除"
    total_groups: int = 0
    failed_groups: int = 0
    detection_incomplete: bool = False
    cleared_rules: int = 0
    warnings: list[str] = Field(default_factory=list)


class RuleImportRunError(BaseModel):
    """一条 LLM 没能解析/入库的规则记录。"""

    index: int
    reason: str


class RuleImportRunOut(BaseModel):
    """一次规则导入的持久记录（供"导入记录"面板复盘 LLM 判不了的部分）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_set_id: UUID
    file_name: str = ""
    status: str
    message: str = ""
    total_chunks: int = 0
    parsed_chunks: int = 0
    total_rules: int = 0
    imported_rules: int = 0
    import_errors: int = 0
    conflict_found: int = 0
    errors: list[RuleImportRunError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
