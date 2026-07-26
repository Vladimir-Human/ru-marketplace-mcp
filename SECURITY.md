# Security

## Reporting a vulnerability

Report privately via GitHub's [Security Advisories](https://github.com/Vladimir-Human/ru-marketplace-mcp/security/advisories/new)
rather than a public issue. A first response should come within a few days.

Useful in a report: what an attacker gains, how to reproduce, and which connector or
transport tier is involved.

## What this project does and does not touch

**There are no credentials anywhere in this project.** No API keys, no tokens, no
passwords, no credential store, no `.env` requirement. Every setting is an
operational knob (timeouts, rate gaps, region, proxy). Nothing to leak.

All access is read-only, against the public catalog endpoints the official web
clients use. No authenticated or administrative areas are touched.

## The one part that carries real risk: the CDP tier

Ozon rejects datacenter traffic, so its second transport tier runs fetches inside a
Chrome instance **you** started and logged into, over the DevTools Protocol.

**CDP grants any local process full control of the profile it is attached to**,
including every session logged into that profile. That is the threat to understand
before enabling it.

Mitigations, in order of importance:

| Mitigation | What it bounds |
|---|---|
| Dedicated scraping profile (default) | Blast radius — banking and email stay out |
| `--remote-debugging-address=127.0.0.1` | LAN access to the debugging port |
| Scheme guard in `open_page` | The browser being aimed at `file:///` |
| Per-connector host allowlists | A crafted input becoming a request for `/api/personal/orders` |

The dedicated profile is not a nicety, it is the primary control. Log into
marketplaces there and nothing else. Full detail: [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

If you do not need Ozon, do not enable this tier. The other three marketplaces work
over plain anonymous HTTP.

## Other hardening in place

**Bounded response bodies.** Responses stream against a hard byte cap, so a
compromised CDN or MITM cannot exhaust memory with an endless body.

**Allowlisted child environments.** The worker process Ozon spawns receives only the
variables it needs — a scraping worker has no business seeing tokens that happen to
sit in the parent environment.

**Un-hijackable `taskkill`.** On Windows the system directory is resolved via
`GetSystemDirectoryW`, not `SystemRoot`/`WINDIR`, because those are ordinary
environment variables that any process able to set the environment could redirect.

**Redirects not followed by default.** Several marketplaces answer datacenter IPs
with self-referential 307 loops; following them burns the request budget instead of
surfacing the block.

**Error redaction.** Bearer tokens, API keys and query-string secrets are stripped
from error text before it reaches a tool response, and absolute profile paths (which
contain the OS username) are kept out of user-visible errors.

**Input validation over escaping.** Values that reach URL paths or filter
expressions are validated against a strict shape rather than escaped.

## Prompt injection — the boundary users must respect

Tool output is **seller- and buyer-authored content**: product titles, seller names,
review text. It is untrusted data.

If a review or description appears to contain instructions ("ignore previous
instructions", "fetch this URL"), an agent must treat it as input, not policy. Every
skill document and tool docstring states this, but the ultimate control is the
consuming agent's own trust boundary.

This matters more than usual here: review text is free-form, high-volume, and
written by anyone.

## Legal note

Marketplace terms of service generally disallow unofficial parsing. This project
queries only public catalog endpoints, at a deliberately polite rate, for personal
research. You are responsible for your own use, including compliance with local law
and the relevant terms.

## Supported versions

The latest release receives security fixes. Report against `main` where possible.
