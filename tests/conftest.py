import os
import sys
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest


_TEST_DSN = os.environ.get("TEST_DATABASE_URL", "")
_DATABASE_NAME = urlparse(_TEST_DSN).path.lstrip("/")
if not _DATABASE_NAME.endswith("_test"):
    pytest.exit(
        "TEST_DATABASE_URL 必须指向以 _test 结尾的独立数据库",
        returncode=2,
    )

os.environ["DATABASE_URL"] = _TEST_DSN
os.environ["APP_DEBUG"] = "true"
os.environ["APP_SECRET_KEY"] = "test-only-secret-key-" + "x" * 48
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["EMAIL_BACKEND"] = "outbox"
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))
os.chdir(_BACKEND_DIR)


@pytest.fixture
def initialized_database() -> None:
    from app import models  # noqa: F401
    from sqlalchemy import text

    from app.database import engine, init_db

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    init_db()


@pytest.fixture
def migrated_db(initialized_database) -> Generator:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def db(initialized_database) -> Generator:
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(initialized_database) -> Generator:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@dataclass(frozen=True)
class AuthFixture:
    session_token: str
    csrf_token: str
    user_id: UUID
    tenant_id: UUID | None
    membership_id: UUID | None
    email: str


def authenticate(client, auth: AuthFixture) -> dict[str, str]:
    client.cookies.clear()
    client.cookies.set("docgraph_session", auth.session_token)
    return {"X-CSRF-Token": auth.csrf_token}


@pytest.fixture
def make_auth_fixture(migrated_db):
    from app.auth.security import hash_password, new_token
    from app.models import AuthSession, TenantMembership, UserAccount

    def factory(tenant_id, role, email, membership=None):
        phone_suffix = uuid4().int % 100_000_000
        user = UserAccount(
            name=f"{role}测试用户",
            email_normalized=email,
            phone_e164=f"+86138{phone_suffix:08d}",
            password_hash=hash_password("DocGraph2026"),
            email_verified_at=datetime.utcnow(),
        )
        migrated_db.add(user)
        migrated_db.flush()
        if membership is None:
            membership = TenantMembership(
                tenant_id=tenant_id,
                user_id=user.id,
                role=role,
                status="active",
            )
            migrated_db.add(membership)
            migrated_db.flush()
        raw_session, session_hash = new_token()
        raw_csrf, csrf_hash = new_token()
        migrated_db.add(
            AuthSession(
                user_id=user.id,
                tenant_id=tenant_id,
                token_hash=session_hash,
                csrf_hash=csrf_hash,
                expires_at=datetime.utcnow() + timedelta(hours=12),
                idle_expires_at=datetime.utcnow() + timedelta(hours=2),
            )
        )
        migrated_db.commit()
        return AuthFixture(
            session_token=raw_session,
            csrf_token=raw_csrf,
            user_id=user.id,
            tenant_id=tenant_id,
            membership_id=membership.id,
            email=email,
        )

    return factory


@pytest.fixture
def two_tenants(migrated_db):
    from app.models import Tenant

    tenant_a = Tenant(name="租户 A", status="active")
    tenant_b = Tenant(name="租户 B", status="active")
    migrated_db.add_all([tenant_a, tenant_b])
    migrated_db.commit()
    return tenant_a, tenant_b


@pytest.fixture
def admin_auth(two_tenants, make_auth_fixture):
    return make_auth_fixture(two_tenants[0].id, "tenant_admin", "admin-a@example.com")


@pytest.fixture
def admin_b(two_tenants, make_auth_fixture):
    return make_auth_fixture(two_tenants[1].id, "tenant_admin", "admin-b@example.com")


@pytest.fixture
def reviewer_auth(two_tenants, make_auth_fixture):
    return make_auth_fixture(two_tenants[0].id, "reviewer", "reviewer-a@example.com")


@pytest.fixture
def viewer_auth(two_tenants, make_auth_fixture):
    return make_auth_fixture(two_tenants[0].id, "viewer", "viewer-a@example.com")


@pytest.fixture
def active_tenant_user(migrated_db):
    from app.auth.security import hash_password
    from app.models import Tenant, TenantMembership, UserAccount

    tenant = Tenant(name="登录测试租户", status="active")
    user = UserAccount(
        name="登录管理员",
        email_normalized="admin@example.com",
        phone_e164="+8613600136000",
        password_hash=hash_password("DocGraph2026"),
        email_verified_at=datetime.utcnow(),
    )
    migrated_db.add_all([tenant, user])
    migrated_db.flush()
    migrated_db.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role="tenant_admin",
            status="active",
        )
    )
    migrated_db.commit()
    return user
