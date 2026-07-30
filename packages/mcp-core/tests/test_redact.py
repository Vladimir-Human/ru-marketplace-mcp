"""Tests for the secret-scrubbing helpers.

Redaction is the last thing standing between an upstream exception and two
places the operator cannot audit afterwards: the stderr log and the ToolError
text the model receives. Every pattern here earns its keep by a real leak
route, so each test names the route it closes.
"""

from __future__ import annotations

import pytest
from mcp_core.redact import redact_error_text, redact_url

# ------------------------------------------------------------- proxy credentials ----
#
# Proxies are configured as http://user:pass@host:port. A connect failure puts
# the whole URL into the exception, connectors wrap that with redact_error_text
# and hand it to the client. Nothing used to strip the userinfo.

_PROXY_LEAKS = [
    "ProxyError: failed to connect to http://bob:hunter2@proxy.example:3128",
    "httpx.ConnectError: [Errno 111] connecting to https://user%40corp:s3cr3t@10.0.0.1:8080",
    "curl_cffi error using socks5://admin:letmein@127.0.0.1:1080 — timed out",
]


@pytest.mark.parametrize("text", _PROXY_LEAKS)
def test_proxy_userinfo_never_survives_redaction(text):
    cleaned = redact_error_text(text)

    assert "hunter2" not in cleaned
    assert "s3cr3t" not in cleaned
    assert "letmein" not in cleaned
    assert "<redacted>@" in cleaned


def test_redaction_keeps_the_host_so_the_error_stays_useful():
    cleaned = redact_error_text("failed to connect to http://bob:hunter2@proxy.example:3128")

    assert "proxy.example:3128" in cleaned
    assert "bob" not in cleaned


def test_redact_url_strips_userinfo_too():
    cleaned = redact_url("https://user:pass@api.example/path?token=abcdef")

    assert "pass" not in cleaned
    assert "api.example" in cleaned
    assert "token=<redacted>" in cleaned


def test_a_plain_url_is_left_alone():
    url = "https://www.wildberries.ru/catalog/12345/detail.aspx"

    assert redact_url(url) == url


def test_an_email_in_prose_is_not_mangled():
    """The userinfo rule is anchored to :// so ordinary text stays readable."""
    text = "seller contact was support@example.com"

    assert redact_error_text(text) == text


# ------------------------------------------------------------------ other secrets ----


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc123def456ghi789jkl", "abc123def456ghi789jkl"),
        ("GET /v1?api_key=supersecretvalue123", "supersecretvalue123"),
        ("https://x.test/a?token=zzzzzzzzzzzzzzzzzzzz", "zzzzzzzzzzzzzzzzzzzz"),
        ("key sk-abcdefghijklmnopqrstuvwxyz01", "abcdefghijklmnopqrstuvwxyz01"),
        ("token ghp_abcdefghijklmnopqrstuvwxyz01", "abcdefghijklmnopqrstuvwxyz01"),
        ("aws AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ],
)
def test_known_secret_shapes_are_scrubbed(text, secret):
    assert secret not in redact_error_text(text)


_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize(
    "text",
    [
        f"connect failed; Cookie: mp_auth={_JWT}; region=ru",
        f"request body carried mp_auth={_JWT} inline",
        f"session_token={_JWT}&next=1",
        f"upstream rejected {_JWT}",
    ],
)
def test_session_cookies_and_jwts_are_scrubbed(text):
    """The MPStats connector holds the only secret this project ever sees — a
    live paid-session JWT. Nothing is known to put it in an error string today,
    but its docstring claimed this scrubbing existed before it did, so the claim
    is pinned here rather than trusted."""
    assert _JWT not in redact_error_text(text)
    assert "eyJ" not in redact_error_text(text)


def test_redaction_keeps_the_diagnosis_readable():
    """Scrubbing must not eat the part that says what went wrong."""
    out = redact_error_text(f"POST plugin.mpstats.io timed out; Cookie: mp_auth={_JWT}")
    assert "plugin.mpstats.io" in out
    assert "timed out" in out


def test_ordinary_text_survives_untouched():
    """A cookie-shaped regex that also eats normal words would hide real errors."""
    plain = "card.wb.ru returned 429 for nm=176700771 after 3 retries"
    assert redact_error_text(plain) == plain


def test_output_is_capped():
    assert len(redact_error_text("x" * 5000)) == 500


def test_empty_input_is_safe():
    assert redact_error_text("") == ""
    assert redact_url("") == ""
