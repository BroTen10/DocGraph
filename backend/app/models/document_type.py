"""文档类型表 ORM 模型。

替代 constants.py 中的硬编码文件类型清单，支持动态管理。
文档类型可来自：
1. 内置种子数据（初始化时从 constants.py 导入）
2. 规则导入时 LLM 新发现的类型
3. 用户通过前端手动创建
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class DocumentType(Base):
    """文档类型定义。"""

    __tablename__ = "document_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 文档类型名称（唯一），如"代理协议"
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # 简短描述
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关键字段提取模板
    key_fields: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 用印要求
    stamp_required: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # AI 分析的业务含义描述
    business_meaning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用户上传的样例文档路径
    sample_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 文档类型来源：seed / rule_import / manual
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    # 状态：active / pending_review / rejected
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentType {self.name} [{self.status}]>"
