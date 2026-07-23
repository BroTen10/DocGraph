"""文档表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Document(Base):
    """单个上传的业务文件。"""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # 文件格式：pdf / png / jpg / docx
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 业务类型：代理协议 / 委托单 / 报关单 / 运单 / 签收单 / 出入仓单 / 派车单 /
    # 收款水单 / 收汇认领 / 付款水单 / 付款申请 / 销售合同 / 装箱单 / 销售发票 /
    # 增值税发票 / 放行条 / 其他
    doc_type: Mapped[str] = mapped_column(String(64), default="其他", nullable=False)
    # 是否必备文件
    is_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    # OCR 状态：pending / done / failed / skipped
    ocr_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # 结构化提取字段（按文件类型模板）
    extracted_fields: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # OCR 整体置信度
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 印章判断结果：true / false / null
    has_stamp: Mapped[bool | None] = mapped_column(nullable=True)
    # 原始 OCR 文本（用于审查推理）
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 提取时间
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    contract: Mapped["Contract"] = relationship("Contract", back_populates="documents")
    review_results: Mapped[list["ReviewResult"]] = relationship(
        "ReviewResult", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document {self.file_name} [{self.doc_type}]>"
