from __future__ import annotations

import re

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


def redact_error_text(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    redacted = _BEARER_RE.sub(r"\1<redacted>", text)
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
