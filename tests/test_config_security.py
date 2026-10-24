import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_secret_key():
    with pytest.raises(ValidationError):
        Settings(app_debug=False, app_secret_key="")


def test_production_requires_secure_cookie():
    with pytest.raises(ValidationError):
        Settings(
            app_debug=False,
            app_secret_key="x" * 64,
            session_cookie_secure=False,
        )


def test_development_allows_empty_secret_key():
    assert Settings(app_debug=True, app_secret_key="").app_debug is True


def test_sqlalchemy_url_prefers_database_url():
    database_url = "postgresql+psycopg2://postgres:postgres@localhost:15432/docgraph_test"
    configured = Settings(database_url=database_url)
    assert configured.sqlalchemy_url == database_url
