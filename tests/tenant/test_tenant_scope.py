import uuid

import pytest
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, engine
from app.database import SessionLocal
from app.models.mixins import TenantOwnedMixin
from app.tenant_scope import bind_tenant, current_tenant_id, tenant_get, tenant_select


class ScopedRecord(TenantOwnedMixin, Base):
    __tablename__ = "scoped_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture(autouse=True)
def create_scoped_table(initialized_database):
    Base.metadata.create_all(bind=engine)
    yield


def test_tenant_get_hides_other_tenant(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    row_a = ScopedRecord(tenant_id=tenant_a.id, name="A")
    row_b = ScopedRecord(tenant_id=tenant_b.id, name="B")
    migrated_db.add_all([row_a, row_b])
    migrated_db.commit()

    assert tenant_get(migrated_db, ScopedRecord, row_a.id, tenant_a.id).name == "A"
    assert tenant_get(migrated_db, ScopedRecord, row_b.id, tenant_a.id) is None
    assert list(
        migrated_db.execute(tenant_select(ScopedRecord, tenant_a.id)).scalars()
    ) == [row_a]


def test_session_get_is_tenant_scoped(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    row_a = ScopedRecord(tenant_id=tenant_a.id, name="A")
    row_b = ScopedRecord(tenant_id=tenant_b.id, name="B")
    migrated_db.add_all([row_a, row_b])
    migrated_db.commit()

    bind_tenant(migrated_db, tenant_a.id)
    try:
        migrated_db.expire_all()
        assert migrated_db.get(ScopedRecord, row_a.id) is not None
        assert migrated_db.get(ScopedRecord, row_b.id) is None
    finally:
        migrated_db.info.pop("tenant_id", None)


def test_before_flush_assigns_or_blocks_tenant(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    bind_tenant(migrated_db, tenant_a.id)
    try:
        assigned = ScopedRecord(name="assigned")
        migrated_db.add(assigned)
        migrated_db.flush()
        assert assigned.tenant_id == tenant_a.id

        conflict = ScopedRecord(tenant_id=tenant_b.id, name="conflict")
        migrated_db.add(conflict)
        with pytest.raises(ValueError, match="租户"):
            migrated_db.flush()
        migrated_db.rollback()
    finally:
        migrated_db.info.pop("tenant_id", None)


def test_authenticated_context_binds_request_session_to_tenant(admin_auth):
    from starlette.requests import Request
    from app.auth.dependencies import get_auth_context

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/contracts",
            "headers": [
                (
                    b"cookie",
                    f"docgraph_session={admin_auth.session_token}".encode("ascii"),
                )
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    with SessionLocal() as db:
        ctx = get_auth_context(request, db)
        assert current_tenant_id(db) == admin_auth.tenant_id
        assert ctx.tenant.id == admin_auth.tenant_id
