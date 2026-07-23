"""合同表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Contract(Base):
    """合同记录（一个合同号 ↔ 一个文件夹 ↔ N 个文件）。"""

    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 归一化后的主合同号
    contract_no: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    # 合同号别名列表（分批号、外贸号等）
    alias_list: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 上传时间
    upload_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # 审查状态：uploaded / reviewing / reviewed / failed
    status: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False)
    # 备注信息
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="contract", cascade="all, delete-orphan"
    )
    review_tasks: Mapped[list["ReviewTask"]] = relationship(
        "ReviewTask", back_populates="contract", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Contract {self.contract_no}>"
