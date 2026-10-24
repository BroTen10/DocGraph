import os
import sys
from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlparse

import pytest


_TEST_DSN = os.environ.get("TEST_DATABASE_URL", "")
_DATABASE_NAME = urlparse(_TEST_DSN).path.lstrip("/")
if not _DATABASE_NAME.endswith("_test"):
    pytest.exit(
        "TEST_DATABASE_URL 必须指向以 _test 结尾的独立数据库",
        returncode=2,
    )

os.environ["DATABASE_URL"] = _TEST_DSN
os.environ["APP_DEBUG"] = "false"
os.environ["APP_SECRET_KEY"] = "test-only-secret-key-" + "x" * 48
os.environ["EMAIL_BACKEND"] = "outbox"
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))
os.chdir(_BACKEND_DIR)


@pytest.fixture
def initialized_database() -> None:
    from app import models  # noqa: F401
    from app.database import Base, engine, init_db

    Base.metadata.drop_all(bind=engine)
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
