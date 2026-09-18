"""Verify every fixture's sha256 pin against the fixture itself.

A provenance file pins the bytes it describes, and nothing checked that the pin
still matched. The citilink card pin was found stale by a manual audit on
2026-09-18 (declared daeb2646..., file 9f33c019..., and no line-ending
normalisation explains it), which is exactly the class this gate makes automatic.

Known exceptions live in KNOWN_STALE, each with a dated reason. An allowlist is
not a fix: it keeps new drift loud while one old case waits for its owner, and the
list is meant to shrink to empty.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# path -> why it is tolerated, dated. Empty is the healthy state: a pin that
# matches under raw, LF or CRLF needs no entry, and a resolved entry must be
# deleted - the gate fails while a quarantined pin is matching, on purpose.
KNOWN_STALE: dict[str, str] = {}


def pins() -> list[tuple[pathlib.Path, pathlib.Path, str]]:
    """Every (provenance, fixture, declared sha256) triple we can check."""
    found = []
    for prov in sorted(ROOT.glob("packages/*/tests/fixtures/*.provenance.json")):
        try:
            declared = json.loads(prov.read_text(encoding="utf-8")).get("sha256")
        except (OSError, json.JSONDecodeError):
            continue
        if not declared:
            continue
        fixture = prov.with_name(prov.name.replace(".provenance.json", ".html"))
        if not fixture.exists():
            fixture = prov.with_name(prov.name.replace(".provenance.json", ".json"))
        if fixture.exists():
            found.append((prov, fixture, declared))
    return found


def check() -> list[str]:
    problems = []
    for prov, fixture, declared in pins():
        rel = prov.relative_to(ROOT).as_posix()
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


def main() -> int:
    checked = len(pins())
    problems = check()
    for line in problems:
        print(line)
    print(f"checked {checked} pin(s): {'ok' if not problems else str(len(problems)) + ' problem(s)'}")
    if KNOWN_STALE:
        print(f"quarantined: {len(KNOWN_STALE)} known-stale pin(s), each with a dated reason")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
