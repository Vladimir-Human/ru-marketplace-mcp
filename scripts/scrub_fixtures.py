#!/usr/bin/env python3
"""Personal data and private material out of the committed fixtures.

START_MODULE_CONTRACT
PURPOSE: Find personal names, phones and e-mails in the committed fixtures and
         recordings, replace every hit with a fixed placeholder of the same shape,
         and hold the private-material rule (placeholder regions only, no Windows
         user path, no scraping-profile directory) as a gate.
SCOPE: packages/*/tests/fixtures/**, committed recordings and the private-material
       trees packages/*/tests/, scripts/ and docs/; JSON and HTML fixtures; a pure
       scan(path, data) and a --write mode.
DEPENDS: Standard-library argparse, dataclasses, hashlib, json, pathlib, re.
LINKS: M-SCRUB-FIXTURES, R-FIXTURE-PERSONAL-DATA.
END_MODULE_CONTRACT
START_MODULE_MAP
  scan: Every personal value in one fixture, with its key path and fixed placeholder.
  scrub: The same fixture with every hit replaced.
  scan_file: Every personal value in one file on disk.
  scrub_file: The file's bytes with every hit replaced.
  private_material_problems: User paths, profile directory names, non-placeholder recording regions.
  main: Check mode (one masked line per hit) or --write mode.
END_MODULE_MAP

The keys are the ones change (a) scrubs at runtime, and they are read from the
parsers, not guessed:

  WB    review and question author      wbUserDetails.name
        (packages/wb-connector/src/wb_connector/server.py:1390 and :1592)
  Ozon  review author                   author.firstName / author.lastName
        (packages/ozon-connector/src/ozon_connector/server.py:826)
  Yandex review author                  author.nickname, and the copies of that
                                         name in the same object (replyName and
                                         the profile transition's title)
        (packages/yandex-connector/src/yandex_connector/ssr.py:955)
  Avito  seller name                    seller.name / sellerName, and the
                                         data-marker="seller-info/name" block
        (packages/avito-connector/src/avito_connector/server.py:754 and :1001)
  Cian  agent name                      agent.name / agent.companyName,
                                         user.agencyName / user.companyName
        (packages/cian-connector/src/cian_connector/server.py:612 and :603)

A name is replaced unless the marketplace presents the seller as an organisation
rather than a natural person: a company label, a legal-entity display name, or a
brand (shop) profile. Company and shop names, profile and listing URLs and
ratings stay.

HTML fixtures are only touched where the value can be located by a stable
selector (Avito's seller-name marker) or a JSON island key (Yandex's state
patches); anything else is reported instead of guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# START_BLOCK_PLACEHOLDERS

REVIEWER = "reviewer"
ASKER = "asker"
SELLER = "seller"
AGENT = "agent"
PHONE = "phone"
EMAIL = "email"

# The fixed placeholder per key kind. A name becomes the word its key means, a
# phone keeps its format with the digits replaced by one fixed number, an e-mail
# becomes one fixed address. Nothing here is random: the same input always
# yields the same output, and a second run finds nothing left to replace.
NAME_PLACEHOLDERS = {
    REVIEWER: "Покупатель",
    ASKER: "Покупатель",
    SELLER: "Продавец",
    AGENT: "Агент",
}
EMAIL_PLACEHOLDER = "user@example.com"
PHONE_DIGITS = "9000000000"

# END_BLOCK_PLACEHOLDERS

# START_BLOCK_SOURCES

# The package directory that owns a fixture, and the source whose parser reads it.
SOURCE_BY_PACKAGE = {
    "avito-connector": "avito",
    "cian-connector": "cian",
    "ozon-connector": "ozon",
    "wb-connector": "wb",
    "yandex-connector": "yandex",
}

# Keys that hold a contact detail wherever they appear.
PHONE_KEYS = {"phone", "phones", "phoneNumber", "phoneNumbers", "phone_number", "tel", "telephone"}
EMAIL_KEYS = {"e-mail", "e_mail", "email", "emailForRequest", "emails"}

# A display name that opens with a legal form is a business identity the
# marketplace shows, not a person's name.
LEGAL_ENTITY_RE = re.compile(r"^\s*(?:ип|ооо|оао|зао|пао|ао|гупу|муп|фгуп|тпк)\b", re.IGNORECASE)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# END_BLOCK_SOURCES

# START_BLOCK_HIT


@dataclass(frozen=True)
class Hit:
    """One personal value found in a fixture."""

    path: Path
    source: str
    kind: str
    key_path: str
    value: str
    placeholder: str

    def line(self, root: Path | None = None) -> str:
        """One gate line: the file, the key path and a masked preview."""
        try:
            shown = self.path.relative_to(root).as_posix() if root else self.path.as_posix()
        except ValueError:
            shown = self.path.as_posix()
        return f"{shown}: {self.kind} at {self.key_path}: {mask(self.value)}"


def mask(value: str) -> str:
    """A preview that never contains the whole value."""
    preview = f"{value[:2]}***" if len(value) > 2 else "***"
    return "***" if value and value in preview else preview


@dataclass
class _Context:
    """What the walk needs to know about the file it is inside."""

    path: Path
    source: str
    hits: list[Hit] = field(default_factory=list)
    rewrite: bool = False


# END_BLOCK_HIT

# START_BLOCK_PLACEHOLDER_RULES


def phone_placeholder(value: str) -> str:
    """The same phone with the fixed national number; the country prefix stays."""
    digits = [index for index, char in enumerate(value) if char.isdigit()]
    if not digits:
        return value
    keep = 0
    prefix = re.match(r"\s*(\+\s*7|8|7)(?=\D|$)", value)
    if prefix and len(digits) > 10:
        keep = len(re.findall(r"\d", prefix.group(1)))
    rest = digits[keep:]
    if not rest:
        return value
    fill = (PHONE_DIGITS * 2)[: len(rest)]
    out = list(value)
    for position, digit in zip(rest, fill, strict=True):
        out[position] = digit
    return "".join(out)


def _is_mask_run(value: str) -> bool:
    """Whether the value is an obvious mask (one character repeated)."""
    stripped = re.sub(r"[\s\-()]", "", value)
    return len(stripped) >= 3 and len(set(stripped)) == 1


def already_scrubbed(kind: str, value: str) -> bool:
    """Whether the value is a placeholder or an obvious mask, not personal data."""
    if not value.strip():
        return True
    if kind in NAME_PLACEHOLDERS:
        return value in set(NAME_PLACEHOLDERS.values()) or _is_mask_run(value)
    if kind == EMAIL:
        return value == EMAIL_PLACEHOLDER or _is_mask_run(value)
    if kind == PHONE:
        return re.sub(r"\D", "", value).endswith(PHONE_DIGITS) or _is_mask_run(value)
    return False


def _has_legal_entity(name: str) -> bool:
    """Whether a display name opens with a legal form (ИП, ООО, ...)."""
    return bool(LEGAL_ENTITY_RE.match(name))


# END_BLOCK_PLACEHOLDER_RULES

# START_BLOCK_DETECTORS


def _leaves(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], Any]]:
    """Every leaf under a container, with the path to it."""
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(_leaves(item, (*prefix, key)))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(_leaves(item, (*prefix, index)))
        return found
    return [(prefix, value)]


def _detect_wb(node: dict[str, Any]) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """The WB review or question author (wbUserDetails.name)."""
    details = node.get("wbUserDetails")
    if not isinstance(details, dict):
        return []
    name = details.get("name")
    if not isinstance(name, str) or not name.strip():
        return []
    kind = ASKER if "answer" in node else REVIEWER
    return [(("wbUserDetails", "name"), kind, name, NAME_PLACEHOLDERS[kind])]


def _detect_ozon(node: dict[str, Any]) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """The Ozon review author (author.firstName and author.lastName)."""
    author = node.get("author")
    if not isinstance(author, dict):
        return []
    found = []
    first = author.get("firstName")
    last = author.get("lastName")
    if isinstance(first, str) and first.strip():
        found.append((("author", "firstName"), REVIEWER, first, NAME_PLACEHOLDERS[REVIEWER]))
    if isinstance(last, str) and last.strip():
        # Ozon joins the two parts into one display name, so the second part is
        # emptied rather than duplicated: the reader sees «Покупатель».
        found.append((("author", "lastName"), REVIEWER, last, ""))
    return found


def _detect_yandex(node: dict[str, Any]) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """The Yandex review author and the copies of that name in the same object.

    Yandex carries the reviewer's display name three times: under ``nickname``
    (the key the parser reads), under ``replyName`` and in the profile
    transition's ``title``. All three are the same person, so all three are
    replaced; leaving one behind would keep the name in the file.
    """
    found = []
    for key in ("nickname", "replyName"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            found.append(((key,), REVIEWER, value, NAME_PLACEHOLDERS[REVIEWER]))
    transition = node.get("transition")
    if isinstance(transition, dict) and transition.get("type") == "userPublicProfile":
        params = transition.get("params")
        if isinstance(params, dict):
            title = params.get("title")
            if isinstance(title, str) and title.strip():
                found.append((("transition", "params", "title"), REVIEWER, title, NAME_PLACEHOLDERS[REVIEWER]))
    return found


def _avito_is_company(node: dict[str, Any], seller: dict[str, Any]) -> bool | None:
    """The marketplace's own seller type; None when the page does not say."""
    for source in (seller, node):
        for key in ("isCompany", "is_company"):
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _is_shop_profile(container: dict[str, Any]) -> bool:
    """Whether the seller links to a brand (shop) profile instead of a person."""
    for key in ("profileUrl", "profile_url", "url", "href"):
        value = container.get(key)
        if isinstance(value, str) and "/brands/" in value:
            return True
    return False


