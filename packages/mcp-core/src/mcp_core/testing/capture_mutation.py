"""Derandomized m1-m4 mutations of committed parser inputs, without transports."""

# START_MODULE_CONTRACT
# PURPOSE: Mutate raw JSON/HTML captures and check equivalence/data-truth invariants.
# SCOPE: m1-m4 only; explicit Random instances; no connector or test-framework imports.
# DEPENDS: stdlib copy, html, html.parser, json, random, re, dataclasses, collections.abc, typing.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# equivalent: Add harmless token whitespace, reorder attributes, add an unrelated key/node.
# remove_price/decoy_price: Remove one price node/key or replace one price value.
# truncate: Cut at a seeded raw offset, optionally selecting a valid JSON prefix.
# raw_amounts: Collect complete numeric tokens from the clean capture, including grouped/decimal amounts.
# assert_oracle: Check known items by id and price field; validate fallback amounts against the clean capture.
# run_mutations: Drive a pure parser, count invalid JSON skips, honor exact exclusions.
# END_MODULE_MAP

from __future__ import annotations

import copy
import html
import json
import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

# START_BLOCK_VOLUMES
PYTHON_SEEDS = 50  # Per fixture and mutation: 200 cases across m1-m4.
JSDOM_SEEDS = 7  # Per fixture and mutation: 28 cases across m1-m4.
# END_BLOCK_VOLUMES

# START_BLOCK_JSON


@dataclass
class _Embedded:
    value: Any


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_expand(child) for child in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _Embedded(_expand(json.loads(value)))
        except json.JSONDecodeError:
            pass
    return value


