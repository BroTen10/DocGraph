"""系统设置表 ORM 模型（批次 11）。

存储可运维的运行时配置：提示词模板 + 少量运行参数。
value 统一 JSONB 存储（提示词为字符串，参数为数值/布尔）。
未写入该表的 key 使用内置默认值（prompt_templates / config）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class SystemSetting(Base):
    """一条系统设置（key 唯一）。"""

    __tablename__ = "system_settings"

    # 设置键，如 ocr.image.system / review.amount_tolerance_percent
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 设置值（JSONB：字符串/数值/布尔/数组）
    value: Mapped[object] = mapped_column(JSONB, nullable=False)
    # 展示分组（OCR 识别 / 规则解析 / 审查与建议 / 文档类型 / 图谱 / 运行参数）
    group: Mapped[str] = mapped_column(String(64), default="其他", nullable=False)
    # 展示名称
    label: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # 说明
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 值类型：text / number / boolean
    kind: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SystemSetting {self.key} [{self.group}]>"