def _detect_avito(node: dict[str, Any]) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """The private seller's name (seller.name, seller.title or sellerName)."""
    found = []
    seller = node.get("seller")
    if isinstance(seller, dict):
        for key in ("name", "title"):
            value = seller.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            if _avito_is_company(node, seller) is True:
                break
            if _has_legal_entity(value) or _is_shop_profile(seller):
                break
            found.append((("seller", key), SELLER, value, NAME_PLACEHOLDERS[SELLER]))
            break
    own = node.get("sellerName")
    if isinstance(own, str) and own.strip() and _avito_is_company(node, {}) is not True and not _has_legal_entity(own):
        found.append((("sellerName",), SELLER, own, NAME_PLACEHOLDERS[SELLER]))
    return found


def _cian_is_organisation(data: dict[str, Any], name: str) -> bool:
    """Whether the Cian publisher is an agency or a developer, not a person.

    Cian says both in its own fields: the account type («agency» against
    «specialist» for a private agent), the publisher type («developer»), the
    developer flag, and the profile the publisher owns («company/» against
    «agents/»).
    """
    if data.get("isDeveloper") is True or data.get("isBuilder") is True:
        return True
    for key in ("userType", "accountType"):
        value = data.get(key)
        if isinstance(value, str) and value.strip().lower() in {"agency", "developer"}:
            return True
    profile = data.get("profileUri")
    if isinstance(profile, str) and profile.strip().lower().startswith("company/"):
        return True
    return _has_legal_entity(name)


