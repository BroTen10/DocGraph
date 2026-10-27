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
    assert not verify_password("not-a-valid-hash", "DocGraph2026")


def test_mail_and_token_normalization():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    raw, digest = new_token()
    assert len(raw) >= 40
    assert digest != raw


def test_accepts_minimum_length_password():
    validate_password("Abc123")


@pytest.mark.parametrize("password", ["Abc12", "1234567890", "abcdefghij"])
def test_rejects_weak_password(password):
    with pytest.raises(ValueError):
        validate_password(password)
