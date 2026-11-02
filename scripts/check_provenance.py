"""Verify every fixture's sha256 pin against the fixture itself, and check that
the provenance set is complete.

A provenance file pins the bytes it describes, and nothing checked that the pin
still matched. The citilink card pin was found stale by a manual audit on
2026-09-18 (declared daeb2646..., file 9f33c019..., and no line-ending
normalisation explains it), which is exactly the class this gate makes automatic.

Completeness is part of the gate now. A sidecar that does not parse, carries no
usable sha256 pin or has no fixture to describe is a hole in the evidence, not a
neutral file: the gate used to skip all three and report "checked 0: ok" with
exit 0, so a missing hash, a broken JSON and an orphan sidecar all passed. The
same goes the other way round - a fixture in the covered directories that no
sidecar describes is evidence nobody captured. Both are failures unless the
path sits in KNOWN_EXCEPTIONS with a dated reason.

Known exceptions live in KNOWN_STALE (a pin that no longer matches its file)
and in KNOWN_EXCEPTIONS (a completeness gap), each with a dated reason. An
allowlist is not a fix: it keeps new drift loud while one old case waits for
its owner, and the list is meant to shrink to empty. An exception covers one
defect: a sidecar entry tolerates a missing pin, a fixture entry tolerates a
missing sidecar, and a resolved case is reported so its entry can go.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The naming convention: a sidecar sits next to the fixture it describes and
# carries the same stem.
SIDECAR_SUFFIX = ".provenance.json"
FIXTURE_SUFFIXES = (".html", ".json")

# path -> why it is tolerated, dated. Empty is the healthy state: a pin that
# matches under raw, LF or CRLF needs no entry, and a resolved entry must be
# deleted - the gate fails while a quarantined pin is matching, on purpose.
KNOWN_STALE: dict[str, str] = {}

# path -> the completeness gap it tolerates, dated. A sidecar without a sha256
# pin and a fixture without a sidecar are both listed here with the reason they
# are still open. The eight sidecars below never carried a pin: their capture
# happened before the gate existed, so the pin is missing since capture and
# nobody can re-derive it from the bytes that are committed today. The five
# fixtures below are not captures at all - their own test modules say so -
# so a sidecar would be invented provenance.
KNOWN_EXCEPTIONS: dict[str, str] = {
    "packages/aliexpress-connector/tests/fixtures/card.provenance.json": "2026-11-02: pin missing since capture",
    "packages/aliexpress-connector/tests/fixtures/search_grid.provenance.json": "2026-11-02: pin missing since capture",
    "packages/cian-connector/tests/fixtures/card_daily_live.provenance.json": "2026-11-02: pin missing since capture",
    "packages/cian-connector/tests/fixtures/card_rent_live.provenance.json": "2026-11-02: pin missing since capture",
    "packages/cian-connector/tests/fixtures/card_sale_live.provenance.json": "2026-11-02: pin missing since capture",
    "packages/cian-connector/tests/fixtures/search_daily_live.provenance.json": "2026-11-02: pin missing since capture",
    "packages/cian-connector/tests/fixtures/search_rent_live.provenance.json": "2026-11-02: pin missing since capture",
    "packages/cian-connector/tests/fixtures/search_sale_live.provenance.json": "2026-11-02: pin missing since capture",
    "packages/lamoda-connector/tests/fixtures/search_grid.html": (
        "2026-11-02: hand-modelled fixture, not a capture (the extractor test says so)"
    ),
    "packages/marketplace-connector/tests/fixtures/public_contract.json": (
        "2026-11-02: generated contract snapshot, not a capture"
    ),
    "packages/mcp-core/tests/fixtures/utf8_roundtrip.html": (
        "2026-11-02: hand-written UTF-8 roundtrip fixture, not a capture"
    ),
    "packages/taobao-connector/tests/fixtures/item_card.html": (
        "2026-11-02: hand-modelled fixture, not a capture (the extractor test says so)"
    ),
    "packages/taobao-connector/tests/fixtures/search_grid.html": (
        "2026-11-02: hand-modelled fixture, not a capture (the extractor test says so)"
    ),
}

# The defect codes a sidecar or a fixture can carry. KNOWN_EXCEPTIONS covers
# exactly one of them per path: "no_pin" for a sidecar, "no_sidecar" for a
# fixture.
NO_PIN = "no_pin"
NO_SIDECAR = "no_sidecar"


def _relative(path: pathlib.Path) -> str:
    """The repository-relative path of a file, as the gate prints it."""
    return path.relative_to(ROOT).as_posix()


def sidecars() -> list[pathlib.Path]:
    """Every provenance sidecar in the repository, in a stable order."""
    return sorted(ROOT.glob(f"packages/*/tests/fixtures/*{SIDECAR_SUFFIX}"))


def fixture_files() -> list[pathlib.Path]:
    """Every fixture file a sidecar could describe, in a stable order."""
    found = []
    for path in sorted(ROOT.glob("packages/*/tests/fixtures/*")):
        if not path.is_file() or path.name.endswith(SIDECAR_SUFFIX):
            continue
        if path.suffix in FIXTURE_SUFFIXES:
            found.append(path)
    return found


def _sidecar_for(fixture: pathlib.Path) -> pathlib.Path:
    """The sidecar that would describe a fixture."""
    return fixture.with_name(fixture.stem + SIDECAR_SUFFIX)


def _paired_fixtures(prov: pathlib.Path) -> list[pathlib.Path]:
    """The fixtures a sidecar could describe: its own stem with a fixture suffix."""
    stem = prov.name[: -len(SIDECAR_SUFFIX)]
    paired = []
    for suffix in FIXTURE_SUFFIXES:
        candidate = prov.with_name(stem + suffix)
        if candidate.exists():
            paired.append(candidate)
    return paired


def _is_sha256(value: str) -> bool:
    """Whether a string is a sha256 digest and not just something hex-shaped."""
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _declared_pin(prov: pathlib.Path) -> str | None:
    """The sha256 a sidecar declares, when it parses and declares one."""
    try:
        data = json.loads(prov.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    declared = data.get("sha256")
    return declared if isinstance(declared, str) else None


def pins() -> list[tuple[pathlib.Path, pathlib.Path, str]]:
    """Every (provenance, fixture, declared sha256) triple we can check."""
    found = []
    for prov in sidecars():
        declared = _declared_pin(prov)
        if declared is None or not _is_sha256(declared):
            continue
        paired = _paired_fixtures(prov)
        if len(paired) != 1:
            continue
        found.append((prov, paired[0], declared.lower()))
    return found


def _sidecar_defect(prov: pathlib.Path) -> tuple[str | None, str]:
    """What is wrong with a sidecar: a defect code and what to say about it."""
    try:
        data = json.loads(prov.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unparsable", "does not parse as JSON"
    if not isinstance(data, dict):
        return "unparsable", "does not parse as a JSON object"
    paired = _paired_fixtures(prov)
    if len(paired) > 1:
        return "ambiguous", f"describes more than one fixture ({', '.join(item.name for item in paired)})"
    if not paired:
        return "no_fixture", "has no fixture to describe"
    declared = data.get("sha256")
    if declared is None:
        return NO_PIN, "carries no sha256 pin"
    if not isinstance(declared, str) or not _is_sha256(declared):
        return "bad_pin", f"carries an unusable sha256 pin ({declared!r})"
    return None, ""


def _sidecar_problems() -> list[str]:
    """Every sidecar that cannot do its job."""
    problems = []
    for prov in sidecars():
        rel = _relative(prov)
        defect, detail = _sidecar_defect(prov)
        if defect is None:
            if rel in KNOWN_EXCEPTIONS:
                problems.append(f"{rel}: the KNOWN_EXCEPTIONS entry is not needed any more - delete it")
            continue
        if defect == NO_PIN and rel in KNOWN_EXCEPTIONS:
            continue
        problems.append(f"{rel}: {detail}")
    return problems


def _fixture_problems() -> list[str]:
    """Every fixture that no sidecar describes."""
    problems = []
    for fixture in fixture_files():
        rel = _relative(fixture)
        if _sidecar_for(fixture).exists():
            if rel in KNOWN_EXCEPTIONS:
                problems.append(f"{rel}: the KNOWN_EXCEPTIONS entry is not needed any more - delete it")
            continue
        if rel in KNOWN_EXCEPTIONS:
            continue
        problems.append(
            f"{rel}: no {fixture.stem}{SIDECAR_SUFFIX} sidecar - a captured fixture must carry its provenance"
        )
    return problems


def _pin_problems() -> list[str]:
    """Every pin that no longer describes its fixture."""
    problems = []
    for prov, fixture, declared in pins():
        rel = _relative(prov)
        raw = fixture.read_bytes()
        lf = raw.replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        actual = hashlib.sha256(raw).hexdigest()
        # A pin is legitimate under any of the three line-ending conventions: the
        # repository has both, because different contributors hashed different
        # checkouts. Only "none of them" is a defect. (Learned the hard way: the
        # first version compared raw bytes and LF only, so on a Windows CI checkout
        # it reported six healthy pins as stale - and called the CRLF-based citilink
        # pin broken, which it is not.)
        matches = declared in {actual, hashlib.sha256(lf).hexdigest(), hashlib.sha256(crlf).hexdigest()}
        if matches:
            if rel in KNOWN_STALE:
                problems.append(f"{rel}: pin now MATCHES - remove the KNOWN_STALE entry")
            continue
        if rel in KNOWN_STALE:
            continue
        problems.append(
            f"{rel}: declared {declared[:16]}... but {fixture.name} hashes to {actual[:16]}... "
            "(update the pin, or quarantine it in KNOWN_STALE with a dated reason)"
        )
    return problems


def check() -> list[str]:
    """Every problem the gate found, one human-readable line each."""
    problems: list[str] = []
    problems.extend(_sidecar_problems())
    problems.extend(_fixture_problems())
    problems.extend(_pin_problems())
    return problems


def main() -> int:
    checked = len(pins())
    problems = check()
    for line in problems:
        print(line)
    print(
        f"checked {checked} pin(s) in {len(sidecars())} sidecar(s): {'ok' if not problems else str(len(problems)) + ' problem(s)'}"
    )
    if KNOWN_STALE:
        print(f"quarantined: {len(KNOWN_STALE)} known-stale pin(s), each with a dated reason")
    if KNOWN_EXCEPTIONS:
        print(f"excepted: {len(KNOWN_EXCEPTIONS)} completeness gap(s), each with a dated reason")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