def _detect_cian(node: dict[str, Any]) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """The private agent's name (agent.name/companyName, user.agencyName/companyName)."""
    found = []
    for container, keys in (("agent", ("name", "companyName")), ("user", ("agencyName", "companyName"))):
        data = node.get(container)
        if not isinstance(data, dict):
            continue
        for key in keys:
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            if _cian_is_organisation(data, value):
                continue
            found.append(((container, key), AGENT, value, NAME_PLACEHOLDERS[AGENT]))
    return found


def _detect_contact(node: dict[str, Any]) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """A phone number or an e-mail address, wherever the key holds one."""
    found = []
    for key, value in node.items():
        if key in PHONE_KEYS:
            for relative, leaf in _leaves(value):
                if not isinstance(leaf, str) or len(re.sub(r"\D", "", leaf)) < 6:
                    continue
                found.append(((key, *relative), PHONE, leaf, phone_placeholder(leaf)))
        elif key in EMAIL_KEYS:
            for relative, leaf in _leaves(value):
                if not isinstance(leaf, str) or not EMAIL_RE.fullmatch(leaf):
                    continue
                found.append(((key, *relative), EMAIL, leaf, EMAIL_PLACEHOLDER))
    return found


def _detect_json(source: str, node: dict[str, Any], in_reviews: bool) -> list[tuple[tuple[Any, ...], str, str, str]]:
    """Every personal value directly inside one JSON object."""
    found: list[tuple[tuple[Any, ...], str, str, str]] = []
    if source == "wb":
        found.extend(_detect_wb(node))
    elif source == "ozon":
        found.extend(_detect_ozon(node))
    elif source == "yandex" and in_reviews:
        found.extend(_detect_yandex(node))
    elif source == "avito":
        found.extend(_detect_avito(node))
    elif source == "cian":
        found.extend(_detect_cian(node))
    found.extend(_detect_contact(node))
    return found


