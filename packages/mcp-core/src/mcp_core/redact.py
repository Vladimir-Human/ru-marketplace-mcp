# START_MODULE_CONTRACT
# PURPOSE: Scrub recognized credentials and secret env var values from error text, diagnostics and JSON-shaped values.
# SCOPE: Pattern-based redaction and bounded error output; not general PII removal.
# DEPENDS: stdlib os,re,typing.
# LINKS: M-REDACT; M-CORE; M-LOGGING; M-ERRORS.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _strip_userinfo: Remove user:password material up to the last URL-token at sign.
# _redact_env_values: Replace the exact current values of secret environment variables, longest first.
# redact_error_text: Scrub known token/cookie/JWT shapes before truncating text.
# redact_url: Scrub sensitive query parameters and proxy userinfo.
# redact_value: Redact every string in a JSON-shaped value, leaving other types.
# END_MODULE_MAP
from __future__ import annotations

import os
import re
from typing import Any

_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_API_KEY_RE = re.compile(r"(api[_-]?key=)[^&\s\"']+", re.IGNORECASE)
_TOKEN_QUERY_RE = re.compile(r"([?&](?:token|key|access_token|api_key)=)[^&\s\"']+", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*(?:Bearer|Basic|Token)\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SK_RE = re.compile(r"(sk-)[A-Za-z0-9]{20,}")
_GHP_RE = re.compile(r"(gh[pousr]_)[A-Za-z0-9]{20,}")
_AKIA_RE = re.compile(r"(AKIA)[0-9A-Z]{16}")
# Proxies are configured as http://user:pass@host:port (see *_PROXY), and a
# connect failure puts that whole URL into the exception text — which then
# travels into stderr logs and into the ToolError the client sees. The
# userinfo is everything between "://" and the LAST '@' of the URL token:
# passwords in the wild carry a second '@' and even '/' (which RFC 3986
# forbids in userinfo, but proxy configs do anyway), and splitting at the
# first '@' or stopping at '/' leaks the tail of the credential. The userinfo
# must contain ':' (the user:pass shape): that keeps a path-embedded '@'
# (https://cdn.example/photo@2x.png) and a bare username out of the blast
# radius. A password containing a space splits the URL token itself, so no
# text-level rule can redact it reliably — treat such proxies as unsupported.
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+")


def _strip_userinfo(url: str) -> str:
    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    userinfo, _, hostish = rest.rpartition("@")
    if ":" not in userinfo:
        return url
    if not hostish and "/" in userinfo:
        # Two shapes look alike here: a path capped with '@'
        # (https://host:8080/a@) and a credential truncated at the host
        # (https://user:p/ss@). The segment between the first ':' and the '/'
        # tells them apart: a numeric port means a path, anything else means a
        # password carrying '/' — and a leaked truncated credential is still a
        # leak, so only the port shape is kept readable.
        _, _, after_colon = userinfo.partition(":")
        if after_colon.split("/", 1)[0].isdigit():
            return url
    return f"{scheme}{sep}<redacted>@{hostish}"


# A whole Cookie header, and any cookie whose name ends in auth/token/session.
# The MPStats connector sends `mp_auth=<JWT>`: a live paid session, the only
# secret this project ever handles. httpx keeps headers out of exception text,
# so nothing is known to leak today — but a docstring promised this scrubbing
# existed before it did, and a secret that valuable deserves the belt as well
# as the braces.
_COOKIE_HEADER_RE = re.compile(r"(Cookie:\s*)[^\r\n]+", re.IGNORECASE)
_COOKIE_PAIR_RE = re.compile(r"\b([A-Za-z0-9_-]*(?:auth|token|session)=)[^;,\s\"']+", re.IGNORECASE)
# A bare JWT, in case one reaches an error string outside a cookie: three
# base64url segments, the first always starting `eyJ` ({" encoded).
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")

_PLACEHOLDER = "<redacted>"

# The pattern rules above recognize shapes, not values: a secret env var this
# project reads whose value carries no token/cookie/URL-credential shape would
# sail through them, so its exact current value is replaced too. MPSTATS_MP_AUTH
# is the paid MPStats cookie (mpstats-connector settings) and MCP_HTTP_AUTH_TOKEN
# is the HTTP serving bearer token (runtime.ENV_HTTP_AUTH_TOKEN, spelled here
# because importing runtime would cycle: redact <- logging <- runtime). A
# connector that reads a new secret adds its name here — one line of data, no
# code. Proxy vars stay out: their credential part is already covered by the
# URL userinfo rule and their host is deliberately kept readable.
_SECRET_ENV_VARS = ("MPSTATS_MP_AUTH", "MCP_HTTP_AUTH_TOKEN")
# Shorter values are ignored: real credentials are far longer, while "1" or
# "debug" set in the environment would otherwise eat those ordinary words out
# of every diagnostic. Six keeps the shortest plausible secrets in scope.
_SECRET_ENV_MIN_LEN = 6


def _redact_env_values(text: str) -> str:
    """Replace the exact current values of the secret environment variables."""
    values: set[str] = set()
    for name in _SECRET_ENV_VARS:
        # Read per call, never cached at import time, so a value set later is
        # still covered. Connectors strip a value before using it, so both
        # spellings can leak.
        raw = os.environ.get(name) or ""
        for value in (raw, raw.strip()):
            if len(value) < _SECRET_ENV_MIN_LEN or value in _PLACEHOLDER:
                # A value occurring inside the placeholder is not a credential
                # ("redact"), and replacing it would make the second pass chew
                # the placeholder itself, breaking idempotence.
                continue
            values.add(value)
    # Longest first: when one value contains another, replacing the shorter one
    # first would leave the longer value's tail in the text. Plain str.replace,
    # no per-call regex.
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, _PLACEHOLDER)
    return text


def redact_error_text(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    redacted = _redact_env_values(text)
    redacted = _BEARER_RE.sub(r"\1<redacted>", redacted)
    redacted = _API_KEY_RE.sub(r"\1<redacted>", redacted)
    redacted = _TOKEN_QUERY_RE.sub(r"\1<redacted>", redacted)
    redacted = _AUTH_HEADER_RE.sub(r"\1<redacted>", redacted)
    redacted = _SK_RE.sub(r"\1<redacted>", redacted)
    redacted = _GHP_RE.sub(r"\1<redacted>", redacted)
    redacted = _AKIA_RE.sub(r"\1<redacted>", redacted)
    redacted = _URL_RE.sub(lambda m: _strip_userinfo(m.group(0)), redacted)
    redacted = _COOKIE_HEADER_RE.sub(r"\1<redacted>", redacted)
    redacted = _COOKIE_PAIR_RE.sub(r"\1<redacted>", redacted)
    redacted = _JWT_RE.sub("<redacted>", redacted)
    return redacted[:max_len]


def redact_url(url: str) -> str:
    if not url:
        return ""
    redacted = _TOKEN_QUERY_RE.sub(r"\1<redacted>", url)
    return _strip_userinfo(redacted)


def redact_value(value: Any) -> Any:
    """Redact every string inside a JSON-shaped value.

    Containers are rebuilt rather than mutated; non-string leaves are returned
    unchanged, so an int, bool or float field is never turned into text. This is
    what lets ``log_event`` and ``raise_tool_error`` redact by construction while
    callers keep passing the raw values they always did.
    """
    if isinstance(value, str):
        return redact_error_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, set):
        return {redact_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(redact_value(item) for item in value)
    return value
