# 登录、权限与多租户隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有文档审查链路的前提下，交付邮箱注册审核、手机号登记、服务端会话、固定角色权限和完整租户数据隔离。

**Architecture:** 在现有 FastAPI 单体中增加独立认证模块和租户上下文，以服务端可撤销会话作为身份来源。租户 ID 写入全部业务表，业务服务显式接收 `tenant_id`，对象级查询统一使用租户条件；上传文件、后台任务和 Neo4j 图谱分别增加租户边界。前端通过 `/api/auth/me` 驱动路由和菜单，同时后端独立执行 API 权限校验。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 16、Neo4j 5.26、pytest、React 18、TypeScript、Vite、Ant Design、Vitest、Testing Library。

**Spec:** `docs/登录权限与多租户隔离-需求设计.md`

## Global Constraints

- 租户是安全边界；规则集只是租户内部业务分组。
- 角色固定为 `tenant_admin`、`reviewer`、`viewer`、`platform_admin`。
- 邮箱统一去空格并转小写，作为全局唯一登录名。
- 手机号接受 `+86`、空格和短横线输入，规范化后必须是 `+861[3-9]\d{9}`。
- 密码长度为 10-64 个字符，至少包含一个字母和一个数字，使用 Argon2id 哈希。
- 会话为空闲 2 小时、绝对 12 小时；连续失败 5 次锁定 15 分钟。
- 会话令牌不得写入 `localStorage`；Cookie 在生产环境必须为 `HttpOnly`、`Secure`、`SameSite=Lax`。
- 新租户注册必须完成邮箱验证并由平台管理员审核。
- 已有租户成员由租户管理员邀请，不接受用户自行加入组织。
- 系统设置与成员管理只允许租户管理员访问。
- 业务 API 的租户范围只能来自服务端会话和成员关系，不接受客户端 `tenant_id` 参数。
- 跨租户对象访问统一返回 `404`；已登录但角色不足返回 `403`。
- 普通业务对象 `tenant_id` 非空；仅系统内置模板和平台级配置允许显式为空。
- 原审查链路必须保持通过；禁止使用 `DB_RESET_ON_STARTUP=true` 完成升级。
- 每项任务采用测试先行，完成后提交一次独立 commit。

---

## 文件结构

### 后端新增

| 路径 | 职责 |
| --- | --- |
| `backend/app/auth/security.py` | 邮箱、手机号、密码、随机令牌与哈希工具 |
| `backend/app/auth/dependencies.py` | 当前会话、租户上下文、权限依赖 |
| `backend/app/auth/permissions.py` | 角色到权限点的固定映射 |
| `backend/app/auth/rate_limit.py` | 登录、注册、重置密码限流与账号锁定辅助 |
| `backend/app/auth/csrf.py` | 写请求 Origin 与 CSRF 校验 |
| `backend/app/models/identity.py` | 租户、账号、成员、注册申请和邀请 |
| `backend/app/models/auth_session.py` | 会话与一次性令牌 |
| `backend/app/models/audit_log.py` | 追加式审计记录 |
| `backend/app/models/mixins.py` | `TenantOwnedMixin` |
| `backend/app/migrations/security_v1.py` | 租户字段、约束和存量数据迁移 |
| `backend/app/services/auth_service.py` | 注册、验证、登录、会话和密码流程 |
| `backend/app/services/email_service.py` | SMTP/outbox/console 邮件适配 |
| `backend/app/services/tenant_member_service.py` | 邀请、成员状态和角色管理 |
| `backend/app/services/platform_service.py` | 平台审核、租户状态和初始化 |
| `backend/app/services/audit_service.py` | 统一审计写入 |
| `backend/app/schemas/auth.py` | 认证请求与响应 |
| `backend/app/schemas/platform.py` | 平台审核接口 |
| `backend/app/schemas/tenant.py` | 成员与邀请接口 |
| `backend/app/routers/auth.py` | `/api/auth/*` |
| `backend/app/routers/platform.py` | `/api/platform/*` |
| `backend/app/routers/tenant_members.py` | `/api/tenant/members/*` |
| `backend/app/bootstrap.py` | 一次性平台管理员初始化命令 |
| `backend/app/tenant_scope.py` | ORM 查询租户过滤与写入默认值保护 |

### 前端新增

| 路径 | 职责 |
| --- | --- |
| `frontend/src/auth/AuthContext.tsx` | 当前用户、权限、CSRF 和会话生命周期 |
| `frontend/src/auth/permissions.ts` | 权限判断与路由准入 |
| `frontend/src/auth/validation.ts` | 邮箱、手机号和密码前端校验 |
| `frontend/src/auth/RequireAuth.tsx` | 登录和权限路由守卫 |
| `frontend/src/layouts/AppLayout.tsx` | 原租户业务主布局与动态菜单 |
| `frontend/src/layouts/PlatformLayout.tsx` | 平台管理员布局 |
| `frontend/src/pages/auth/LoginPage.tsx` | 登录 |
| `frontend/src/pages/auth/RegisterPage.tsx` | 新租户注册 |
| `frontend/src/pages/auth/PendingPage.tsx` | 审核状态提示 |
| `frontend/src/pages/auth/ForgotPasswordPage.tsx` | 发起重置密码 |
| `frontend/src/pages/auth/ResetPasswordPage.tsx` | 设置新密码 |
| `frontend/src/pages/auth/AcceptInvitePage.tsx` | 接受租户邀请 |
| `frontend/src/pages/ForbiddenPage.tsx` | 无权限页 |
| `frontend/src/pages/platform/RegistrationReviewPage.tsx` | 平台注册审核 |
| `frontend/src/pages/platform/TenantListPage.tsx` | 平台租户状态管理 |
| `frontend/src/pages/settings/MembersTab.tsx` | 用户与权限 |
| `frontend/src/pages/settings/RuntimeSettingsTab.tsx` | 从现有设置页拆出的运行设置 |

### 后端测试

新增 `tests/auth/`、`tests/tenant/` 和 `tests/acceptance_security.py`。现有图谱、OCR、审查测试同步改造为显式默认租户上下文。

---

## Task 1: 建立可重复的后端测试环境与安全配置

**Files:**
- Create: `backend/requirements-dev.txt`
- Create: `backend/pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/test_config_security.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`

**Interfaces:**
- Consumes: 现有 `Settings`、`SessionLocal` 和 FastAPI `app`
- Produces: `Settings.database_url: str | None`、`Settings.app_secret_key: str`、`client`、`db`、`migrated_db` pytest fixtures

- [ ] **Step 1: 写配置安全失败测试**

```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_secret_key():
    with pytest.raises(ValidationError):
        Settings(app_debug=False, app_secret_key="")


def test_production_requires_secure_cookie():
    with pytest.raises(ValidationError):
        Settings(app_debug=False, app_secret_key="x" * 64, session_cookie_secure=False)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\test_config_security.py -v`

Expected: FAIL，因为配置字段和校验尚未实现。

- [ ] **Step 3: 增加开发依赖和安全配置**

`backend/requirements-dev.txt` 写入：

```text
pytest==8.3.4
pytest-cov==6.0.0
```

在 `backend/app/config.py` 增加字段与校验：

```python
from pydantic import Field, model_validator

app_secret_key: str = ""
session_cookie_secure: bool = True
session_idle_minutes: int = Field(default=120, ge=5, le=1440)
session_absolute_hours: int = Field(default=12, ge=1, le=168)
database_url: str | None = None
email_backend: str = "smtp"
public_base_url: str = "http://localhost:5173"
trust_proxy_headers: bool = False

@model_validator(mode="after")
def _validate_security(self):
    if not self.app_debug and len(self.app_secret_key) < 32:
        raise ValueError("生产环境 APP_SECRET_KEY 至少 32 个字符")
    if not self.app_debug and not self.session_cookie_secure:
        raise ValueError("生产环境必须启用 Secure Cookie")
    return self

@property
def sqlalchemy_url(self) -> str:
    return self.database_url or self.pg_dsn
```

将 `backend/app/database.py` 的
`create_engine(settings.pg_dsn, pool_pre_ping=True, pool_size=5, max_overflow=10, echo=False)`
改为
`create_engine(settings.sqlalchemy_url, pool_pre_ping=True, pool_size=5, max_overflow=10, echo=False)`。

`tests/conftest.py` 必须在导入任何 `app.*` 模块前验证测试库名并设置环境变量，避免误连生产库：

```python
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest


_TEST_DSN = os.environ.get("TEST_DATABASE_URL", "")
_DATABASE_NAME = urlparse(_TEST_DSN).path.lstrip("/")
if not _DATABASE_NAME.endswith("_test"):
    pytest.exit("TEST_DATABASE_URL 必须指向以 _test 结尾的独立数据库", returncode=2)

os.environ["DATABASE_URL"] = _TEST_DSN
os.environ["APP_DEBUG"] = "false"
os.environ["APP_SECRET_KEY"] = "test-only-secret-key-" + "x" * 48
os.environ["EMAIL_BACKEND"] = "outbox"
os.chdir(Path(__file__).resolve().parents[1] / "backend")


@pytest.fixture
def initialized_database():
    from app import models  # noqa: F401
    from app.database import Base, engine, init_db

    Base.metadata.drop_all(bind=engine)
    init_db()


@pytest.fixture
def migrated_db(initialized_database):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def client(initialized_database):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
```

- [ ] **Step 4: 运行测试并验证通过**

先创建测试库：

```powershell
docker compose exec postgres createdb -U postgres docgraph_test
$env:TEST_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:15432/docgraph_test"
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
backend\.venv\Scripts\python.exe -m pytest tests\test_config_security.py -v
```

Expected: PASS。

- [ ] **Step 5: 更新环境变量样例并提交**

在 `backend/.env.example` 增加 `APP_SECRET_KEY=`、`SESSION_COOKIE_SECURE=true`、`SESSION_IDLE_MINUTES=120`、`SESSION_ABSOLUTE_HOURS=12`、`EMAIL_BACKEND=smtp`、`PUBLIC_BASE_URL=http://localhost:5173`、`TRUST_PROXY_HEADERS=false`。

```powershell
git add backend/app/config.py backend/app/database.py backend/requirements-dev.txt backend/pytest.ini backend/.env.example tests/conftest.py tests/test_config_security.py
git commit -m "test: establish secure backend test foundation"
```

---

## Task 2: 实现邮箱、手机号、密码和一次性令牌安全原语

**Files:**
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/security.py`
- Create: `tests/auth/test_security.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: `Settings.app_secret_key`
- Produces: `normalize_email(value: str) -> str`、`normalize_cn_phone(value: str) -> str`、`validate_password(value: str) -> None`、`hash_password(value: str) -> str`、`verify_password(hash_value: str, value: str) -> bool`、`new_token() -> tuple[str, str]`

