# v2.4.1 — 2026-09-18

**This release replaces v2.4.0, which has been withdrawn.** The source distributions of
v2.4.0 shipped test fixtures that contained third-party personal data: two Yandex
reviewer accounts (one of them recoverable by base64-decoding a protobuf field, so a
text search did not find it) and three named private individuals from Cian listings,
with account ids and a resolvable agent profile URL. Anyone who downloaded
`cian_connector-2.4.0.tar.gz` or `yandex_connector-2.4.0.tar.gz` has that data locally;
the release and its tag were removed rather than left downloadable.

## What changed since 2.4.0

Every user-visible change is a privacy or accuracy fix found by a four-way audit of the
public surface (root documents, all fixtures, the working documentation, and the
repository's GitHub side):

- **Fixtures.** The reviewer identity in `card_washer.html` is masked in all four
  carriers it appeared in; the Cian fixtures no longer name private realtors. Agency
  and business records are unchanged - a company name is not personal data.
- **Provenance.** Notes no longer reference the operator's private capture workspace,
  and every fixture pin is recomputed and verified by the CI gate.
- **Documentation.** `ARCHITECTURE.md` describes nine error codes (it had said eight
  and omitted `challenge_required`, which callers branch on); `CDP_SETUP.md` lists Cian
  among the sources needing a browser and counts the CDP-only set as six, not five.
- **A root document.** `DEEP_RESEARCH_V2.0.0.md` carried a dated superseded notice: the
  four security defects it listed as open work had all shipped after it was written.
- **Keep-out rules.** The `.gitignore` entries protecting local run tooling are back in
  the tracked file with neutral wording, so a fresh clone protects them too.

## Verification status — read this before trusting the numbers

**No source was re-verified against live pages for this release.** The live checks the
checklist requires (`doctor`, an eyeball pass over each responding source, and the
Taobao yuan-versus-ruble ranking check) need a headed browser and the operator's
profile, and they were not run in this cycle. Under the checklist's *conditional go*
rule that makes the sources **unverified for 2.4.1**, not "working": the last live
evidence remains the v2.3.0 pass.

What **was** verified for this build:

- the offline suite, coverage above the documented floor, ruff, formatting, mypy;
- `e2e_stdio_check.py`: 16/16 servers completed a real MCP session;
- version agreement across the release surface (84 places);
- the wire-cost gate against the stored baseline;
- every fixture pin against its file;
- and, specifically for this release: the published archives were downloaded and
  searched for the personal data that triggered the withdrawal.