def _collapse(value: Any) -> Any:
    if isinstance(value, _Embedded):
        return json.dumps(_collapse(value.value), ensure_ascii=False)
    if isinstance(value, dict):
        return {key: _collapse(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_collapse(child) for child in value]
    return value


def _json_targets(value: Any, patterns: tuple[str, ...], path: str = "") -> list[tuple[dict, str]]:
    targets: list[tuple[dict, str]] = []
    if isinstance(value, _Embedded):
        return _json_targets(value.value, patterns, path)
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            # Only scalar amount fields; never replace price currency or a whole object.
            if (
                any(re.search(pattern, child_path) for pattern in patterns)
                and not isinstance(child, (dict, list, _Embedded))
                and child is not None
            ):
                targets.append((value, key))
            targets.extend(_json_targets(child, patterns, child_path))
    elif isinstance(value, list):
        for child in value:
            targets.extend(_json_targets(child, patterns, f"{path}[]"))
    return targets


def _json_mutation(raw: str, rng: random.Random, patterns: tuple[str, ...], marker: str | None) -> str:
    value = copy.deepcopy(_expand(json.loads(raw)))
    targets = _json_targets(value, patterns)
    if not targets:
        return raw
    parent, key = rng.choice(targets)
    if marker is None:
        del parent[key]
    else:
        parent[key] = marker
    return json.dumps(_collapse(value), ensure_ascii=False)


def _json_whitespace(raw: str, rng: random.Random) -> str:
    # Token boundaries, not string contents: escaped quotes and backslashes are preserved.
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[^\s"{}\[\],:]+|[{}\[\],:]', raw)
    return "".join(token + rng.choice((" ", "\n", "\t", " \n ")) for token in tokens)


# END_BLOCK_JSON
# START_BLOCK_HTML


@dataclass
class _Node:
    start: int
    content: int
    end_content: int
    end: int
    tag: str
    opening: str


class _Markup(HTMLParser):
    def __init__(self, raw: str) -> None:
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", raw))
        self.nodes: list[_Node] = []
        self.stack: list[_Node] = []
        self.feed(raw)
        self.close()

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._offset()
        opening = self.get_starttag_text() or ""
        end = start + len(opening)
        node = _Node(start, end, end, end, tag, opening)
        self.nodes.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] is self.nodes[-1]:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            node = self.stack[index]
            if node.tag == tag:
                node.end_content = self._offset()
                close = self.raw.find(">", node.end_content)
                node.end = len(self.raw) if close < 0 else close + 1
                del self.stack[index:]
                return


def _html_json(raw: str) -> list[tuple[int, int, str, str]]:
    regions = []
    for node in _Markup(raw).nodes:
        if node.tag in {"script", "noframes"} and node.end_content > node.content:
            text = raw[node.content : node.end_content]
            try:
                json.loads(text)
            except json.JSONDecodeError:
                continue
            regions.append((node.content, node.end_content, text, ""))
        # Yandex's HTML-escaped productSnippet payload is also genuine parser input.
        for match in re.finditer(r"""\bdata-zone-data\s*=\s*(["'])(.*?)\1""", node.opening, re.S):
            text = html.unescape(match[2])
            try:
                json.loads(text)
            except json.JSONDecodeError:
                continue
            regions.append((node.start + match.start(2), node.start + match.end(2), text, match[1]))
    return regions


def _html_mutation(
    raw: str, rng: random.Random, patterns: tuple[str, ...], nodes: tuple[str, ...], marker: str | None
) -> str:
    regions = [region for region in _html_json(raw) if _json_targets(_expand(json.loads(region[2])), patterns)]
    matches = [
        node
        for node in _Markup(raw).nodes
        if any(
            re.search(pattern[6:], html.unescape(raw[node.content : node.end_content]))
            if pattern.startswith("@text:") and node.tag in {"div", "span", "del"}
            else re.search(pattern, node.opening)
            for pattern in nodes
        )
    ]
    # Prefer the actual price leaf over a wrapper containing another matched node.
    matches = [node for node in matches if not any(node.content <= other.start < node.end_content for other in matches)]
    choices: list[Any] = [*(("json", region) for region in regions), *(("node", node) for node in matches)]
    if not choices:
        return raw  # A committed empty/unpriced control has no amount to mutate.
    kind, target = rng.choice(choices)
    if kind == "json":
        start, end, text, quote = target
        replacement = _json_mutation(text, rng, patterns, marker)
        if quote:
            replacement = html.escape(replacement, quote=True)
        return raw[:start] + replacement + raw[end:]
    node = target
    if marker is None:
        return raw[: node.start] + raw[node.end :]
    if node.tag in {"meta", "input"} or "data-meta-price" in node.opening:
        replacement = re.sub(
            r"""\b(content|value|data-meta-price)\s*=\s*(["']).*?\2""",
            lambda match: f'{match[1]}="{html.escape(marker, quote=True)}"',
            node.opening,
            count=1,
            flags=re.S,
        )
        return raw[: node.start] + replacement + raw[node.end :]
    return raw[: node.content] + html.escape(marker) + raw[node.end_content :]


# END_BLOCK_HTML
# START_BLOCK_MUTATORS


def equivalent(raw: str, rng: random.Random, *, kind: str = "json") -> str:
    if kind == "json":
        value = json.loads(raw)
        if isinstance(value, dict):
            key = "_p21_unrelated"
            while key in value:
                key += "_"
            value[key] = {"seed": rng.randrange(1_000_000)}
        elif isinstance(value, list):
            objects = [item for item in value if isinstance(item, dict)]
            if objects:
                parent = rng.choice(objects)
                key = "_p21_unrelated"
                while key in parent:
                    key += "_"
                parent[key] = rng.randrange(1_000_000)
        return _json_whitespace(json.dumps(value, ensure_ascii=False), rng)
    markup = _Markup(raw)
    candidates = []
    for node in markup.nodes:
        attrs = re.findall(r"""[^\s=<>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?""", node.opening)
        if len(attrs) > 2:
            candidates.append((node, attrs))
    if candidates:
        node, attrs = rng.choice(candidates)
        shuffled = attrs[1:]
        rng.shuffle(shuffled)
        suffix = "/>" if node.opening.endswith("/>") else ">"
        opening = "<" + attrs[0] + " " + " ".join(shuffled) + suffix
        raw = raw[: node.start] + opening + raw[node.content :]
    # Head-only whitespace/node: body_snippet and tile text are part of output too.
    head = re.search(r"</head\s*>", raw, re.I)
    insertion = head.start() if head else len(raw)
    unrelated = rng.choice(("\n", "\t", " \n")) + '<meta data-p21="unrelated">'
    return raw[:insertion] + unrelated + raw[insertion:]


def remove_price(
    raw: str,
    rng: random.Random,
    *,
    kind: str = "json",
    price_paths: tuple[str, ...] = (),
    price_nodes: tuple[str, ...] = (),
) -> str:
    if kind == "json":
        return _json_mutation(raw, rng, price_paths, None)
    return _html_mutation(raw, rng, price_paths, price_nodes, None)


def decoy_price(
    raw: str,
    rng: random.Random,
    marker: str,
    *,
    kind: str = "json",
    price_paths: tuple[str, ...] = (),
    price_nodes: tuple[str, ...] = (),
) -> str:
    if kind == "json":
        return _json_mutation(raw, rng, price_paths, marker)
    return _html_mutation(raw, rng, price_paths, price_nodes, marker)


def truncate(raw: str, rng: random.Random, *, valid_json_prefix: bool = False) -> str:
    if valid_json_prefix:
        # Ordinary JSON has almost no valid proper prefixes. Stratify half the
        # seeds over its complete-token/trailing-whitespace offsets; the other
        # half still samples arbitrary raw offsets and records decode skips.
        leading = len(raw) - len(raw.lstrip())
        _, end = json.JSONDecoder().raw_decode(raw.lstrip())
        offset = rng.randrange(leading + end, len(raw) + 1)
    else:
        offset = rng.randrange(len(raw) + 1)
    return raw[:offset]


# END_BLOCK_MUTATORS
# START_BLOCK_ORACLE


def _values(output: Any, fields: tuple[str, ...]) -> list[Any]:
    values = []
    if isinstance(output, dict):
        for key, value in output.items():
            if key in fields:
                values.append(value)
            values.extend(_values(value, fields))
    elif isinstance(output, (list, tuple)):
        for item in output:
            values.extend(_values(item, fields))
    return values


def raw_amounts(clean_raw: str) -> set[int | float]:
    amounts: set[int | float] = set()
    for match in re.finditer(r"\d+(?:[ \u00a0\u2009\u202f]+\d+)*(?:[.,]\d+)?", clean_raw):
        token = re.sub(r"[ \u00a0\u2009\u202f]", "", match[0]).replace(",", ".")
        amounts.add(float(token) if "." in token else int(token))
    return amounts


def _objects(output: Any) -> Iterable[dict[str, Any]]:
    if isinstance(output, dict):
        yield output
        for child in output.values():
            yield from _objects(child)
    elif isinstance(output, (list, tuple)):
        for child in output:
            yield from _objects(child)


def _identities(item: dict[str, Any], id_fields: tuple[str, ...]) -> list[tuple[str, Any]]:
    return [(field, item[field]) for field in id_fields if item.get(field) is not None and item[field] != ""]


# START_CONTRACT: assert_oracle
# PURPOSE: Keep m1 exact; reject wrong-item/field prices and amounts invented by truncation or decoys.
# INPUTS: Clean raw text and clean/mutated outputs; emitted price/id fields; None/empty ids are unknown.
# OUTPUTS: None on compliance; AssertionError naming a violated price or non-equivalent output otherwise.
# SIDE_EFFECTS: None; known ids require their own clean field value or None. New/unknown ids and prices
# outside identified dicts require None or a full clean-raw numeric token/clean-output price.
# END_CONTRACT: assert_oracle


def assert_oracle(
    clean: Any,
    mutated: Any,
    *,
    clean_raw: str,
    price_fields: tuple[str, ...],
    id_fields: tuple[str, ...],
    equivalent_output: bool = False,
) -> None:
    if equivalent_output:
        assert mutated == clean, "m1 changed the clean parser output"
        return
    clean_items: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for item in _objects(clean):
        for identity in _identities(item, id_fields):
            clean_items.setdefault(identity, []).append(item)
    amounts: set[Any] = raw_amounts(clean_raw)
    amounts.update(value for value in _values(clean, price_fields) if value is not None)
    for item in _objects(mutated):
        known_ids = [identity for identity in _identities(item, id_fields) if identity in clean_items]
        for field in price_fields:
            value = item.get(field)
            if value is None:
                continue
            if known_ids:
                assert all(
                    any(value == original.get(field) for original in clean_items[identity]) for identity in known_ids
                ), f"invented price {field}={value!r} for item {known_ids!r}; differs from its clean field"
            else:
                assert value in amounts, f"invented price {field}={value!r}; absent from clean capture/output"


# END_BLOCK_ORACLE
# START_BLOCK_DRIVER


def mutation_case(
    raw: str,
    mutation: str,
    seed: int,
    *,
    kind: str,
    price_paths: tuple[str, ...],
    price_nodes: tuple[str, ...],
    markers: tuple[str, ...],
) -> str:
    rng = random.Random(seed)
    if mutation == "m1":
        return equivalent(raw, rng, kind=kind)
    if mutation == "m2":
        return remove_price(raw, rng, kind=kind, price_paths=price_paths, price_nodes=price_nodes)
    if mutation == "m3":
        return truncate(raw, rng, valid_json_prefix=kind == "json" and seed % 2 == 0)
    if mutation == "m4":
        return decoy_price(
            raw, rng, markers[seed % len(markers)], kind=kind, price_paths=price_paths, price_nodes=price_nodes
        )
    raise ValueError(f"unknown mutation {mutation!r}")


def check_case(
    raw: str,
    clean: Any,
    parse: Callable[[str], Any],
    *,
    clean_raw: str,
    mutation: str,
    kind: str,
    price_fields: tuple[str, ...],
    id_fields: tuple[str, ...],
    allowed_errors: tuple[type[Exception], ...],
) -> bool:
    if kind == "json":
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            return False  # Outside the parser, not an allowed parser exception.
    try:
        mutated = parse(raw)
    except allowed_errors:
        assert mutation != "m1", "m1 raised a documented error instead of identical output"
        return True
    assert_oracle(
        clean,
        mutated,
        clean_raw=clean_raw,
        price_fields=price_fields,
        id_fields=id_fields,
        equivalent_output=mutation == "m1",
    )
    return True


def run_mutations(
    raw: str,
    parse: Callable[[str], Any],
    *,
    fixture: str,
    mutation: str,
    kind: str,
    price_paths: tuple[str, ...],
    price_nodes: tuple[str, ...] = (),
    price_fields: tuple[str, ...],
    id_fields: tuple[str, ...],
    markers: tuple[str, ...],
    allowed_errors: tuple[type[Exception], ...] = (),
    seeds: int = PYTHON_SEEDS,
    exclusions: Iterable[tuple[str, str, int]] = (),
) -> dict[str, int]:
    clean = parse(raw)
    excluded = set(exclusions)
    reached = skipped = omitted = 0
    for seed in range(seeds):
        if (fixture, mutation, seed) in excluded:
            omitted += 1
            continue
        mutated = mutation_case(
            raw, mutation, seed, kind=kind, price_paths=price_paths, price_nodes=price_nodes, markers=markers
        )
        try:
            if mutation in {"m2", "m4"} and any(value is not None for value in _values(clean, price_fields)):
                assert mutated != raw, "priced capture has no mutation target"
            ran = check_case(
                mutated,
                clean,
                parse,
                clean_raw=raw,
                mutation=mutation,
                kind=kind,
                price_fields=price_fields,
                id_fields=id_fields,
                allowed_errors=allowed_errors,
            )
        except Exception as exc:
            raise AssertionError(f"{fixture}, {mutation}, seed={seed}: {type(exc).__name__}: {exc}") from exc
        reached += int(ran)
        skipped += int(not ran)
    if mutation == "m3" and kind == "json":
        assert reached >= (seeds + 1) // 2, f"only {reached}/{seeds} truncation seeds reached the parser"
    return {"reached": reached, "json_skips": skipped, "excluded": omitted}


# END_BLOCK_DRIVER