- [ ] **Step 1: 写安全原语失败测试**

```python
import pytest

from app.auth.security import (
    hash_password,
    new_token,
    normalize_cn_phone,
    normalize_email,
    validate_password,
    verify_password,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13800138000", "+8613800138000"),
        ("138 0013 8000", "+8613800138000"),
        ("138-0013-8000", "+8613800138000"),
        ("+8613800138000", "+8613800138000"),
    ],
)
def test_normalize_cn_phone(raw, expected):
    assert normalize_cn_phone(raw) == expected


def test_rejects_lan_line():
    with pytest.raises(ValueError, match="中国大陆手机号"):
        normalize_cn_phone("010-12345678")


def test_password_hash_round_trip():
    digest = hash_password("DocGraph2026")
    assert digest != "DocGraph2026"
    assert verify_password(digest, "DocGraph2026")
    assert not verify_password(digest, "wrong-password")


def test_mail_and_token_normalization():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    raw, digest = new_token()
    assert len(raw) >= 40
    assert digest != raw


@pytest.mark.parametrize("password", ["short1", "1234567890", "abcdefghij"])
def test_rejects_weak_password(password):
    with pytest.raises(ValueError):
        validate_password(password)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_security.py -v`

Expected: FAIL，`app.auth.security` 不存在。

- [ ] **Step 3: 实现安全原语**

在 `backend/requirements.txt` 增加：

```text
pwdlib[argon2]==0.2.1
email-validator==2.2.0
```

实现关键逻辑：

```python
import hashlib
import re
import secrets
from email.utils import parseaddr

from pwdlib import PasswordHash

CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
password_hash = PasswordHash.recommended()


def normalize_cn_phone(value: str) -> str:
    compact = re.sub(r"[\s-]", "", value.strip())
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("86") and len(compact) == 13:
        compact = compact[2:]
    if not CN_MOBILE_RE.fullmatch(compact):
        raise ValueError("请输入有效的中国大陆手机号")
    return f"+86{compact}"


def normalize_email(value: str) -> str:
    candidate = parseaddr(value.strip())[1].lower()
    if len(candidate) > 254 or not EMAIL_RE.fullmatch(candidate):
        raise ValueError("请输入有效邮箱")
    return candidate


def validate_password(value: str) -> None:
    if not 10 <= len(value) <= 64 or not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("密码需为 10-64 位，并同时包含字母和数字")


def hash_password(value: str) -> str:
    validate_password(value)
    return password_hash.hash(value)


def verify_password(hash_value: str, value: str) -> bool:
    return password_hash.verify(value, hash_value)


def new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest
```

`verify_password` 必须捕获哈希格式错误并返回 `False`，避免登录接口因数据库脏值返回 500。

- [ ] **Step 4: 运行安全测试并确认通过**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_security.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/requirements.txt backend/app/auth tests/auth/test_security.py
git commit -m "feat: add authentication security primitives"
```

---

## Task 3: 建立租户、账号、成员、邀请、会话和审计模型

**Files:**
- Create: `backend/app/models/identity.py`
- Create: `backend/app/models/auth_session.py`
- Create: `backend/app/models/audit_log.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/database.py`
- Create: `tests/auth/test_identity_models.py`

**Interfaces:**
- Consumes: `Base`、`normalize_email`、`normalize_cn_phone`
- Produces: `Tenant`、`UserAccount`、`TenantMembership`、`TenantRegistrationRequest`、`TenantInvitation`、`AuthSession`、`AuthToken`、`AuditLog`

- [ ] **Step 1: 写模型失败测试**

```python
from uuid import uuid4

from app.models import (
    AuthSession,
    Tenant,
    TenantMembership,
    UserAccount,
)


def test_identity_objects_can_be_persisted(migrated_db):
    tenant = Tenant(name="甲公司", status="active")
    user = UserAccount(
        name="张三",
        email_normalized="zhang@example.com",
        phone_e164="+8613800138000",
        password_hash="argon2-hash",
    )
    migrated_db.add_all([tenant, user])
    migrated_db.flush()
    member = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role="tenant_admin",
        status="active",
    )
    migrated_db.add(member)
    migrated_db.commit()
    assert member.id


def test_membership_is_unique_per_tenant_and_user(migrated_db):
    tenant = Tenant(name="乙公司", status="active")
    user = UserAccount(
        name="李四",
        email_normalized="li@example.com",
        phone_e164="+8613900139000",
        password_hash="argon2-hash",
    )
    migrated_db.add_all([tenant, user])
    migrated_db.flush()
    migrated_db.add_all([
        TenantMembership(tenant_id=tenant.id, user_id=user.id, role="reviewer", status="active"),
        TenantMembership(tenant_id=tenant.id, user_id=user.id, role="viewer", status="active"),
    ])
    with pytest.raises(IntegrityError):
        migrated_db.commit()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_identity_models.py -v`

Expected: FAIL，模型不存在。

- [ ] **Step 3: 实现模型**

身份模型至少包含以下字段和约束：

```python
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
```

`TenantMembership` 使用 `(tenant_id, user_id)` 唯一约束，并保存 `role`、`status`、`invited_by`、`joined_at`。

`TenantRegistrationRequest` 保存申请资料、密码哈希、`status`、`email_verified_at`、审核人和驳回原因；在正式审核通过前不得创建 `UserAccount`。

`TenantInvitation` 保存 `tenant_id`、姓名、规范化邮箱、规范化手机号、角色、令牌哈希、`status`、`expires_at`、`invited_by`。

`AuthSession`、`AuthToken` 和 `AuditLog` 放入对应文件，关系外键均使用 `ondelete="CASCADE"` 或 `SET NULL`；`AuthToken.subject_id` 按 `purpose` 指向注册申请、账号或邀请。

在 `backend/app/database.py` 的模型导入区加入三个新模块，在 `backend/app/models/__init__.py` 导出全部新模型。

在 `tests/conftest.py` 增加统一的认证夹具，后续任务使用同一结构，避免把业务字段误写成 Cookie：

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


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
        user = UserAccount(
            name=f"{role}测试用户",
            email_normalized=email,
            phone_e164=f"+86138{int(tenant_id.int % 100000000):08d}",
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
```

- [ ] **Step 4: 运行模型测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_identity_models.py -v`

Expected: PASS，包括唯一约束测试。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/models backend/app/database.py tests/auth/test_identity_models.py
git commit -m "feat: add identity and audit data models"
```

---

## Task 4: 建立租户 ORM 防护层

**Files:**
- Create: `backend/app/models/mixins.py`
- Create: `backend/app/tenant_scope.py`
- Create: `tests/tenant/test_tenant_scope.py`
- Modify: `backend/app/database.py`

**Interfaces:**
- Consumes: `TenantOwned` 模型基类与 SQLAlchemy `Session`
- Produces: `TenantOwnedMixin`、`bind_tenant(db: Session, tenant_id: UUID) -> None`、`tenant_get(db: Session, model: type[T], object_id: UUID, tenant_id: UUID) -> T | None`、`tenant_select(model, tenant_id)`

- [ ] **Step 1: 写跨租户查询失败测试**

测试使用一个测试专用 `ScopedRecord` 模型，创建 A/B 两条记录后断言：

```python
def test_tenant_get_hides_other_tenant(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    row_a = ScopedRecord(tenant_id=tenant_a.id, name="A")
    row_b = ScopedRecord(tenant_id=tenant_b.id, name="B")
    migrated_db.add_all([row_a, row_b])
    migrated_db.commit()

    assert tenant_get(migrated_db, ScopedRecord, row_a.id, tenant_a.id).name == "A"
    assert tenant_get(migrated_db, ScopedRecord, row_b.id, tenant_a.id) is None
    assert list(migrated_db.execute(tenant_select(ScopedRecord, tenant_a.id)).scalars()) == [row_a]
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_tenant_scope.py -v`

Expected: FAIL，租户查询辅助尚不存在。

- [ ] **Step 3: 实现租户属性和查询辅助**

```python
class TenantOwnedMixin:
    @declared_attr
    def tenant_id(cls):
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )


def tenant_select(model, tenant_id: uuid.UUID):
    return select(model).where(model.tenant_id == tenant_id)


def tenant_get(db: Session, model, object_id: uuid.UUID, tenant_id: uuid.UUID):
    return db.execute(
        select(model).where(model.id == object_id, model.tenant_id == tenant_id)
    ).scalar_one_or_none()
```

`database.py` 注册 `do_orm_execute` 防御性过滤器：当 `db.info["tenant_id"]` 存在且查询对象继承 `TenantOwnedMixin` 时，附加 `with_loader_criteria`。另注册 `before_flush`：

- 新建租户对象缺少 `tenant_id` 时自动取当前会话租户。
- 新建租户对象携带其他租户 ID 时抛出 `ValueError`。
- 不使用 `tenant_select` 的原始 SQL 仍由代码审查和跨租户验收测试覆盖。

- [ ] **Step 4: 验证 ORM 过滤和写入保护**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_tenant_scope.py -v`

Expected: 查询隔离、跨租户 `db.get` 和错误写入均 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/models/mixins.py backend/app/tenant_scope.py backend/app/database.py tests/tenant/test_tenant_scope.py
git commit -m "feat: enforce tenant scope in database access"
```

---

## Task 5: 实现新租户注册、邮箱验证和邮件适配

**Files:**
- Create: `backend/app/services/email_service.py`
- Create: `backend/app/services/auth_service.py`
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/routers/auth.py`
- Create: `tests/auth/test_registration.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: 安全原语、身份模型、`Settings`
- Produces: `register_tenant(payload) -> TenantRegistrationRequest`、`verify_registration_token(raw_token) -> str`、`POST /api/auth/register`、`POST /api/auth/verify-email`

- [ ] **Step 1: 写注册流程失败测试**

```python
def test_register_then_verify_enters_review_queue(client, monkeypatch):
    delivered = {}

    def capture(email, url):
        delivered["email"] = email
        delivered["url"] = url

    monkeypatch.setattr("app.services.auth_service.send_verification_email", capture)
    response = client.post("/api/auth/register", json={
        "organization_name": "测试贸易公司",
        "admin_name": "张三",
        "email": "Zhang@Example.com",
        "phone": "+86 138-0013-8000",
        "password": "DocGraph2026",
        "accept_terms": True,
    })
    assert response.status_code == 202
    assert delivered["email"] == "zhang@example.com"
    token = delivered["url"].split("token=", 1)[1]
    verified = client.post("/api/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert verified.json()["status"] == "pending_review"


def test_invalid_phone_and_duplicate_email_are_rejected(client):
    assert client.post("/api/auth/register", json={
        "organization_name": "测试公司",
        "admin_name": "李四",
        "email": "li@example.com",
        "phone": "010-12345678",
        "password": "DocGraph2026",
        "accept_terms": True,
    }).status_code == 422
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_registration.py -v`

