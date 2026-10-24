"""ORM 模型共享的租户归属字段。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declared_attr, mapped_column


class TenantOwnedMixin:
    """带租户归属的业务表基类。

    子表通过 ``tenant_id`` 外键指向 ``tenants.id``。删除租户默认被拒绝，
    租户清理必须走显式业务流程，避免误删业务数据。
    """

    @declared_attr
    def tenant_id(cls):
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
