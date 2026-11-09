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
            public_base_url="https://docgraph.example.com",
            email_backend="smtp",
            smtp_host="smtp.example.com",
        )


def test_development_allows_empty_secret_key():
    assert Settings(app_debug=True, app_secret_key="").app_debug is True


def test_sqlalchemy_url_prefers_database_url():
    database_url = "postgresql+psycopg2://postgres:postgres@localhost:15432/docgraph_test"
    configured = Settings(database_url=database_url)
    assert configured.sqlalchemy_url == database_url


def test_api_docs_are_disabled_by_default():
    assert Settings().api_docs_enabled is False


def test_development_allows_missing_smtp_configuration():
    configured = Settings(
        app_debug=True,
        email_backend="smtp",
        smtp_host="",
    )
    assert configured.email_backend == "smtp"
    assert configured.smtp_host == ""


def test_production_requires_smtp_host_for_smtp_backend():
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        Settings(
            app_debug=False,
            app_secret_key="x" * 64,
            session_cookie_secure=True,
            public_base_url="https://docgraph.example.com",
            email_backend="smtp",
            smtp_host="",
        )


@pytest.mark.parametrize("backend", ["console", "outbox"])
def test_production_rejects_non_smtp_email_backends(backend):
    with pytest.raises(ValidationError, match="EMAIL_BACKEND"):
        Settings(
            app_debug=False,
            app_secret_key="x" * 64,
            session_cookie_secure=True,
            public_base_url="https://docgraph.example.com",
            email_backend=backend,
            smtp_host="smtp.example.com",
        )


_PROD = {
    "app_debug": False,
    "app_secret_key": "x" * 64,
    "email_backend": "smtp",
    "smtp_host": "smtp.example.com",
    "mail_from": "no-reply@example.com",
}


def test_production_rejects_http_entry_without_insecure_switch():
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            **_PROD,
            public_base_url="http://203.0.113.10:53267",
            session_cookie_secure=True,
        )


def test_insecure_http_mode_allows_http_entry():
    configured = Settings(
        **_PROD,
        public_base_url="http://203.0.113.10:53267",
        session_cookie_secure=False,
        allow_insecure_http=True,
    )
    assert configured.allow_insecure_http is True


def test_insecure_http_mode_rejects_secure_cookie_with_http_entry():
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        Settings(
            **_PROD,
            public_base_url="http://203.0.113.10:53267",
            session_cookie_secure=True,
            allow_insecure_http=True,
        )


def test_csrf_origin_allowlist_is_normalized_and_deduplicated():
    configured = Settings(
        csrf_allowed_origins=(
            "http://203.0.113.10:53267, "
            "https://Docs.Example.com:443/, "
            "http://203.0.113.10:53267"
        ),
    )
    assert configured.csrf_origin_allowlist == [
        "http://203.0.113.10:53267",
        "https://docs.example.com",
    ]


def test_csrf_origin_allowlist_rejects_invalid_entry():
    with pytest.raises(ValidationError, match="CSRF_ALLOWED_ORIGINS"):
        Settings(csrf_allowed_origins="203.0.113.10:53267")