Expected: FAIL，路由返回 404。

- [ ] **Step 3: 实现注册和验证**

`RegisterTenantRequest` 使用 `EmailStr`、字段长度约束和 `field_validator` 调用安全原语。`register_tenant` 必须：

1. 在事务中创建 `TenantRegistrationRequest(status="pending_email")`。
2. 创建 24 小时有效的 `AuthToken(purpose="verify_registration")`。
3. 发送 `f"{settings.public_base_url}/verify-email?token={raw_token}"`。
4. 对已存在邮箱或手机号返回通用 `202` 响应，不创建重复申请。

`verify_registration_token` 必须使用哈希查找、拒绝过期或已使用令牌，并在同一事务内把申请改为 `pending_review`。

`email_service.py` 提供三种后端：

- `smtp`：通过标准库 `smtplib` 发送。
- `console`：仅开发环境把验证链接写入 warning 日志。
- `outbox`：测试环境保存到进程内列表，供测试断言。

在 `backend/app/main.py` 注册 `auth.router`。本任务只开放注册与验证接口。

- [ ] **Step 4: 验证注册流程**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_registration.py -v`

Expected: PASS；非法手机号返回 `422`，重复申请不产生重复行。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/services/email_service.py backend/app/services/auth_service.py backend/app/schemas/auth.py backend/app/routers/auth.py backend/app/main.py tests/auth/test_registration.py
git commit -m "feat: add tenant registration and email verification"
```

---

## Task 6: 实现登录、会话、退出和密码重置

**Files:**
- Create: `backend/app/auth/session_tokens.py`
- Modify: `backend/app/services/auth_service.py`
- Modify: `backend/app/routers/auth.py`
- Modify: `backend/app/schemas/auth.py`
- Create: `tests/auth/test_login_sessions.py`

**Interfaces:**
- Consumes: `AuthSession`、`AuthToken`、`UserAccount`、`TenantMembership`
- Produces: `create_session(db, user, tenant_id, ip, user_agent) -> tuple[AuthSession, str]`、`authenticate(db, email, password, ip, user_agent) -> AuthSession`、`revoke_session(db, session_id)`、`POST /api/auth/login|logout|password/*`、`GET /api/auth/me`

- [ ] **Step 1: 写登录和会话失败测试**

在 `tests/conftest.py` 增加登录用账号夹具：

```python
@pytest.fixture
def active_tenant_user(migrated_db):
    from app.auth.security import hash_password
    from app.models import Tenant, TenantMembership, UserAccount
    from datetime import datetime

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
    migrated_db.add(TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role="tenant_admin",
        status="active",
    ))
    migrated_db.commit()
    return user
```

```python
def test_login_me_logout(client, active_tenant_user):
    response = client.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "DocGraph2026",
    })
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "tenant_admin"
    assert response.cookies.get("docgraph_session")
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_five_failures_lock_account(client, active_tenant_user):
    for _ in range(5):
        response = client.post("/api/auth/login", json={
            "email": "admin@example.com",
            "password": "WrongPassword1",
        })
        assert response.status_code == 401
    locked = client.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "DocGraph2026",
    })
    assert locked.status_code == 403
    assert locked.json()["code"] == "ACCOUNT_LOCKED"
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_login_sessions.py -v`

Expected: FAIL，登录接口不存在。

- [ ] **Step 3: 实现会话和登录**

登录事务必须：

1. 规范化邮箱并查找账号。
2. 检查 `locked_until`。
3. 校验密码；失败时原子增加 `failed_login_count`，达到 5 次时设置 `now()+15min`。
4. 检查 `email_verified_at`、平台管理员身份或唯一 `active` 成员关系及租户状态。
5. 成功后清零失败计数并更新时间。
6. 生成 32 字节随机会话令牌，数据库只存 SHA-256 哈希。
7. 返回 raw token 给路由，由路由写 Cookie。

Cookie 参数固定为：

```python
response.set_cookie(
    "docgraph_session",
    session_token,
    max_age=settings.session_absolute_hours * 3600,
    httponly=True,
    secure=settings.session_cookie_secure,
    samesite="lax",
    path="/",
)
```

`/auth/me` 返回当前用户 ID、姓名、邮箱、手机号脱敏值、租户 ID/名称、角色、权限清单和新的 CSRF token。CSRF token 由路由放在响应 JSON 中，前端仅保存在内存。

密码重置使用通用请求响应、一次性 `reset_password` 令牌和 30 分钟过期时间；重置成功后撤销该用户全部会话。

- [ ] **Step 4: 运行会话测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_login_sessions.py -v`

Expected: 登录、退出、锁定、重置密码和旧会话撤销全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/auth/session_tokens.py backend/app/services/auth_service.py backend/app/routers/auth.py backend/app/schemas/auth.py tests/auth/test_login_sessions.py
git commit -m "feat: add secure login and revocable sessions"
```

---

## Task 7: 实现固定角色权限、认证依赖、CSRF 和限流

**Files:**
- Create: `backend/app/auth/permissions.py`
- Create: `backend/app/auth/dependencies.py`
- Create: `backend/app/auth/csrf.py`
- Create: `backend/app/auth/rate_limit.py`
- Create: `tests/tenant/test_permissions.py`
- Create: `tests/auth/test_csrf_rate_limit.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: 会话 Cookie、`AuthSession`、`TenantMembership`
- Produces: `ROLE_PERMISSIONS`、`AuthContext`、`get_auth_context()`、`require_permission(name)`、`require_platform_admin`、CSRF middleware

- [ ] **Step 1: 写权限和 CSRF 失败测试**

```python
def test_viewer_cannot_update_settings(client, viewer_auth):
    headers = authenticate(client, viewer_auth)
    response = client.put(
        "/api/settings",
        json={"items": []},
        headers=headers,
    )
    assert response.status_code == 403


def test_write_without_csrf_is_rejected(client, admin_auth):
    authenticate(client, admin_auth)
    response = client.put("/api/settings", json={"items": []})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_INVALID"
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_permissions.py tests\auth\test_csrf_rate_limit.py -v`

Expected: FAIL，权限依赖和 CSRF 中间件不存在。

- [ ] **Step 3: 实现权限映射和请求上下文**

固定角色权限至少在代码中明确如下集合：

```python
ROLE_PERMISSIONS = {
    "tenant_admin": frozenset({
        "profile.read", "profile.update", "review.view", "review.execute",
        "rules.view", "rules.manage", "doc_types.view", "doc_types.manage",
        "graph.view", "graph.manage", "settings.read", "settings.update",
        "members.read", "members.manage", "audit.read",
    }),
    "reviewer": frozenset({
        "profile.read", "profile.update", "review.view", "review.execute",
        "rules.view", "rules.manage", "doc_types.view", "doc_types.manage",
        "graph.view", "graph.manage",
    }),
    "viewer": frozenset({
        "profile.read", "profile.update", "review.view",
        "rules.view", "doc_types.view", "graph.view",
    }),
}
```

平台管理员权限单独定义，不进入租户角色集合。

`AuthContext` 是不可变 dataclass，包含 `user`、`tenant`、`membership`、`session` 和 `permissions`。`get_auth_context` 从 Cookie 哈希查找会话，校验过期、撤销状态、账号状态、租户状态和成员状态；每次有效请求更新 `idle_expires_at`。

`require_permission(name)` 返回 FastAPI dependency，先调用 `get_auth_context`，再检查权限。

实现 CSRF：

- 所有 `POST/PUT/PATCH/DELETE` 要求 `X-CSRF-Token` 与会话保存的 CSRF 哈希匹配。
- 公开认证端点只校验 Origin，不要求已登录 CSRF。
- Origin 比对 `PUBLIC_BASE_URL` 的 scheme 和 host。

实现内存滑动窗口限流器：登录按 IP+邮箱 10 次/分钟，注册与找回密码按 IP 5 次/分钟。单进程部署使用线程锁保护计数，测试可注入时间函数。

- [ ] **Step 4: 运行权限、CSRF 和限流测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_permissions.py tests\auth\test_csrf_rate_limit.py -v`

Expected: PASS；viewer 无设置权限，缺少 CSRF 或 Origin 错误的写请求被拒绝。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/auth backend/app/main.py tests/tenant/test_permissions.py tests/auth/test_csrf_rate_limit.py
git commit -m "feat: enforce role permissions and csrf protection"
```

---

## Task 8: 实现租户管理员邀请与成员权限管理

**Files:**
- Create: `backend/app/services/tenant_member_service.py`
- Create: `backend/app/schemas/tenant.py`
- Create: `backend/app/routers/tenant_members.py`
- Create: `tests/tenant/test_members.py`
- Modify: `backend/app/routers/auth.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `AuthContext`、`require_permission("members.manage")`
- Produces: `invite_member()`、`accept_invitation()`、`list_members()`、`change_member_role()`、`set_member_status()`

- [ ] **Step 1: 写成员管理失败测试**

```python
def test_tenant_admin_invites_and_accepts_member(client, admin_auth, monkeypatch):
    headers = authenticate(client, admin_auth)
    delivered = {}
    monkeypatch.setattr(
        "app.services.tenant_member_service.send_invitation_email",
        lambda email, url: delivered.update(email=email, url=url),
    )
    invited = client.post(
        "/api/tenant/members/invite",
        json={
            "name": "王五",
            "email": "wang@example.com",
            "phone": "13900139000",
            "role": "reviewer",
        },
        headers=headers,
    )
    assert invited.status_code == 201
    raw_token = delivered["url"].split("token=", 1)[1]
    accepted = client.post("/api/auth/accept-invite", json={
        "token": raw_token,
        "password": "Reviewer2026",
    })
    assert accepted.status_code == 200


def test_last_admin_cannot_be_demoted(client, admin_auth):
    headers = authenticate(client, admin_auth)
    response = client.patch(
        f"/api/tenant/members/{admin_auth.membership_id}",
        json={"role": "reviewer"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "LAST_TENANT_ADMIN"
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_members.py -v`

Expected: FAIL，成员接口不存在。

- [ ] **Step 3: 实现邀请、接受和成员管理**

邀请必须在当前租户内创建 `TenantInvitation(status="invited")`，令牌 72 小时有效。接受邀请时在同一事务中：

1. 校验令牌和邀请状态。
2. 校验邮箱、手机号未被其他正式账号占用。
3. 创建 `UserAccount`。
4. 创建 `TenantMembership(status="active")`。
5. 标记邀请已接受、令牌已使用。

