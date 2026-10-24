"""租户级 ORM 查询与写入防护。

租户是安全边界。业务查询应显式使用 ``tenant_select`` 或 ``tenant_get``；
此处注册的 SQLAlchemy 事件是第二道防线：当会话绑定了租户时，所有继承
``TenantOwnedMixin`` 的查询自动附加租户条件，写入时自动回填或拒绝越权数据。
"""

from __future__ import annotations

import uuid

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, with_loader_criteria

from .models.mixins import TenantOwnedMixin

TENANT_SCOPE_KEY = "tenant_id"


class TenantAwareSession(Session):
    """会话基类：为继承租户基类的对象强制租户条件，覆盖默认 ``get``。"""

    def get(self, entity, ident, *, options=None, populate_existing=False, with_for_update=None, identity_token=None):
        tenant_id = self.info.get(TENANT_SCOPE_KEY)
        if tenant_id is not None and isinstance(entity, type) and issubclass(entity, TenantOwnedMixin):
            primary_key = sa_inspect(entity).primary_key[0]
            stmt = select(entity).where(
                primary_key == ident,
                entity.tenant_id == tenant_id,
            )
            if options:
                stmt = stmt.options(*options)
            if with_for_update is not None:
                stmt = stmt.with_for_update(with_for_update)
            if populate_existing:
                stmt = stmt.execution_options(populate_existing=True)
            return self.execute(stmt).scalars().first()
        return super().get(
            entity,
            ident,
            options=options,
            populate_existing=populate_existing,
            with_for_update=with_for_update,
            identity_token=identity_token,
        )


def bind_tenant(db: Session, tenant_id: uuid.UUID | None) -> None:
    """把当前会话绑定到指定租户；传 None 清除绑定。"""
    if tenant_id is None:
        db.info.pop(TENANT_SCOPE_KEY, None)
    else:
        db.info[TENANT_SCOPE_KEY] = tenant_id


def current_tenant_id(db: Session) -> uuid.UUID | None:
    return db.info.get(TENANT_SCOPE_KEY)


def tenant_select(model, tenant_id: uuid.UUID):
    return select(model).where(model.tenant_id == tenant_id)


def tenant_get(db: Session, model, object_id: uuid.UUID, tenant_id: uuid.UUID):
    return db.execute(
        select(model).where(
            model.id == object_id,
            model.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()


def _entities_with_tenant(statement):
    entities: list[type] = []
    seen: set[int] = set()
    for desc in statement.column_descriptions:
        entity = desc.get("entity")
        if entity is None or id(entity) in seen:
            continue
        seen.add(id(entity))
        if isinstance(entity, type) and issubclass(entity, TenantOwnedMixin):
            entities.append(entity)
    return entities


@event.listens_for(Session, "do_orm_execute")
def _filter_tenant_owned_queries(orm_execute_state):
    if not orm_execute_state.is_select:
        return

    tenant_id = orm_execute_state.session.info.get(TENANT_SCOPE_KEY)
    if tenant_id is None:
        return

    statement = orm_execute_state.statement
    for entity in _entities_with_tenant(statement):
        statement = statement.options(
            with_loader_criteria(
                entity,
                lambda cls: cls.tenant_id == tenant_id,
                include_aliases=True,
            )
        )
    orm_execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _enforce_tenant_owned_writes(session, flush_context, instances):
    tenant_id = session.info.get(TENANT_SCOPE_KEY)
    if tenant_id is None:
        return

    for obj in session.new:
        if not hasattr(obj, "tenant_id"):
            continue
        current = getattr(obj, "tenant_id", None)
        if current is None:
            setattr(obj, "tenant_id", tenant_id)
            continue
        if current != tenant_id:
            raise ValueError(
                "拒绝写入其他租户的数据："
                f"对象 tenant_id={current}，会话 tenant_id={tenant_id}"
            )
