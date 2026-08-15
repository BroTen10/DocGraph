"""合同与文档相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocTypeUpdate(BaseModel):
    """修正文件业务类型。"""
    doc_type: str


class OcrFieldsUpdate(BaseModel):
    """人工修正 OCR 结构化字段（OCR 对照界面保存）。"""
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    has_stamp: bool | None = None


class DocumentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_type: str
    doc_type: str
    is_required: bool
    ocr_status: str
    ocr_confidence: float | None = None
    has_stamp: bool | None = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    # OCR 识别的原始文本（用于前端对照查看）
    ocr_text: str | None = None
    # 字段提取时间
    extracted_at: datetime | None = None


class ContractBrief(BaseModel):
    """合同列表项。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_set_id: UUID
    contract_no: str
    alias_list: list[str] = Field(default_factory=list)
    upload_time: datetime
    status: str
    file_count: int = 0


class ContractDetail(ContractBrief):
    """合同详情（含文件清单）。"""
    documents: list[DocumentBrief] = Field(default_factory=list)
    note: str | None = None


class ContractUploadResponse(BaseModel):
    """文件夹上传响应。"""
    contract_id: UUID
    contract_no: str
    alias_list: list[str] = Field(default_factory=list)
    file_count: int
    classified: list[dict] = Field(default_factory=list)
    message: str = "上传成功"


class ContractAliasUpdate(BaseModel):
    """修正合同号归一化结果。"""
    contract_no: str
    alias_list: list[str] = Field(default_factory=list)