`PATCH /api/tenant/members/{membership_id}` 只允许在 `tenant_admin`、`reviewer`、`viewer` 中切换角色或启停。降级或停用最后一个有效 `tenant_admin` 返回 `409 LAST_TENANT_ADMIN`。被停用成员的全部会话立即撤销。

成员列表和角色变更查询必须包含当前租户条件；其他租户成员 ID 返回 `404`。

- [ ] **Step 4: 运行成员测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_members.py -v`

Expected: 邀请、接受、跨租户隐藏、最后管理员保护和会话撤销全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/services/tenant_member_service.py backend/app/schemas/tenant.py backend/app/routers/tenant_members.py backend/app/routers/auth.py backend/app/main.py tests/tenant/test_members.py
git commit -m "feat: add tenant member invitation and role management"
```

---

## Task 9: 实现平台管理员初始化、注册审核和租户状态管理

**Files:**
- Create: `backend/app/services/platform_service.py`
- Create: `backend/app/schemas/platform.py`
- Create: `backend/app/routers/platform.py`
- Create: `backend/app/bootstrap.py`
- Create: `tests/auth/test_platform_admin.py`
- Modify: `backend/app/main.py`
- Modify: `deploy.sh`

**Interfaces:**
- Consumes: `TenantRegistrationRequest`、`require_platform_admin`
- Produces: `create_platform_admin(email, password)`、`approve_registration()`、`reject_registration()`、`set_tenant_status()`、`python -m app.bootstrap`

- [ ] **Step 1: 写审核流程失败测试**

先在 `tests/conftest.py` 增加无租户成员关系的平台会话夹具：

```python
@pytest.fixture
def platform_auth(migrated_db):
    from app.auth.security import hash_password, new_token
    from app.models import AuthSession, UserAccount

    user = UserAccount(
        name="平台管理员",
        email_normalized="platform@example.com",
        phone_e164="+8613700137000",
        password_hash=hash_password("DocGraph2026"),
        email_verified_at=datetime.utcnow(),
        is_platform_admin=True,
    )
    migrated_db.add(user)
    migrated_db.flush()
    raw_session, session_hash = new_token()
    raw_csrf, csrf_hash = new_token()
    migrated_db.add(
        AuthSession(
            user_id=user.id,
            tenant_id=None,
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
        tenant_id=None,
        membership_id=None,
        email="platform@example.com",
    )
```

平台角色夹具中的 `tenant_id` 和 `membership_id` 为 `None`；租户角色夹具测试必须断言二者非空。

```python
def test_approval_creates_active_tenant_and_admin(client, verified_registration, platform_auth):
    headers = authenticate(client, platform_auth)
    response = client.post(
        f"/api/platform/registrations/{verified_registration.id}/approve",
        json={"note": "资料完整"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["tenant_status"] == "active"
    assert response.json()["admin_role"] == "tenant_admin"
    assert client.post("/api/auth/login", json={
        "email": "applicant@example.com",
        "password": "DocGraph2026",
    }).status_code == 200


def test_platform_admin_cannot_read_tenant_contracts(client, platform_auth):
    authenticate(client, platform_auth)
    assert client.get("/api/contracts").status_code == 403
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_platform_admin.py -v`

Expected: FAIL，平台路由与初始化命令不存在。

- [ ] **Step 3: 实现审核与初始化**

审核通过必须在一个数据库事务中完成：

```python
tenant = Tenant(name=request.organization_name, status="active")
db.add(tenant)
db.flush()
user = UserAccount(
    name=request.admin_name,
    email_normalized=request.email_normalized,
    phone_e164=request.phone_e164,
    password_hash=request.password_hash,
    email_verified_at=request.email_verified_at,
)
db.add(user)
db.flush()
membership = TenantMembership(
    tenant_id=tenant.id,
    user_id=user.id,
    role="tenant_admin",
    status="active",
)
db.add(membership)
request.status = "approved"
request.reviewed_by = ctx.user.id
request.reviewed_at = datetime.utcnow()
```

驳回时只更新申请状态、审核人和驳回原因，并向申请人发送结果邮件。平台管理员只能完成用户与租户“准入”动作，不能通过平台 API 读取合同正文、文档、审查结果或规则正文。

`bootstrap.py` 从 `BOOTSTRAP_ADMIN_EMAIL`、`BOOTSTRAP_ADMIN_PASSWORD` 读取一次性口令，要求密码合规、账号不存在，并在成功后打印“请立即修改初始密码”。无参数时拒绝运行。

- [ ] **Step 4: 验证平台流程**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\auth\test_platform_admin.py -v`

Expected: 审核通过、驳回、暂停租户、审核幂等和平台管理员业务访问拒绝均 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/services/platform_service.py backend/app/schemas/platform.py backend/app/routers/platform.py backend/app/bootstrap.py backend/app/main.py deploy.sh tests/auth/test_platform_admin.py
git commit -m "feat: add platform tenant approval workflow"
```

---

## Task 10: 给业务表增加租户字段并迁移存量数据

**Files:**
- Create: `backend/app/migrations/__init__.py`
- Create: `backend/app/migrations/security_v1.py`
- Modify: `backend/app/database.py`
- Modify: 所有 `backend/app/models/*.py` 业务模型
- Create: `tests/tenant/test_tenant_migration.py`

**Interfaces:**
- Consumes: `TenantOwnedMixin`、现有 PostgreSQL schema
- Produces: `apply_security_v1_migration(engine)`、默认租户、所有业务表 `tenant_id`、租户内唯一约束

- [ ] **Step 1: 写迁移失败测试**

```python
def test_all_business_rows_have_tenant_id(migrated_db):
    tenant_id = default_tenant_id(migrated_db)
    for model in TENANT_OWNED_MODELS:
        null_count = migrated_db.execute(
            select(func.count()).select_from(model).where(
                model.tenant_id.is_(None)
            )
        ).scalar_one()
        assert null_count == 0


def test_rule_set_name_is_unique_only_within_tenant(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    migrated_db.add_all([
        RuleSet(tenant_id=tenant_a.id, name="默认规则集"),
        RuleSet(tenant_id=tenant_b.id, name="默认规则集"),
    ])
    migrated_db.commit()
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_tenant_migration.py -v`

Expected: FAIL，业务表没有 `tenant_id`，迁移函数不存在。

- [ ] **Step 3: 添加模型字段**

以下模型继承 `TenantOwnedMixin`：

- `RuleSet`
- `Rule`
- `RuleSnapshot`
- `Contract`
- `Document`
- `OcrTask`
- `ReviewTask`
- `ReviewResult`
- `DocumentType`

`RuleParseSkill.tenant_id` 显式定义为可空外键：内置模板为空，租户副本非空。

`SystemSetting` 改成复合主键 `(tenant_id, key)`，其中 `tenant_id` 非空。

在父表增加 `(id, tenant_id)` 唯一约束，并在子表使用复合外键：

```python
ForeignKeyConstraint(
    ["rule_set_id", "tenant_id"],
    ["rule_sets.id", "rule_sets.tenant_id"],
    ondelete="CASCADE",
)
```

对应关系覆盖 `rules/snapshots/contracts -> rule_sets`、`documents -> contracts`、`ocr_tasks -> contracts`、`review_tasks -> contracts`、`review_results -> review_tasks`。

- [ ] **Step 4: 实现幂等迁移**

`apply_security_v1_migration(engine)` 按以下顺序执行：

1. 若不存在 `code="default"` 的租户则创建“默认租户”，状态为 `active`。
2. 对每张业务表执行 `ADD COLUMN IF NOT EXISTS tenant_id UUID`。
3. 按现有父子关系回填租户：`rules/snapshots/contracts -> rule_sets`，`documents/ocr_tasks/review_tasks -> contracts`，`review_results -> review_tasks`。
4. 将独立配置对象、文档类型和存量设置回填为默认租户。
5. 将业务表 `tenant_id` 设为 `NOT NULL`。
6. 删除 `rule_sets.name` 和 `document_types.name` 的原全局唯一约束。
7. 创建 `(tenant_id, name)` 唯一索引和 `(tenant_id) WHERE is_default` 部分唯一索引。
8. 创建 `(id, tenant_id)` 唯一索引和复合外键。
9. 检查孤儿行、空租户行和跨租户父子关系；发现异常时抛错并回滚。
10. 仅当默认租户还没有规则集时才创建一个“默认规则集”。

迁移 SQL 只增不删；旧文件路径暂不移动。迁移失败时应用启动失败，禁止继续提供 API。

