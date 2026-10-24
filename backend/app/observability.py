"""Request correlation and sensitive-data redaction for logs and audits."""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Any

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SENSITIVE_KEY_PATTERN = re.compile(
    r"password|passwd|secret|token|authorization|cookie|phone|"
    r"document_text|ocr_text|content|file_path",
    re.IGNORECASE,
)
SAFE_METADATA_KEYS = frozenset(
    {
        "phone_masked",
        "token_expires_minutes",
        "total_count",
        "changed_count",
    }
)
SECRET_TEXT_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie)"
    r"(\s*[=:]\s*)([^\s,;&]+)"
)
TOKEN_QUERY_PATTERN = re.compile(r"(?i)([?&](?:token|access_token|refresh_token)=)[^&\s]+")
BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
request_ip_var: ContextVar[str | None] = ContextVar("request_ip", default=None)
actor_user_id_var: ContextVar[str | None] = ContextVar("actor_user_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def normalize_request_id(value: str | None) -> str | None:
    """Keep a caller-provided request ID only when it is bounded and safe."""
    candidate = (value or "").strip()
    return candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else None


def bind_request(request_id: str, ip: str | None) -> None:
    request_id_var.set(request_id)
    request_ip_var.set(ip)


def bind_principal(user_id: Any, tenant_id: Any) -> None:
    actor_user_id_var.set(str(user_id) if user_id else None)
    tenant_id_var.set(str(tenant_id) if tenant_id else None)


def clear_context() -> None:
    request_id_var.set(None)
    request_ip_var.set(None)
    actor_user_id_var.set(None)
    tenant_id_var.set(None)


def redact_text(value: str) -> str:
    """Redact common credential and token representations from log text."""
    redacted = BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    redacted = SECRET_TEXT_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    redacted = TOKEN_QUERY_PATTERN.sub(r"\1[REDACTED]", redacted)
    return redacted


def sanitize_metadata(value: Any) -> Any:
    """Return JSON-compatible metadata without credentials or personal payloads."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key not in SAFE_METADATA_KEYS and SENSITIVE_KEY_PATTERN.search(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize_metadata(raw_value)
        return result
    if isinstance(value, (list, tuple, set)):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))[:1000]


class RequestContextFilter(logging.Filter):
    """Attach correlation fields to records and redact credential-like text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.user_id = actor_user_id_var.get() or "-"
        record.tenant_id = tenant_id_var.get() or "-"
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, dict):
            record.args = {
                key: redact_text(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(
                redact_text(value) if isinstance(value, str) else value
                for value in record.args
            )
        return True