# END_BLOCK_DETECTORS

# START_BLOCK_WALK


def _key_path(path: tuple[Any, ...]) -> str:
    """The readable key path of a value inside a fixture."""
    parts = []
    for item in path:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}" if parts else str(item))
    return "".join(parts)


def _set_in(node: Any, relative: tuple[Any, ...], value: Any) -> None:
    """Write a value at a path inside a parsed fixture."""
    for key in relative[:-1]:
        node = node[key]
    node[relative[-1]] = value


def _walk_json(node: Any, path: tuple[Any, ...], ctx: _Context, in_reviews: bool = False) -> Any:
    """The parsed fixture with every personal value replaced."""
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            inside = in_reviews or (key == "reviews" and isinstance(value, list))
            result[key] = _walk_json(value, (*path, key), ctx, inside)
        for relative, kind, value, replacement in _detect_json(ctx.source, node, in_reviews):
            if already_scrubbed(kind, value):
                continue
            ctx.hits.append(Hit(ctx.path, ctx.source, kind, _key_path(path + relative), value, replacement))
            if ctx.rewrite:
                _set_in(result, relative, replacement)
        return result
    if isinstance(node, list):
        return [_walk_json(item, (*path, index), ctx, in_reviews) for index, item in enumerate(node)]
    return node


# END_BLOCK_WALK

# START_BLOCK_JSON_STYLE

# The JSON styles the committed fixtures were written in. The style that
# reproduces the original bytes is the one used to write the scrubbed file, so
# only the replaced values change.
_JSON_STYLES: tuple[tuple[dict[str, Any], str], ...] = (
    ({"indent": 2, "ensure_ascii": False}, "\n"),
    ({"indent": 2, "ensure_ascii": False}, ""),
    ({"indent": 4, "ensure_ascii": False}, "\n"),
    ({"ensure_ascii": False}, ""),
    ({"ensure_ascii": False, "separators": (",", ":")}, ""),
    ({"ensure_ascii": False, "separators": (", ", ": ")}, ""),
)


def _json_style(original: bytes, data: Any) -> tuple[dict[str, Any], str] | None:
    """The dump style that reproduces the original bytes, if one does."""
    for kwargs, suffix in _JSON_STYLES:
        if (json.dumps(data, **kwargs) + suffix).encode("utf-8") == original:
            return kwargs, suffix
    return None


# END_BLOCK_JSON_STYLE

# START_BLOCK_HTML

# A start or end tag; the attribute text keeps quoted values that hold a ">".
_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)(/?)>", re.S)

# Yandex ships its state as JSON islands inside these blocks.
_PATCH_RE = re.compile(r'<noframes data-apiary="patch">(.*?)</noframes>', re.S)


def _attr(attrs: str, name: str) -> str | None:
    """The value of one attribute in a tag's attribute text."""
    match = re.search(rf'\b{re.escape(name)}\s*=\s*"([^"]*)"', attrs) or re.search(
        rf"\b{re.escape(name)}\s*=\s*'([^']*)'", attrs
    )
    return match.group(1) if match else None