- [ ] **Step 5: 验证迁移和提交**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_tenant_migration.py -v`

Expected: 首次迁移、重复迁移、同名跨租户、默认规则集和约束检查全部 PASS。

```powershell
git add backend/app/migrations backend/app/database.py backend/app/models tests/tenant/test_tenant_migration.py
git commit -m "feat: migrate business data to tenant boundaries"
```

---

## Task 11: 改造规则集、合同、文档和文件下载的租户隔离

**Files:**
- Modify: `backend/app/routers/rule_sets.py`
- Modify: `backend/app/services/contract_service.py`
- Modify: `backend/app/routers/contracts.py`
- Modify: `backend/app/services/doc_normalizer.py`
- Create: `tests/tenant/test_ruleset_contract_isolation.py`

**Interfaces:**
- Consumes: `AuthContext`、`tenant_get`、租户化模型
- Produces: `RuleSet` API 只返回本租户数据、`upload_contract_folder(db, tenant_id, rule_set_id, files)`、租户化上传目录

- [ ] **Step 1: 写规则集和文件跨租户失败测试**

```python
def test_rulesets_and_files_are_isolated(client, admin_auth, admin_b, file_a, contract_a):
    authenticate(client, admin_auth)
    own = client.get("/api/rule-sets")
    assert {item["id"] for item in own.json()} == {contract_a.rule_set_id}

    authenticate(client, admin_b)
    assert client.get(f"/api/contracts/{contract_a.id}").status_code == 404
    assert client.get(f"/api/contracts/documents/{file_a.id}/file").status_code == 404
    assert client.get(f"/api/rule-sets/{contract_a.rule_set_id}").status_code == 404
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_ruleset_contract_isolation.py -v`

Expected: FAIL，当前接口未接收认证上下文。

- [ ] **Step 3: 改造规则集和合同服务**

所有规则集查询使用 `tenant_select(RuleSet, ctx.tenant_id)`；名称冲突检查限定 `tenant_id`；设为默认时只清除本租户其他默认项。

合同服务签名改为：

```python
async def upload_contract_folder(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    files: list[UploadFile],
) -> ContractUploadResponse:
```

先通过 `tenant_get(db, RuleSet, rule_set_id, tenant_id)` 校验规则集。新文件目录固定为：

```python
def _contract_upload_dir(tenant_id: uuid.UUID, rule_set_id: uuid.UUID, contract_id: uuid.UUID) -> Path:
    path = settings.ensure_upload_root() / str(tenant_id) / str(rule_set_id) / str(contract_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
```

合同详情、删除、别名修改、文档类型修改、OCR 字段修改、文件下载和 OCR 读取都先执行 `tenant_get`。文件下载不得使用裸 `db.get(Document, doc_id)`。

文档类型归一函数必须增加 `tenant_id` 参数，防止一个租户的别名表影响另一个租户。

- [ ] **Step 4: 运行隔离测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_ruleset_contract_isolation.py -v`

Expected: A/B 列表、详情、更新、删除、下载全部隔离；同租户原链路正常。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/routers/rule_sets.py backend/app/routers/contracts.py backend/app/services/contract_service.py backend/app/services/doc_normalizer.py tests/tenant/test_ruleset_contract_isolation.py
git commit -m "feat: isolate rulesets contracts and files by tenant"
```

---

## Task 12: 改造文档类型、系统设置及其全部调用点

**Files:**
- Modify: `backend/app/routers/doc_types.py`
- Modify: `backend/app/routers/settings.py`
- Modify: `backend/app/services/settings_service.py`
- Modify: `backend/app/services/doc_normalizer.py`
- Modify: `backend/app/services/ocr_service.py`
- Modify: `backend/app/services/ocr_task_service.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/app/services/llm_review_service.py`
- Modify: `backend/app/services/graph_builder_service.py`
- Modify: `backend/app/services/rule_import_service.py`
- Modify: `backend/app/services/rule_conflict_detector.py`
- Modify: `backend/app/services/suggestion_service.py`
- Create: `tests/tenant/test_doc_types_settings_isolation.py`

**Interfaces:**
- Consumes: `AuthContext`、`require_permission("doc_types.manage")`、`require_permission("settings.update")`
- Produces: `get_setting(db, tenant_id, key, default)`、`get_prompt(db, tenant_id, key)`、`seed_tenant_defaults(db, tenant_id)`

- [ ] **Step 1: 写文档类型和设置跨租户失败测试**

```python
def test_same_doc_type_name_allowed_and_settings_isolated(client, admin_auth, admin_b):
    headers_a = authenticate(client, admin_auth)
    assert client.post(
        "/api/doc-types",
        json={"name": "自定义发票"},
        headers=headers_a,
    ).status_code == 201
    client.put(
        "/api/settings",
        json={"items": [{"key": "review.amount_tolerance_percent", "value": 7}]},
        headers=headers_a,
    )
    authenticate(client, admin_b)
    headers_b = {"X-CSRF-Token": admin_b.csrf_token}
    assert client.post(
        "/api/doc-types",
        json={"name": "自定义发票"},
        headers=headers_b,
    ).status_code == 201
    settings = client.get("/api/settings").json()["settings"]
    tolerance = next(item for item in settings if item["key"] == "review.amount_tolerance_percent")
    assert tolerance["value"] != 7
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_doc_types_settings_isolation.py -v`

Expected: FAIL，名称和设置仍为全局。

- [ ] **Step 3: 改造服务签名和查询**

`settings_service` 的目标接口：

```python
def get_setting(db: Session | None, tenant_id: uuid.UUID, key: str, default: Any = None) -> Any:
    if db is None:
        return default
    row = db.execute(
        select(SystemSetting).where(
            SystemSetting.tenant_id == tenant_id,
            SystemSetting.key == key,
        )
    ).scalar_one_or_none()
    return default if row is None else row.value


def get_prompt(db: Session, tenant_id: uuid.UUID, key: str) -> str:
    return str(get_setting(db, tenant_id, key, DEFAULT_PROMPTS[key]))
```

`get_all_settings` 与 `update_settings` 只处理当前租户覆盖值，返回代码内置默认值作为兜底。更新设置时记录 `updated_by` 审计。

文档类型路由全部使用 `ctx.tenant_id`：列表、详情、OCR profile、创建、更新、删除、确认、拒绝、样例分析和新类型检测。重名检查必须是 `(tenant_id, name)`。

新租户审核通过后调用 `seed_tenant_defaults`，复制内置文档类型和基础设置。内置 Skill 保持全局只读，租户编辑时复制为租户行。

执行以下命令找出全部设置和文档类型调用点并逐个传入 `tenant_id`：

```powershell
rg -n "get_setting\(|get_prompt\(|get_all_settings\(|update_settings\(" backend/app -g "*.py"
rg -n "normalize_doc_type\(|build_doc_type_alias_index\(|resolve_field_aliases\(" backend/app -g "*.py"
```

- [ ] **Step 4: 运行文档类型、设置与相关回归测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_doc_types_settings_isolation.py -v`

Run: `backend\.venv\Scripts\python.exe -m pytest tests\test_ocr_extract_profile.py tests\test_ocr_model.py -v`

Expected: 隔离测试和现有 OCR 配置测试全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/routers/doc_types.py backend/app/routers/settings.py backend/app/services backend/app/database.py tests/tenant/test_doc_types_settings_isolation.py
git commit -m "feat: scope document types and settings to tenants"
```

---

## Task 13: 改造规则、Skill、导入和冲突检测流程

**Files:**
- Modify: `backend/app/services/rule_service.py`
- Modify: `backend/app/services/rule_parse_skill_service.py`
- Modify: `backend/app/services/rule_parse_engine.py`
- Modify: `backend/app/services/rule_import_service.py`
- Modify: `backend/app/services/rule_conflict_detector.py`
- Modify: `backend/app/services/rule_import_task.py`
- Modify: `backend/app/routers/rules.py`
- Modify: `backend/app/routers/rule_parse_skills.py`
- Modify: `backend/app/routers/graph.py`
- Modify: `backend/app/schemas/rule.py`
- Create: `tests/tenant/test_rules_import_isolation.py`

**Interfaces:**
- Consumes: `AuthContext`、租户化 `RuleSet/Rule/DocumentType/RuleParseSkill`
- Produces: 所有规则与导入服务显式 `tenant_id` 参数、租户化导入任务状态

- [ ] **Step 1: 写规则和导入任务隔离失败测试**

```python
def test_rule_and_import_task_are_not_visible_across_tenants(client, admin_auth, admin_b, rule_a):
    headers = authenticate(client, admin_b)
    assert client.get(
        "/api/rules",
        params={"rule_set_id": rule_a.rule_set_id},
    ).status_code == 404
    assert client.delete(f"/api/rules/{rule_a.id}", headers=headers).status_code == 404


def test_import_task_lookup_checks_tenant(tenant_a, tenant_b):
    task = rule_import_task.create_task(
        rule_set_id="ruleset-a",
        tenant_id=tenant_a.id,
        user_id=uuid.uuid4(),
        file_name="rules.xlsx",
    )
    assert rule_import_task.get_task(task.task_id, tenant_b.id) is None
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_rules_import_isolation.py -v`

Expected: FAIL，规则集没有校验租户，导入任务没有 `tenant_id`。

- [ ] **Step 3: 改造规则和导入服务**

所有面向请求的规则函数增加 `tenant_id`：

```text
def list_rules(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    doc_type: str | None = None,
    check_category: str | None = None,
    enabled_only: bool = False,
    defect_severity: str | None = None,
    only_confirmed: bool = False,
) -> list[RuleOut]
get_rule(db, tenant_id, rule_id) -> Rule | None
update_rule(db, tenant_id, rule_id, payload) -> RuleOut | None
delete_rule(db, tenant_id, rule_id) -> bool
get_snapshot(db, tenant_id, snapshot_id) -> RuleSnapshot | None
```

每次入口先校验规则集属于当前租户；规则、快照和 Skill 查询同时带租户条件。`confirmed_by` 只从 `AuthContext.user.id` 取得，不再接受查询参数。

`ImportProgress` 增加 `tenant_id`、`user_id`；`create_task` 签名改为 `create_task(rule_set_id, tenant_id, user_id, file_name)`；`get_task(task_id, tenant_id)` 对租户不匹配返回 `None`。后台线程使用任务中的租户创建独立作用域，不读取请求状态。

规则导入时创建的新文档类型、Skill 副本、规则和缺陷检测全部使用当前租户。

- [ ] **Step 4: 运行规则、导入和冲突检测测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_rules_import_isolation.py -v`

Run: `backend\.venv\Scripts\python.exe -m pytest tests\test_rule_import_granularity.py tests\test_condition_evaluation.py tests\test_field_expression.py -v`

Expected: 新隔离测试和原规则测试全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/services/rule_service.py backend/app/services/rule_parse_skill_service.py backend/app/services/rule_parse_engine.py backend/app/services/rule_import_service.py backend/app/services/rule_conflict_detector.py backend/app/services/rule_import_task.py backend/app/routers/rules.py backend/app/routers/rule_parse_skills.py backend/app/routers/graph.py backend/app/schemas/rule.py tests/tenant/test_rules_import_isolation.py
git commit -m "feat: isolate rule management and import tasks"
```

---

## Task 14: 改造 OCR 和审查任务租户隔离

**Files:**
- Modify: `backend/app/services/ocr_task_service.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/app/routers/ocr.py`
- Modify: `backend/app/routers/reviews.py`
- Modify: `backend/app/main.py`
- Create: `tests/tenant/test_ocr_review_isolation.py`

**Interfaces:**
- Consumes: `AuthContext`、`ReviewTask.tenant_id`、`OcrTask.tenant_id`
- Produces: 租户化 OCR/审查触发、轮询、结果读取和状态更新

- [ ] **Step 1: 写 OCR 与审查跨租户失败测试**

```python
def test_ocr_and_review_ids_from_other_tenant_return_404(client, admin_auth, admin_b, doc_a, review_task_a):
    headers = authenticate(client, admin_b)
    assert client.post(
        f"/api/ocr/documents/{doc_a.id}",
        params={"rule_set_id": doc_a.rule_set_id},
        headers=headers,
    ).status_code == 404
    assert client.get(f"/api/reviews/{review_task_a.id}").status_code == 404


def test_task_list_uses_tenant(admin_a_client, review_task_a, review_task_b):
    ids = {item["id"] for item in admin_a_client.get("/api/reviews").json()}
    assert review_task_a.id in ids
    assert review_task_b.id not in ids
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_ocr_review_isolation.py -v`

Expected: FAIL，后台任务和详情查询仍按裸 ID。

- [ ] **Step 3: 改造任务服务**

函数签名增加 `tenant_id`：

```text
start_ocr_for_doc(db, tenant_id, rule_set_id, doc_id, profile="type") -> OcrTask
start_ocr_for_contract(db, tenant_id, rule_set_id, contract_id, force=False, profile="type") -> OcrTask
get_ocr_task(db, tenant_id, task_id) -> OcrTask | None
start_review(db, tenant_id, user_id, contract_id, snapshot_id=None) -> ReviewTask
get_task_status(db, tenant_id, task_id) -> ReviewTask | None
get_results_by_rule(db, tenant_id, task_id) -> dict
get_results_by_doc(db, tenant_id, task_id) -> dict
```

所有 `db.get` 替换为租户条件查询。后台线程把 `tenant_id` 作为显式参数，创建 `SessionLocal` 后调用 `bind_tenant`；任务、文档和合同三条记录必须属于同一租户。

`reconcile_stale_tasks` 接收可选的 `tenant_id`，请求路径只回收当前租户任务；启动时 `force_all=True` 可以全局回收，但必须逐行保持原租户。审查结果状态历史中的 `by` 写入用户 ID，并同时保存用户显示名快照。

- [ ] **Step 4: 运行 OCR、审查和中断恢复测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_ocr_review_isolation.py -v`

Run: `backend\.venv\Scripts\python.exe -m pytest tests\test_review_task_reconcile.py tests\test_ocr_task_reconcile.py -v`

Expected: 新隔离测试与原中断恢复测试全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/services/ocr_task_service.py backend/app/services/review_service.py backend/app/routers/ocr.py backend/app/routers/reviews.py backend/app/main.py tests/tenant/test_ocr_review_isolation.py
git commit -m "feat: isolate ocr and review tasks by tenant"
```

---

## Task 15: 改造图谱、Neo4j 和进程内构建任务

**Files:**
- Modify: `backend/app/models/rule_snapshot.py`
- Modify: `backend/app/services/graph_builder_service.py`
- Modify: `backend/app/services/graph_review_service.py`
- Modify: `backend/app/services/graph_build_progress.py`
- Modify: `backend/app/neo4j_client.py`
- Modify: `backend/app/routers/graph.py`
- Create: `tests/tenant/test_graph_isolation.py`

**Interfaces:**
- Consumes: `AuthContext`、`RuleSnapshot.tenant_id`、Neo4j `tenant_id`
- Produces: `build_graph(db, tenant_id, rule_set_id, auto_confirm_all, operator, progress_callback)`、`get_graph(neo4j, tenant_id, graph_id)`、租户化 `list_build_tasks(tenant_id, limit)`

- [ ] **Step 1: 写图谱隔离失败测试**

```python
def test_graph_access_resolves_tenant_from_snapshot(client, admin_auth, admin_b, snapshot_a):
    authenticate(client, admin_auth)
    assert client.get(f"/api/rules/graph/{snapshot_a.graph_id}").status_code == 200
    authenticate(client, admin_b)
    assert client.get(f"/api/rules/graph/{snapshot_a.graph_id}").status_code == 404


def test_build_task_list_is_tenant_scoped():
    state_a = graph_build_progress.create_state_for_test(tenant_id="a")
    state_b = graph_build_progress.create_state_for_test(tenant_id="b")
    assert [item["task_id"] for item in graph_build_progress.list_build_tasks("a")] == [state_a.task_id]
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_graph_isolation.py -v`

Expected: FAIL，图谱接口未校验租户，构建任务没有租户字段。

- [ ] **Step 3: 改造 Neo4j 和多进程内状态**

`GraphBuildTaskState` 增加 `tenant_id`、`user_id`。`get_build_task(task_id, tenant_id)` 和 `list_build_tasks(tenant_id, limit)` 只返回当前租户任务；`operator` 只存认证用户 ID/显示名，不接受前端参数。

所有业务图谱方法增加 `tenant_id` 查询条件，例如：

```cypher
MATCH (n)
WHERE n.graph_id = $graph_id AND n.tenant_id = $tenant_id
RETURN n
```

关系查询必须同时约束两端节点 `graph_id` 和 `tenant_id`。写入节点时强制写入 `tenant_id`；更新、删除和清图同样带租户条件。

`get_graph`、`graph/ontology` 和 `confirm_graph` 先从 `RuleSnapshot` 执行 `tenant_get`，确认 `graph_id` 归属后再访问 Neo4j。`build_graph` 的规则查询、快照创建和旧图清理全部限定当前租户。

迁移旧图谱时，从 `rule_snapshots.tenant_id` 回填节点属性；无法匹配的图禁止读取并在日志中报告孤儿 `graph_id`。

- [ ] **Step 4: 运行图谱隔离测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_graph_isolation.py -v`

Run: `backend\.venv\Scripts\python.exe tests\run_graph_rule_tests.py`

Expected: 跨租户图谱读取返回 `404`，同租户图谱构建和审查仍通过。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/models/rule_snapshot.py backend/app/services/graph_builder_service.py backend/app/services/graph_review_service.py backend/app/services/graph_build_progress.py backend/app/neo4j_client.py backend/app/routers/graph.py tests/tenant/test_graph_isolation.py
git commit -m "feat: isolate graph access and build tasks by tenant"
```

---

## Task 16: 给全部业务 API 接入认证和角色依赖

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/rule_sets.py`
- Modify: `backend/app/routers/contracts.py`
- Modify: `backend/app/routers/rules.py`
- Modify: `backend/app/routers/doc_types.py`
- Modify: `backend/app/routers/graph.py`
- Modify: `backend/app/routers/reviews.py`
- Modify: `backend/app/routers/ocr.py`
- Modify: `backend/app/routers/rule_parse_skills.py`
- Modify: `backend/app/routers/settings.py`
- Create: `tests/acceptance_security.py`

**Interfaces:**
- Consumes: `require_permission`、`AuthContext`
- Produces: 除健康检查、版本和公开认证端点外，所有 `/api/*` 默认要求有效会话

- [ ] **Step 1: 写路由保护失败测试**

```python
PROTECTED_GETS = [
    "/api/rule-sets",
    "/api/contracts",
    "/api/doc-types",
    "/api/settings",
    "/api/reviews",
    "/api/ocr/tasks?rule_set_id=00000000-0000-0000-0000-000000000000",
]


def test_all_business_endpoints_require_login(client):
    for path in PROTECTED_GETS:
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code)
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\acceptance_security.py -v`

Expected: FAIL，现有业务 API 未要求登录。

- [ ] **Step 3: 按能力接入依赖**

在路由函数加入 `ctx: AuthContext = Depends(require_permission("review.view"))`，并把字符串替换为该接口所需能力：

- `review.view`：合同、规则、文档类型、图谱、审查和 OCR 的读取。
- `review.execute`：上传、OCR、审查、人工字段修改和结果状态修改。
- `rules.manage`、`doc_types.manage`、`graph.manage`：对应配置写操作。
- `settings.read/update`：系统设置。
- `profile.*`：个人资料。

详情、下载、轮询接口使用 `ctx.tenant_id`；不从请求体或 URL 接收租户 ID。所有路由中删除前端可传的 `operator`、`confirmed_by` 业务身份参数。

生产环境把 FastAPI `/docs`、`/redoc`、`/openapi.json` 关闭或放在明确环境开关后。

- [ ] **Step 4: 运行未登录和角色拒绝测试**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\acceptance_security.py -v`

Expected: 未登录全部 `401`；viewer 写操作 `403`；普通成员无法进入平台和管理员接口。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/main.py backend/app/routers tests/acceptance_security.py
git commit -m "feat: require authentication and permissions on business APIs"
```

---

## Task 17: 改造现有后端测试与默认租户夹具

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/run_graph_rule_tests.py`
- Modify: `tests/test_review_task_reconcile.py`
- Modify: `tests/test_ocr_task_reconcile.py`
- Modify: `tests/run_graph_rule_tests.bat`
- Modify: `tests/README.md`

**Interfaces:**
- Consumes: 迁移后的默认租户与租户化服务签名
- Produces: `default_tenant_id`、`tenant_session`、原测试的显式默认租户适配

- [ ] **Step 1: 增加默认租户夹具**

```python
@pytest.fixture(scope="session")
def default_tenant_id():
    with SessionLocal() as db:
        value = db.execute(
            select(Tenant.id).where(Tenant.code == "default")
        ).scalar_one()
        return value


@pytest.fixture
def tenant_session(default_tenant_id):
    db = SessionLocal()
    bind_tenant(db, default_tenant_id)
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: 运行现有测试并记录失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\test_review_task_reconcile.py tests\test_ocr_task_reconcile.py -v`

Expected: 因服务签名和必填租户字段失败。

- [ ] **Step 3: 修改现有脚本和服务调用**

- 创建 `Contract`、`ReviewTask`、`OcrTask` 时显式传入默认租户。
- 调用 `start_review`、`start_ocr_for_doc`、`reconcile_*` 时传默认租户。
- `run_graph_rule_tests.py` 中的临时规则集、规则、快照、合同全部绑定默认租户。
- 测试清理仍按 ID 和租户删除，不执行全局清空。

- [ ] **Step 4: 运行原测试集合**

Run: `backend\.venv\Scripts\python.exe tests\run_graph_rule_tests.py`

Run: `backend\.venv\Scripts\python.exe -m pytest tests\test_review_task_reconcile.py tests\test_ocr_task_reconcile.py tests\test_rule_import_granularity.py -v`

Expected: 原核心后端测试全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add tests
git commit -m "test: adapt legacy review tests to tenant scoping"
```

---

## Task 18: 建立前端认证类型、API 客户端和单元测试

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/auth/validation.ts`
- Create: `frontend/src/auth/permissions.ts`
- Create: `frontend/src/auth/validation.test.ts`
- Create: `frontend/src/auth/permissions.test.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: 后端 `/auth/me` 和成员接口契约
- Produces: `AuthUser`、`TenantInfo`、`Permission`、`authApi`、`tenantMembersApi`、`platformApi`、`visibleMenuKeys(role)`、`hasPermission(role, permission)`

- [ ] **Step 1: 写前端权限与校验失败测试**

```typescript
import { describe, expect, it } from 'vitest'
import { normalizePhone } from './validation'
import { visibleMenuKeys } from './permissions'

describe('auth validation', () => {
  it('normalizes mainland mobile numbers', () => {
    expect(normalizePhone('138-0013-8000')).toBe('+8613800138000')
  })
})

describe('menu permissions', () => {
  it('hides system settings from reviewers', () => {
    expect(visibleMenuKeys('reviewer')).not.toContain('/settings')
  })

  it('shows system settings to tenant admins', () => {
    expect(visibleMenuKeys('tenant_admin')).toContain('/settings')
  })
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm --prefix frontend test -- --run`

Expected: FAIL，Vitest 与目标模块不存在。

- [ ] **Step 3: 安装测试工具并实现类型/API**

在 `frontend/package.json` 增加：

```json
{
  "scripts": {
    "test": "vitest"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "jsdom": "^25.0.1",
    "vitest": "^2.1.8"
  }
}
```

Axios 客户端设置 `withCredentials: true`，增加内存 CSRF 变量：

```typescript
let csrfToken = ''

export function setCsrfToken(token: string) {
  csrfToken = token
}

http.interceptors.request.use((config) => {
  const method = (config.method ?? 'get').toLowerCase()
  if (csrfToken && ['post', 'put', 'patch', 'delete'].includes(method)) {
    config.headers['X-CSRF-Token'] = csrfToken
  }
  return config
})
```

响应拦截器在 `401` 时派发 `docgraph:session-expired` 事件；在 `403` 时保留后端 `code` 供页面显示。

`visibleMenuKeys(role)` 按固定角色映射返回菜单路径，`tenant_admin` 包含 `/settings`，`reviewer` 和 `viewer` 不包含。
`hasPermission(role, permission)` 使用与后端同名权限字符串判断按钮和路由能力。

- [ ] **Step 4: 运行前端工具测试**

Run: `npm --prefix frontend install`

Run: `npm --prefix frontend test -- --run`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/test frontend/src/auth frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat: add frontend auth contracts and test tooling"
```

---

## Task 19: 实现前端会话 Provider 和认证页面

**Files:**
- Create: `frontend/src/auth/AuthContext.tsx`
- Create: `frontend/src/auth/RequireAuth.tsx`
- Create: `frontend/src/pages/auth/LoginPage.tsx`
- Create: `frontend/src/pages/auth/RegisterPage.tsx`
- Create: `frontend/src/pages/auth/PendingPage.tsx`
- Create: `frontend/src/pages/auth/ForgotPasswordPage.tsx`
- Create: `frontend/src/pages/auth/ResetPasswordPage.tsx`
- Create: `frontend/src/pages/auth/AcceptInvitePage.tsx`
- Create: `frontend/src/pages/ForbiddenPage.tsx`
- Create: `frontend/src/auth/AuthContext.test.tsx`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `authApi`、`setCsrfToken`
- Produces: `useAuth()`、`AuthProvider`、`RequireAuth`、公开认证路由页面

- [ ] **Step 1: 写会话 Provider 失败测试**

```tsx
it('loads current user and csrf token', async () => {
  vi.spyOn(authApi, 'me').mockResolvedValue({
    user: { id: 'u1', name: '张三' },
    tenant: { id: 't1', name: '甲公司' },
    role: 'tenant_admin',
    permissions: ['settings.read'],
    csrf_token: 'csrf-1',
  })
  render(<AuthProvider><Probe /></AuthProvider>)
  expect(await screen.findByText('tenant_admin')).toBeInTheDocument()
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm --prefix frontend test -- --run src/auth/AuthContext.test.tsx`

Expected: FAIL，Provider 不存在。

- [ ] **Step 3: 实现 AuthProvider 和页面**

`AuthContextValue` 和 `RequirePermission` 固定为：

```typescript
type Role = 'tenant_admin' | 'reviewer' | 'viewer' | 'platform_admin'

interface AuthContextValue {
  loading: boolean
  user: AuthUser | null
  tenant: TenantInfo | null
  role: Role | null
  permissions: Set<string>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
  hasPermission: (permission: string) => boolean
}

function RequirePermission(props: {
  permission: string
  children: ReactNode
}): ReactNode
```

初始化时调用 `/auth/me`；成功后将 `csrf_token` 写入模块内存；`401` 置空用户。监听 `docgraph:session-expired` 清理会话并跳转 `/login`。

注册表单必须提交组织名称、管理员姓名、工作邮箱、手机号、密码、确认密码和条款勾选。客户端校验与后端规则一致；后端仍是最终校验源。登录页不区分账号不存在和密码错误。接受邀请页从 URL 读取一次性 token，设置密码后跳转登录。

- [ ] **Step 4: 运行认证页面测试**

Run: `npm --prefix frontend test -- --run src/auth`

Expected: Provider 初始化、退出、会话失效和错误提示测试 PASS。

- [ ] **Step 5: 提交**

```powershell
git add frontend/src/auth frontend/src/pages/auth frontend/src/pages/ForbiddenPage.tsx frontend/src/main.tsx
git commit -m "feat: add frontend authentication flows"
```

---

## Task 20: 拆分主布局并实现动态菜单与路由守卫

**Files:**
- Create: `frontend/src/layouts/AppLayout.tsx`
- Create: `frontend/src/layouts/PlatformLayout.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`
- Modify: `frontend/src/context/RuleSetContext.tsx`

**Interfaces:**
- Consumes: `useAuth`、`visibleMenuKeys`
- Produces: 公开路由、租户受保护布局、平台受保护布局和用户信息菜单

- [ ] **Step 1: 写路由与菜单失败测试**

```tsx
it('does not render settings menu for reviewer', async () => {
  renderAppWithAuth({ role: 'reviewer', permissions: ['review.view'] })
  expect(await screen.findByText('文档上传')).toBeInTheDocument()
  expect(screen.queryByText('系统设置')).not.toBeInTheDocument()
})

it('redirects anonymous user to login', async () => {
  renderAppWithAuth(null)
  expect(await screen.findByRole('heading', { name: '登录' })).toBeInTheDocument()
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm --prefix frontend test -- --run src/App.test.tsx`

Expected: FAIL，路由仍直接进入业务布局。

- [ ] **Step 3: 拆分布局并接入权限**

把现有 `AppInner` 中的侧栏、Header 和业务内容迁入 `AppLayout.tsx`。菜单从全量配置按下式过滤：

```typescript
const items = ALL_MENU_ITEMS.filter((item) =>
  visibleMenuKeys(role!).includes(item.key),
)
```

路由结构固定为：

```tsx
<Routes>
  <Route path="/login" element={<LoginPage />} />
  <Route path="/register" element={<RegisterPage />} />
  <Route path="/forgot-password" element={<ForgotPasswordPage />} />
  <Route path="/reset-password" element={<ResetPasswordPage />} />
  <Route path="/accept-invite" element={<AcceptInvitePage />} />
  <Route element={<RequireAuth mode="tenant" />}>
    <Route element={<AppLayout />}>
      <Route path="/upload" element={<RequirePermission permission="review.execute"><UploadPage /></RequirePermission>} />
      <Route path="/settings" element={<RequirePermission permission="settings.read"><SettingsPage /></RequirePermission>} />
      <Route path="/rules" element={<RulesPage />} />
      <Route path="/doc-types" element={<DocTypesPage />} />
      <Route path="/graph" element={<GraphPage />} />
      <Route path="/review" element={<ReviewPage />} />
      <Route path="/results" element={<ResultsPage />} />
    </Route>
  </Route>
  <Route element={<RequireAuth mode="platform" />}>
    <Route element={<PlatformLayout />}>
      <Route path="/platform/registrations" element={<RegistrationReviewPage />} />
      <Route path="/platform/tenants" element={<TenantListPage />} />
    </Route>
  </Route>
</Routes>
```

侧栏底部显示真实用户名和租户名，增加个人资料、修改密码、退出登录。`RuleSetProvider` 只在租户路由内挂载，不能对平台管理员发起业务请求。

`RuleSetContext` 的 localStorage 键改为：

```typescript
const storageKey = `current_rule_set_id:${tenantId}`
```

登录和退出时清理不在当前租户列表中的旧规则集引用。

- [ ] **Step 4: 运行路由和菜单测试**

Run: `npm --prefix frontend test -- --run src/App.test.tsx src/auth`

Expected: 匿名跳转、角色菜单、设置路由守卫和平台布局测试 PASS。

- [ ] **Step 5: 提交**

```powershell
git add frontend/src/App.tsx frontend/src/layouts frontend/src/context/RuleSetContext.tsx frontend/src/App.test.tsx
git commit -m "feat: add permission-aware frontend routing"
```

---

## Task 21: 在系统设置中加入用户与权限页面

**Files:**
- Create: `frontend/src/pages/settings/RuntimeSettingsTab.tsx`
- Create: `frontend/src/pages/settings/MembersTab.tsx`
- Create: `frontend/src/pages/settings/MembersTab.test.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Consumes: `tenantMembersApi`、邀请与角色修改接口
- Produces: 设置页“运行设置”和“用户与权限”两个页签

- [ ] **Step 1: 写成员页面失败测试**

```tsx
it('hides role editing from reviewer at route level', () => {
  expect(canAccess('/settings', 'reviewer')).toBe(false)
})

it('shows masked phone and role in member table', async () => {
  vi.spyOn(tenantMembersApi, 'list').mockResolvedValue([{
    id: 'm1',
    name: '王五',
    email: 'wang@example.com',
    phone_masked: '139****9000',
    role: 'reviewer',
    status: 'active',
  }])
  render(<MembersTab />)
  expect(await screen.findByText('139****9000')).toBeInTheDocument()
  expect(screen.getByText('审查员')).toBeInTheDocument()
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm --prefix frontend test -- --run src/pages/settings/MembersTab.test.tsx`

Expected: FAIL，页面不存在。

- [ ] **Step 3: 拆分设置页并实现成员管理**

把现有运行参数编辑器整体迁入 `RuntimeSettingsTab.tsx`，`SettingsPage.tsx` 只保留页面标题和页签：

```tsx
const tabs = [
  { key: 'runtime', label: '运行设置', children: <RuntimeSettingsTab /> },
  { key: 'members', label: '用户与权限', children: <MembersTab /> },
]
```

成员页包含搜索、状态筛选、邀请、编辑角色、停用、恢复、重发邀请和撤销邀请。角色标签固定为“租户管理员 / 审查员 / 只读查看者”。手机号默认脱敏；管理员点击查看完整号码时记录审计并显示明确提示。

最后一名租户管理员无法降级或停用的错误 `LAST_TENANT_ADMIN` 必须展示为业务提示，而不是通用“保存失败”。

- [ ] **Step 4: 运行设置与成员前端测试**

Run: `npm --prefix frontend test -- --run src/pages/settings`

Expected: 页签、脱敏、角色切换和最后管理员错误展示 PASS。

- [ ] **Step 5: 提交**

```powershell
git add frontend/src/pages/SettingsPage.tsx frontend/src/pages/settings frontend/src/api/client.ts frontend/src/types.ts
git commit -m "feat: add tenant user and permission management UI"
```

---

## Task 22: 实现平台注册审核和租户状态页面

**Files:**
- Create: `frontend/src/pages/platform/RegistrationReviewPage.tsx`
- Create: `frontend/src/pages/platform/TenantListPage.tsx`
- Create: `frontend/src/pages/platform/PlatformPages.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Consumes: `platformApi`
- Produces: 平台待审列表、通过/驳回、租户暂停/恢复/归档页面

- [ ] **Step 1: 写平台页面失败测试**

```tsx
it('requires a rejection reason', async () => {
  render(<RegistrationReviewPage />)
  await userEvent.click(await screen.findByRole('button', { name: '驳回' }))
  expect(screen.getByText('请填写驳回原因')).toBeInTheDocument()
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm --prefix frontend test -- --run src/pages/platform/PlatformPages.test.tsx`

Expected: FAIL，页面不存在。

- [ ] **Step 3: 实现平台页面**

审核页展示组织、申请人、邮箱、手机号、提交时间、邮箱验证状态和申请状态。批准可填写备注；驳回必须填写原因。所有状态变更使用确认对话框。

租户页仅展示租户名称、状态、创建时间、最近审核时间和成员数量，不展示业务数据。暂停租户时提示现有会话立即失效；恢复与归档需要二次确认。

页面不得渲染合同、文档、规则、图谱或审查结果入口。

- [ ] **Step 4: 运行平台前端测试**

Run: `npm --prefix frontend test -- --run src/pages/platform`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add frontend/src/pages/platform frontend/src/api/client.ts frontend/src/types.ts
git commit -m "feat: add platform approval interface"
```

---

## Task 23: 按角色限制现有业务页面的操作

**Files:**
- Modify: `frontend/src/pages/UploadPage.tsx`
- Modify: `frontend/src/pages/RulesPage.tsx`
- Modify: `frontend/src/pages/DocTypesPage.tsx`
- Modify: `frontend/src/pages/GraphPage.tsx`
- Modify: `frontend/src/pages/ReviewPage.tsx`
- Modify: `frontend/src/pages/ResultsPage.tsx`
- Create: `frontend/src/auth/RoleUi.test.tsx`

**Interfaces:**
- Consumes: `useAuth().hasPermission`
- Produces: 与后端权限一致的按钮/操作可见性

- [ ] **Step 1: 写角色按钮失败测试**

```tsx
it('viewer cannot see upload trigger', async () => {
  renderWithAuth(<UploadPage />, { role: 'viewer', permissions: ['review.view'] })
  expect(screen.queryByText('开始上传')).not.toBeInTheDocument()
  expect(screen.getByText('合同详情')).toBeInTheDocument()
})

it('only roles with execute permission can update result status', () => {
  expect(hasPermission('viewer', 'review.execute')).toBe(false)
  expect(hasPermission('reviewer', 'review.execute')).toBe(true)
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `npm --prefix frontend test -- --run src/auth/RoleUi.test.tsx`

Expected: FAIL，页面未读取权限。

- [ ] **Step 3: 在现有页面接入能力判断**

- 上传、OCR、重跑、删除：`review.execute`
- 规则创建、导入、确认、删除：`rules.manage`
- 文档类型写操作：`doc_types.manage`
- 图谱构建和确认：`graph.manage`
- 审查启动和结果状态修改：`review.execute`
- 查看页面和原件：`review.view`

viewer 进入只读页面时不渲染写按钮，但仍允许查看已有数据和历史任务。组件卸载时停止所有 OCR、审查和图谱轮询；权限变更后通过 `AuthContext.refresh()` 重新挂载关键页面。

- [ ] **Step 4: 运行角色 UI 测试**

Run: `npm --prefix frontend test -- --run src/auth/RoleUi.test.tsx`

Expected: reviewer、viewer、tenant_admin 三种界面的按钮可见性 PASS。

- [ ] **Step 5: 提交**

```powershell
git add frontend/src/pages frontend/src/auth/RoleUi.test.tsx
git commit -m "feat: align review UI controls with permissions"
```

---

## Task 24: 完成审计、日志脱敏、部署和运维配置

**Files:**
- Create: `backend/app/services/audit_service.py`
- Create: `tests/tenant/test_audit.py`
- Modify: `backend/app/services/platform_service.py`
- Modify: `backend/app/services/tenant_member_service.py`
- Modify: `backend/app/services/auth_service.py`
- Modify: `backend/app/services/settings_service.py`
- Modify: `backend/app/routers/contracts.py`
- Modify: `frontend/nginx/default.conf`
- Modify: `docker-compose.yml`
- Modify: `backend/.env.example`
- Modify: `README.md`
- Modify: `docs/部署-Docker单机部署.md`

**Interfaces:**
- Consumes: `AuthContext`、`AuditLog`
- Produces: `audit_service.record(db, tenant_id, actor_user_id, action, target_type, target_id, result, metadata)`、脱敏日志、生产 Cookie/代理配置

- [ ] **Step 1: 写审计失败测试**

```python
def test_role_change_writes_audit_log(client, admin_auth, reviewer_membership):
    headers = authenticate(client, admin_auth)
    response = client.patch(
        f"/api/tenant/members/{reviewer_membership.id}",
        json={"role": "viewer"},
        headers=headers,
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        row = db.execute(
            select(AuditLog)
            .where(AuditLog.action == "member.role.update")
            .order_by(AuditLog.created_at.desc())
        ).scalars().first()
        assert row.tenant_id == admin_auth.tenant_id
        assert row.actor_user_id == admin_auth.user_id
```

- [ ] **Step 2: 运行并确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_audit.py -v`

Expected: FAIL，角色变更没有审计记录。

- [ ] **Step 3: 接入关键审计和部署配置**

至少记录：

- 注册提交、邮箱验证、审核通过/驳回。
- 登录成功、登录失败、账号锁定、退出、密码重置。
- 邀请、接受邀请、角色变更、成员启停。
- 设置更新、规则集删除、租户暂停/恢复。
- 原始文件下载、跨租户访问拒绝。

审计 metadata 不保存密码、令牌、完整手机号或文档正文。日志改用参数化格式并带 `request_id`、`user_id`、`tenant_id`。

部署配置：

- `docker-compose.yml` 设置 `TRUST_PROXY_HEADERS=true`。
- nginx 继续传递 `X-Real-IP`、`X-Forwarded-Proto`，并增加 `Strict-Transport-Security` 时必须以 HTTPS 前置代理为前提。
- 生产文档要求 `APP_SECRET_KEY` 使用 32 字节以上随机值。
- README 删除“API 无鉴权”的过时安全提示，改为初始化平台管理员、创建测试库、备份和升级步骤。
- `deploy.sh` 在 `APP_SECRET_KEY` 缺失或仍是示例值时终止部署。

- [ ] **Step 4: 运行审计和部署静态检查**

Run: `backend\.venv\Scripts\python.exe -m pytest tests\tenant\test_audit.py -v`

Run: `docker compose config`

Expected: 审计测试 PASS，Compose 配置可解析。

- [ ] **Step 5: 提交**

```powershell
git add backend/app/services backend/app/routers/contracts.py frontend/nginx/default.conf docker-compose.yml backend/.env.example deploy.sh README.md docs/部署-Docker单机部署.md tests/tenant/test_audit.py
git commit -m "chore: harden audit logging and deployment"
```

---

## Task 25: 执行完整安全验收、回归和交付检查

**Files:**
- Modify: `tests/acceptance_security.py`
- Create: `docs/登录权限与多租户隔离-验收记录.md`
- Update: `README.md`

**Interfaces:**
- Consumes: 前 24 个任务的最终实现
- Produces: 可复现的验收矩阵、已知限制和上线检查结论

- [ ] **Step 1: 补齐 A/B 租户端到端验收**

验收脚本必须为租户 A/B 各创建：

- 一套同名规则集。
- 一份合同与原始文件。
- 一个文档类型。
- 一个规则和快照。
- 一个 OCR 任务和一个审查任务。
- 一条结果与一个图谱。
- 不同的系统设置值。

逐项断言列表不可见、ID 查询返回 `404`、文件不能下载、图谱不能读取、设置互不影响、成员不能互管。

- [ ] **Step 2: 执行后端完整测试**

```powershell
backend\.venv\Scripts\python.exe -m pytest tests -v
backend\.venv\Scripts\python.exe tests\run_graph_rule_tests.py
backend\.venv\Scripts\python.exe tests\test_llm_client.py
backend\.venv\Scripts\python.exe tests\test_version.py
```

Expected: 全部 PASS；需要 PostgreSQL/Neo4j 的测试在服务启动后运行。

- [ ] **Step 3: 执行前端测试和构建**

```powershell
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Expected: 单元测试全部 PASS，TypeScript 与 Vite 构建成功。

- [ ] **Step 4: 执行部署与安全冒烟**

```powershell
docker compose --env-file backend\.env --profile deploy build
docker compose --env-file backend\.env --profile deploy up -d
bash deploy.sh check
```

手工验证：

1. 未登录访问业务 URL 跳转 `/login`。
2. 注册、邮箱验证、平台审核、登录链路成功。
3. reviewer 看不到系统设置，直接访问 `/settings` 进入无权限页。
4. viewer 无法上传或修改结果。
5. A 用户使用 B 的对象 ID 全部得到 `404`。
6. 停用成员后旧会话立即失效。
7. 原始文件仅本租户成员可下载。

- [ ] **Step 5: 记录验收结果并做最终提交**

在 `docs/登录权限与多租户隔离-验收记录.md` 记录日期、版本、测试库、命令、结果、未覆盖项和上线结论。不得把未运行的测试写成通过。

```powershell
git diff --check
git add tests/acceptance_security.py docs/登录权限与多租户隔离-验收记录.md README.md
git commit -m "test: verify authentication and tenant isolation"
```

---

## 需求覆盖对照

| 需求 | 对应任务 |
| --- | --- |
| 邮箱注册、邮箱验证、管理员审核 | 5、6、9、19、22 |
| 强制手机号与大陆格式 | 2、3、5、19 |
| 邮箱密码登录、锁定、重置 | 6、7、19 |
| 管理员可见系统设置 | 7、16、20、21 |
| 用户权限管理 | 7、8、16、21 |
| 租户是安全边界而非规则集 | 3、4、10、11 |
| 全业务表和服务隔离 | 11、12、13、14、15、16 |
| 文件下载与上传目录隔离 | 11、16、25 |
| Neo4j、后台任务隔离 | 13、14、15 |
| 存量数据迁移 | 10、17 |
| 审计、日志与部署安全 | 7、24、25 |
| 原核心审查链路回归 | 12、13、14、15、17、25 |

## 执行顺序约束

- Task 1-9 完成身份与权限基础，不得提前开放公网。
- Task 10 之后才允许把业务 API 接入租户上下文。
- Task 16 是全业务路由的安全闸门，未完成前不得发布生产镜像。
- Task 17 未通过时不得开始最终验收。
- Task 18-23 只消费已稳定的后端接口，不从浏览器补造权限逻辑。
- Task 24-25 未通过时不得宣称功能完成。
