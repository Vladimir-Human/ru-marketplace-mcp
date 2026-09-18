# v2.4.0 — 2026-09-18

Bounds for the CDP layer, reporting that explains itself, a corrected Yandex zone
parser, a machine-checkable eval verdict, and fixture privacy brought up to the
project's own standard.

## What is in it

**Reliability.** Navigations take a permit from a process-wide budget: three in
flight, one per host, with a per-host breaker that pauses a host rather than
queueing the rest of a fan-out behind it. The permit covers the navigation, not the
page — a retained challenge page waits for a human for minutes and must not hold its
host's slot. Refusal backoff is jittered so sources refused together do not retry in
lockstep. The handoff registry holds eight leases and bounds each one by lifetime
(900 s, never extended by a retry) and by idleness (600 s).

**Reporting.** A resumed read says what happened — resumed or fresh, challenge
cleared or still present, data moved or not (tri-state: "nothing to compare with" is
not "nothing changed"), compared by digest instead of by retaining the payload. A
busy page names the reason and a retry hint; a full registry says so; a caller's own
expired handle says it expired while a foreign handle stays opaque.

**Correctness.** Yandex zone snippets are found with a quote-aware tag scanner, so a
raw `>` inside an attribute value no longer removes a snippet from the parse. The
routing-eval runner's verdict is machine-checkable and its exit code follows it: a
partial run is not `ok`.

**Privacy.** Fixtures no longer carry third-party contact phone numbers, a logged-in
account nick, per-request identifiers, reviewer display names, account identifiers or
order numbers; the operator's egress IP was replaced with documentation space. A CI
gate now verifies every fixture pin against its file.

## Verification status — read this before trusting the numbers

**No source was re-verified against live pages for this release.** The live checks
the checklist requires (`doctor`, an eyeball pass over each responding source, and
the Taobao yuan-versus-ruble ranking check) need a headed browser and the operator's
profile, and they were not run in this cycle. Under the checklist's *conditional go*
rule that makes the sources **unverified for 2.4.0**, not "working": treat the
verification of v2.3.0 as the last live evidence, and re-run the live pass before
relying on any source's current behaviour.

What **was** verified for this build:

- offline suite, coverage above the documented floor, ruff, formatting, mypy;
- `e2e_stdio_check.py`: 16/16 servers completed a real MCP session;
- version agreement across the release surface (84 places);
- the wire-cost gate against the stored baseline;
- every fixture pin against its file.
