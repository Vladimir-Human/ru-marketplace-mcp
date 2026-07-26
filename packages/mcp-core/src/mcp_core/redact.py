from __future__ import annotations

import re

_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_API_KEY_RE = re.compile(r"(api[_-]?key=)[^&\s\"']+", re.IGNORECASE)
_TOKEN_QUERY_RE = re.compile(r"([?&](?:token|key|access_token|api_key)=)[^&\s\"']+", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*(?:Bearer|Basic|Token)\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SK_RE = re.compile(r"(sk-)[A-Za-z0-9]{20,}")
_GHP_RE = re.compile(r"(gh[pousr]_)[A-Za-z0-9]{20,}")
_AKIA_RE = re.compile(r"(AKIA)[0-9A-Z]{16}")


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
    return redacted[:max_len]


def redact_url(url: str) -> str:
    if not url:
        return ""
    return _TOKEN_QUERY_RE.sub(r"\1<redacted>", url)