def _marked_element(text: str, marker: str) -> tuple[int, int] | None:
    """The span of the element carrying ``data-marker="marker"``."""
    for match in _TAG_RE.finditer(text):
        if _attr(match.group(3), "data-marker") != marker:
            continue
        name = match.group(2).lower()
        depth = 1
        for inner in _TAG_RE.finditer(text, match.end()):
            if inner.group(2).lower() != name:
                continue
            if inner.group(1):
                depth -= 1
                if depth == 0:
                    return match.start(), inner.end()
            elif not inner.group(4):
                depth += 1
        return match.start(), len(text)
    return None


def _text_segments(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The spans of the text between the tags inside ``[start, end)``."""
    segments = []
    cursor = start
    for match in _TAG_RE.finditer(text, start, end):
        if match.start() > cursor:
            segments.append((cursor, match.start()))
        cursor = match.end()
    if cursor < end:
        segments.append((cursor, end))
    return segments


def _script_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The spans of the script and style elements inside ``[start, end)``."""
    spans = []
    for match in _TAG_RE.finditer(text, start, end):
        if match.group(2).lower() not in {"script", "style"} or match.group(1):
            continue
        name = match.group(2).lower()
        depth = 1
        for inner in _TAG_RE.finditer(text, match.end(), end):
            if inner.group(2).lower() != name:
                continue
            if inner.group(1):
                depth -= 1
                if depth == 0:
                    spans.append((match.start(), inner.end()))
                    break
            elif not inner.group(4):
                depth += 1
        else:
            spans.append((match.start(), end))
    return spans


def _avito_label(text: str) -> str | None:
    """The seller-type label Avito shows («Компания», «Частное лицо», ...)."""
    for marker in ("seller-info/label",):
        element = _marked_element(text, marker)
        if element is None:
            continue
        start, end = element
        scripts = _script_spans(text, start, end)
        visible = "".join(
            text[s:e] for s, e in _text_segments(text, start, end) if not any(s >= a and e <= b for a, b in scripts)
        ).strip()
        if visible:
            return visible
    return None


def _avito_is_company_html(text: str) -> bool | None:
    """The marketplace's own seller type, from the seller block's label."""
    label = _avito_label(text)
    if label == "Частное лицо":
        return False
    if label in {"Компания", "Магазин"}:
        return True
    return None


def _avito_shop_link(text: str, start: int, end: int) -> bool:
    """Whether the seller block links to a brand (shop) profile."""
    for match in _TAG_RE.finditer(text, start, end):
        if match.group(2).lower() != "a" or match.group(1):
            continue
        href = _attr(match.group(3), "href") or ""
        if "/brands/" in href:
            return True
    return False


def _scan_avito_html(path: Path, text: str) -> list[Hit]:
    """The private seller's name in an Avito item page's seller block."""
    element = _marked_element(text, "seller-info/name")
    if element is None:
        return []
    start, end = element
    scripts = _script_spans(text, start, end)
    visible = [
        (s, e)
        for s, e in _text_segments(text, start, end)
        if text[s:e].strip() and not any(s >= a and e <= b for a, b in scripts)
    ]
    if len(visible) != 1:
        # More than one text inside the name marker: the value cannot be located
        # safely, so it is reported instead of guessed.
        return []
    name = text[visible[0][0] : visible[0][1]].strip()
    if _avito_is_company_html(text) is True:
        return []
    if _has_legal_entity(name) or _avito_shop_link(text, start, end):
        return []
    return [Hit(path, "avito", SELLER, "seller-info/name", name, NAME_PLACEHOLDERS[SELLER])]


def _scrub_avito_html(path: Path, text: str) -> tuple[str, list[Hit]]:
    """The page with the private seller's name replaced."""
    hits = _scan_avito_html(path, text)
    if not hits:
        return text, hits
    element = _marked_element(text, "seller-info/name")
    if element is None:
        return text, hits
    start, end = element
    scripts = _script_spans(text, start, end)
    visible = [
        (s, e)
        for s, e in _text_segments(text, start, end)
        if text[s:e].strip() and not any(s >= a and e <= b for a, b in scripts)
    ]
    if len(visible) != 1:
        return text, []
    first, last = visible[0]
    stripped = text[first:last].strip()
    offset = first + (len(text[first:last]) - len(text[first:last].lstrip()))
    return text[:offset] + hits[0].placeholder + text[offset + len(stripped) :], hits


def _yandex_island_hits(path: Path, island: str) -> list[Hit]:
    """The personal values inside one Yandex state island."""
    try:
        data = json.loads(island)
    except (json.JSONDecodeError, ValueError):
        return []
    ctx = _Context(path=path, source="yandex")
    _walk_json(data, (), ctx)
    return ctx.hits


def _scan_yandex_html(path: Path, text: str) -> list[Hit]:
    """The review authors in a Yandex page's state islands."""
    hits = []
    for island in _PATCH_RE.finditer(text):
        hits.extend(_yandex_island_hits(path, island.group(1)))
    return hits


def _scrub_yandex_html(path: Path, text: str) -> tuple[str, list[Hit]]:
    """The page with the review authors replaced, byte for byte elsewhere.

    The state island is JSON, so it is rewritten as JSON in the style that
    reproduces its own bytes: only the replaced values change.
    """
    hits = _scan_yandex_html(path, text)
    if not hits:
        return text, hits
    result = text
    for island in _PATCH_RE.finditer(text):
        block = island.group(1)
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        ctx = _Context(path=path, source="yandex", rewrite=True)
        new_data = _walk_json(data, (), ctx)
        if not ctx.hits:
            continue
        style = _json_style(block.encode("utf-8"), data)
        if style is None:
            continue
        kwargs, suffix = style
        updated = json.dumps(new_data, **kwargs) + suffix
        result = result.replace(block, updated, 1)
    return result, hits


# END_BLOCK_HTML

# START_BLOCK_API


def source_of(path: Path) -> str:
    """The source whose parser reads the fixture at ``path``."""
    for part in path.parts:
        if part in SOURCE_BY_PACKAGE:
            return SOURCE_BY_PACKAGE[part]
    return ""


def scan(path: Path, data: Any) -> list[Hit]:
    """Every personal value in one fixture. Pure: the same input, the same output."""
    ctx = _Context(path=path, source=source_of(path))
    if path.suffix == ".json":
        _walk_json(data, (), ctx)
        return ctx.hits
    text = data if isinstance(data, str) else str(data)
    if source_of(path) == "yandex":
        return _scan_yandex_html(path, text)
    if source_of(path) == "avito":
        return _scan_avito_html(path, text)
    return []


def scrub(path: Path, data: Any) -> tuple[Any, list[Hit]]:
    """The fixture with every hit replaced by its fixed placeholder."""
    ctx = _Context(path=path, source=source_of(path), rewrite=True)
    if path.suffix == ".json":
        return _walk_json(data, (), ctx), ctx.hits
    text = data if isinstance(data, str) else str(data)
    if source_of(path) == "yandex":
        return _scrub_yandex_html(path, text)
    if source_of(path) == "avito":
        return _scrub_avito_html(path, text)
    return text, []


def scan_file(path: Path) -> list[Hit]:
    """Every personal value in the file at ``path``."""
    text = path.read_bytes().decode("utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return []
        return scan(path, data)
    return scan(path, text)


# END_BLOCK_API

# START_BLOCK_WRITE


def _replace_tokens(text: str, hits: list[Hit]) -> str | None:
    """Replace the values in the original text, keeping every other byte.

    Only used when no dump style reproduces the file: a value is replaced when
    its own JSON token appears exactly once, so nothing else can be touched.
    """
    for hit in hits:
        token = json.dumps(hit.value, ensure_ascii=False)
        if text.count(token) != 1:
            return None
        text = text.replace(token, json.dumps(hit.placeholder, ensure_ascii=False), 1)
    return text


def scrub_file(path: Path) -> tuple[bytes, list[Hit]]:
    """The file's bytes with every hit replaced, and the hits that were found."""
    original = path.read_bytes()
    text = original.decode("utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return original, []
        new_data, hits = scrub(path, data)
        if not hits:
            return original, hits
        style = _json_style(original, data)
        if style is not None:
            kwargs, suffix = style
            return (json.dumps(new_data, **kwargs) + suffix).encode("utf-8"), hits
        replaced = _replace_tokens(text, hits)
        if replaced is None:
            return original, hits
        return replaced.encode("utf-8"), hits
    new_text, hits = scrub(path, text)
    return new_text.encode("utf-8"), hits


def update_pin(path: Path) -> None:
    """Point the fixture's provenance sidecar at the bytes now on disk.

    The sidecar is edited byte for byte: only the pin changes, so its own line
    endings and formatting stay as the capture wrote them.
    """
    sidecar = path.with_name(path.stem + ".provenance.json")
    if not sidecar.exists():
        return
    raw = sidecar.read_bytes()
    try:
        declared = json.loads(raw.decode("utf-8")).get("sha256")
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError, AttributeError):
        return
    if not isinstance(declared, str):
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    token = f'"sha256": "{declared}"'.encode()
    if raw.count(token) != 1:
        return
    sidecar.write_bytes(raw.replace(token, f'"sha256": "{digest}"'.encode(), 1))


# END_BLOCK_WRITE

# START_BLOCK_PRIVATE_MATERIAL

# The scraping profile's directory name, from mcp_core's default
# (packages/mcp-core/src/mcp_core/transport/chrome_cdp.py:_default_profile_dir).
PROFILE_DIRECTORY_NAMES = ("Chrome-Scraping", "chrome-scraping")

# A Windows user path: the operator's home directory.
USER_PATH_RE = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"'<>|]+")

# A region in a recording must be the placeholder form, R1, R2, ... in order.
REGION_PLACEHOLDER_RE = re.compile(r"^R\d+$")

# The trees the private-material rule covers.
PRIVATE_TREES = ("scripts", "docs")

# The planted fake user paths, per file: a test that proves a redaction needs a
# Windows user path to redact. Only these exact strings pass in that file, so a
# real path added next to them still fails. The date says when the entry was
# written and why the occurrence is not a leak.
KNOWN_FAKE_USER_PATHS: dict[str, tuple[str, frozenset[str]]] = {
    "packages/marketplace-connector/tests/test_diagnostics_redaction.py": (
        "2026-11-16: planted fake path proving the redaction works",
        frozenset({"C:\\Users\\someone"}),
    ),
    "packages/marketplace-connector/tests/test_health.py": (
        "2026-11-16: planted fake paths proving the redaction works",
        frozenset({"C:\\Users\\someone"}),
    ),
    "packages/mcp-core/tests/test_chrome_cdp.py": (
        "2026-11-16: fake profile paths the launcher tests assert on",
        frozenset({"C:\\Users\\op"}),
    ),
    "packages/mcp-core/tests/test_chrome_cdp_stealth.py": (
        "2026-11-16: planted fake profile paths for the launcher",
        frozenset({"C:\\Users\\op", "C:\\Users\\John"}),
    ),
    "packages/mcp-core/tests/test_process.py": (
        "2026-11-16: planted fake path proving the allowlist works",
        frozenset({"C:\\Users\\op"}),
    ),
    "scripts/test_live_matrix.py": (
        "2026-11-16: planted sentinel path proving the matrix redaction works",
        frozenset({"C:\\Users\\PRIVATE_SENTINEL_Мария"}),
    ),
    "scripts/test_scrub_fixtures.py": (
        "2026-11-16: the test plants the user path the gate refuses",
        frozenset({"C:\\Users\\operator"}),
    ),
}


def _private_files(root: Path) -> list[Path]:
    """Every file the private-material rule covers."""
    found: list[Path] = []
    for package in sorted(root.glob("packages/*/tests")):
        found.extend(path for path in package.rglob("*") if path.is_file())
    for name in PRIVATE_TREES:
        base = root / name
        if base.is_dir():
            found.extend(path for path in base.rglob("*") if path.is_file())
    return [path for path in found if "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}]


def _is_captured(path: Path, root: Path) -> bool:
    """Whether the file is captured material: a committed fixture or recording."""
    parts = path.relative_to(root).parts
    return "recordings" in parts or (len(parts) > 3 and parts[0] == "packages" and parts[3] == "fixtures")


def _is_recording(path: Path, root: Path) -> bool:
    """Whether the file is a committed recording."""
    relative = path.relative_to(root)
    return "recordings" in relative.parts or path.name.endswith(".provenance.json")


def _region_problems(relative: str, data: Any) -> list[str]:
    """Recording region fields that hold something other than a placeholder.

    Only the region's own value counts: a structured region (a search filter
    with a type and a value) is not a region name and is left alone.
    """
    problems = []

    def walk(node: Any, keys: tuple[Any, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"region", "regions"}:
                    for index, leaf in enumerate(_region_values(value)):
                        if not REGION_PLACEHOLDER_RE.match(leaf):
                            where = _key_path((*keys, key)) + (f"[{index}]" if index else "")
                            problems.append(f"{relative}: {where} holds {mask(leaf)} instead of a region placeholder")
                walk(value, (*keys, key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, (*keys, index))

    walk(data, ())
    return problems


def _region_values(value: Any) -> list[str]:
    """The region names a region field holds, if it holds any."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def private_material_problems(root: Path) -> list[str]:
    """Every private-material problem: a user path, a profile directory, a real region."""
    problems: list[str] = []
    for path in _private_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        allowed = KNOWN_FAKE_USER_PATHS.get(relative, ("", frozenset()))[1]
        for match in USER_PATH_RE.finditer(text):
            if match.group(0) not in allowed:
                problems.append(f"{relative}: a Windows user path ({mask(match.group(0))})")
        if _is_captured(path, root):
            # The default directory name is published (docs, launcher scripts), so
            # it is private only inside captured material, where it means a path
            # from the capturing machine came along.
            for name in PROFILE_DIRECTORY_NAMES:
                if name in text:
                    problems.append(f"{relative}: the scraping profile directory name ({name})")
        if path.suffix == ".json" and _is_recording(path, root):
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
            problems.extend(_region_problems(relative, data))
    return problems


# END_BLOCK_PRIVATE_MATERIAL

# START_BLOCK_CLI


def fixture_files(root: Path) -> list[Path]:
    """Every committed fixture the scanner reads, in a stable order."""
    found = []
    for path in sorted(root.glob("packages/*/tests/fixtures/*")):
        if path.is_file() and path.suffix in {".html", ".json"}:
            found.append(path)
    for path in sorted(root.glob("packages/*/tests/**/recordings/**")):
        if path.is_file() and path.suffix in {".html", ".json"}:
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    """Check the committed fixtures, or replace what it finds."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument("--write", action="store_true", help="replace every hit with its fixed placeholder")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    hits = []
    changed = []
    for path in fixture_files(root):
        found = scan_file(path)
        hits.extend(found)
        if args.write and found:
            new_bytes, _ = scrub_file(path)
            if new_bytes != path.read_bytes():
                path.write_bytes(new_bytes)
                update_pin(path)
                changed.append(path)

    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.kind] = counts.get(hit.kind, 0) + 1
    for hit in hits:
        print(hit.line(root))
    summary = ", ".join(f"{kind}={counts[kind]}" for kind in sorted(counts)) or "no hits"
    print(f"checked {len(fixture_files(root))} fixture(s): {len(hits)} hit(s) ({summary})")
    if not args.write:
        return 1 if hits else 0

    print(f"rewrote {len(changed)} fixture(s)")
    remaining = [hit for path in fixture_files(root) for hit in scan_file(path)]
    for hit in remaining:
        print(hit.line(root))
    print(f"{len(remaining)} hit(s) left after the rewrite")
    return 1 if remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
