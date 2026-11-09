"""Origin allowlist tests (no database required)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.auth import csrf
from app.origin_utils import normalize_origin, origin_of_url


def _request(origin: str | None):
    headers = {"origin": origin} if origin is not None else {}
    return SimpleNamespace(headers=headers)


@pytest.fixture
def allowlisted_settings(monkeypatch):
    fake = SimpleNamespace(
        public_base_url="https://docs.example.com",
        csrf_origin_allowlist=["http://203.0.113.10:53267"],
    )
    monkeypatch.setattr(csrf, "settings", fake)
    return fake


def test_public_base_url_origin_is_accepted(allowlisted_settings):
    assert csrf.origin_matches(_request("https://docs.example.com"))


def test_extra_allowlisted_http_origin_is_accepted(allowlisted_settings):
    assert csrf.origin_matches(_request("http://203.0.113.10:53267"))


def test_unlisted_origin_is_rejected(allowlisted_settings):
    assert not csrf.origin_matches(_request("http://203.0.113.10:8080"))


def test_missing_origin_is_accepted(allowlisted_settings):
    assert csrf.origin_matches(_request(None))


def test_invalid_origin_is_rejected(allowlisted_settings):
    assert not csrf.origin_matches(_request("null"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP://Docs.Example.com:80", "http://docs.example.com"),
        ("https://docs.example.com:443/", "https://docs.example.com"),
        ("http://[::1]:53267", "http://[::1]:53267"),
        ("https://docs.example.com/app", None),
        ("docs.example.com", None),
        ("http://docs.example.com:bad", None),
    ],
)
def test_normalize_origin(raw, expected):
    assert normalize_origin(raw) == expected


def test_origin_of_url_ignores_path_and_query():
    assert (
        origin_of_url("https://docs.example.com/login?x=1")
        == "https://docs.example.com"
    )
