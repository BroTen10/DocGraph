"""Security primitives shared by registration, login and account recovery."""

from __future__ import annotations

import hashlib
import re
import secrets
from email.utils import parseaddr

from argon2.exceptions import Argon2Error
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

password_hash = PasswordHash.recommended()


def normalize_cn_phone(value: str) -> str:
    """Return a Mainland China mobile number in +86 E.164 form."""
    if not isinstance(value, str):
        raise ValueError("请输入有效的中国大陆手机号")

    compact = re.sub(r"[\s-]", "", value.strip())
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("86") and len(compact) == 13:
        compact = compact[2:]

    if not CN_MOBILE_RE.fullmatch(compact):
        raise ValueError("请输入有效的中国大陆手机号")
    return f"+86{compact}"


def normalize_email(value: str) -> str:
    """Return the canonical login form of an email address."""
    if not isinstance(value, str):
        raise ValueError("请输入有效邮箱")

    raw = value.strip()
    display_name, address = parseaddr(raw)
    if display_name or address != raw:
        raise ValueError("请输入有效邮箱")

    candidate = address.lower()
    if len(candidate) > 254 or not EMAIL_RE.fullmatch(candidate):
        raise ValueError("请输入有效邮箱")
    return candidate


def validate_password(value: str) -> None:
    """Validate the product password policy without altering the password."""
    if (
        not isinstance(value, str)
        or not 10 <= len(value) <= 64
        or not re.search(r"[A-Za-z]", value)
        or not re.search(r"\d", value)
    ):
        raise ValueError("密码需为 10-64 位，并同时包含字母和数字")


def hash_password(value: str) -> str:
    """Validate and hash a password with the recommended Argon2id profile."""
    validate_password(value)
    return password_hash.hash(value)


def verify_password(hash_value: str, value: str) -> bool:
    """Verify a password, treating malformed stored hashes as a mismatch."""
    if not isinstance(hash_value, str) or not isinstance(value, str):
        return False
    try:
        return password_hash.verify(value, hash_value)
    except (PwdlibError, Argon2Error):
        return False


def new_token() -> tuple[str, str]:
    """Create an opaque token and the SHA-256 digest stored by the app."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest
