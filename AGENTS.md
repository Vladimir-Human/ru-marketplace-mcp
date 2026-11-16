# Working on ru-marketplace-mcp

Read this before you change the repository. "The owner" is the repository owner, a person;
everything else here applies to whoever does the work, agent or human.

## Do not break

- Protect the public tool contract and data truth; [REVIEW.md](REVIEW.md) lists the invariants to check.
- The runtime stays read-only and keyless, with no model at runtime. Only the owner enters keys or passwords.
- No proxies, CAPTCHA solving, fingerprint spoofing or TLS impersonation; a human clears challenges.
- New sources are frozen. [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md) is reference, not permission to add one.
- Read [SECURITY.md](SECURITY.md) before changing input, output or browser trust boundaries.
- No daily or weekly live matrix, public status feed or scheduler: every tool call reports the truth at run time, and `marketplace-mcp doctor` checks each user's own setup.
- No outreach: no invitations, pinned discussions or new directory submissions. Replying to issues and reviewing PRs people open themselves is routine.
- No text names the owner's network address or whereabouts; write "the owner's machine".

These rules win over older guidance in other files; report a conflict instead of reconciling it quietly.

## Repository map

- `packages/<source>-connector/src/<source>_connector/`: one source's code; its `tests/` holds fixtures and tests.
- `packages/mcp-core/src/mcp_core/`: shared runtime; `sources.py` is the source registry.
- `packages/compare-connector/`: cross-source comparison.
- `packages/marketplace-connector/`: the unified server and the `marketplace-mcp` CLI.
- `packages/marketplace-connector/tests/conformance/`: registry-driven tests of rules every source must follow.
- `scripts/`: operational checks and generators; `.github/workflows/ci.yml`: the CI gates.
- `skills/`: usage guides for agents (`dsh/skills/` is a byte-identical copy); `docs/`: architecture, setup, tools.
- `.grace/`: requirements, module graph, verification index and evidence.
- Public history: `CHANGELOG.md`, `docs/releases/`, `docs/archive/` and git. Plans and raw live-run history stay in the owner's private notes.

## Structure

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains the layout; these rules govern new changes.

- A connector imports itself and `mcp_core`, never another connector; `mcp_core` imports no connector. Only compare and marketplace load connectors, and only through the registry.
- A rule that must hold for every source is written once (in `mcp_core` or as a registry field) and tested once, driven by the registry, in the conformance folder. No per-connector copies.
- Parsing and deciding are pure functions tested on fixtures; a tool function keeps only the I/O around them. A test that needs a fake transport tests that shell and nothing else.
- Refactor and behaviour never share a PR. A move-only PR changes no test assertion; a behaviour PR moves no code.
- No new file over 1,200 lines and no new function over 150. Today's larger files and functions sit under caps that only go down.
- A structure gate is coming; until it lands these limits hold by review.

## Work, review and merge

- Run the gates in [CONTRIBUTING.md](CONTRIBUTING.md#before-you-open-a-pr) and every `lint` and `test` step of `ci.yml` that runs locally.
- A new behaviour test must fail on the old code (copy the old module to a temp file and run the test against it). A change in live behaviour also needs one public-tool call per affected source through `scripts/live_check.py`.
- A move-only PR is checked differently: read every line `git diff --color-moved=zebra` does not mark as moved, confirm no assertion changed, run the gates. No new test, no old-code run.
- GRACE: done means requirement → code → evidence from that exact code version; green tests alone prove nothing. Requirements and graph change in the same PR as the behaviour ([CONTRIBUTING.md](CONTRIBUTING.md#grace-1)).
- Tests declare what they prove with `@pytest.mark.req("R-X")`; `python scripts/grace_index.py --write` regenerates `.grace/verification/index.xml` and `--check` fails on an untraced requirement or a stale index. Before a PR is done and before a release, `--evidence` runs the local gates and writes `.grace/evidence/latest.json`, bound to the working tree by one digest; `--check-evidence` checks it (not in CI).
- A PR that changes `packages/` adds `changes/<PR>.md` ([fragment rule](CONTRIBUTING.md#changelog-fragment-1)); `scripts/build_changelog.py` folds the fragments into `CHANGELOG.md` at release.
- Worker agents: a brief quotes the requirement verbatim, names the files the job may touch (all inside its worktree), says "change nothing else, reword no docs", asks for a red test first and for a report listing every fake and why. The reviewer reruns every gate outside the worker's sandbox and reads every fake in the diff.
- At most three workers at once, each in its own worktree, only when their file sets do not overlap. Merge one PR at a time; rebase the others and rerun their gates before review. Remove a worktree after its merge.
- Never auto-resolve a conflict: read the diff against `MERGE_HEAD`. No `git stash` (worktrees share it).
- `ci.yml` is the one shared file: a new check is a step at the end of `lint` or a new non-required job, and that rebase is done by hand. The branch ruleset does not change.
- A failed review gets one re-brief with the notes; a second failure splits the job in two.
- Reviewed PRs are squash-merged on green CI.

## Private material

- Captures, raw recordings, run logs, the budget ledgers and `doctor` output from the owner's machine are private: gitignored, never in a brief, issue or PR. Captures are read on the owner's machine only and go to no other model or service.
- What may leave a live run: whitelisted matrix rows, recordings after the scrub (regions replaced by `R1`, `R2` in order of appearance; no machine path, user name or profile directory) and an audit's summary numbers.

## Live runs

- Only on the owner's machine with the owner's Chrome: at most three runs a day, at least three hours apart.
- Always through `scripts/live_check.py`. Its preflight checks writable temp space, the Playwright driver and a bare CDP connect, and on Windows moves TEMP into the repo or switches to system Node by itself; no manual workarounds.
- `scripts/live_budget.py` counts outgoing marketplace requests, not tool calls: 40 per run, 8 per source, at least 3 s between requests to one source. Screenshot navigations are booked in the same ledger.
- Before every call read the ledger. A call that would take the run past 36 requests or a source past 7 is skipped, recorded as `skipped: budget` and carried to the next run. Count each call at its measured cost (1 until measured; an Ozon search costs 3).
- Set `CHROME_CHALLENGE_HANDOFF_S=0`. On a challenge record `needs_human` and move on: no retry of that source that day, no window brought on-screen.

## Releases

[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) has the commands; where it differs, these rules win.

1. `main` is green.
2. A matrix run on the commit to be tagged (a patch: only the sources it changes). `healthy` in the previous release's run and `drift` now blocks.
3. `refused`, `needs_human` or `inconclusive` now → one rerun at least 3 h later. Still not healthy and the release changes that source → the same call on the previous tag: old passes and new fails blocks; both fail → release and name the source under known limits.
4. `scripts/bump_version.py` and the version gate pass; the version is not on PyPI yet.
5. Every release-note claim is backed by a public-tool call on that source.
6. At least 24 h since the previous tag unless it fixes that tag's regression; at most one tag a day.

- The release commit is made when 1-5 pass; the tag goes on it when 1-6 pass. If a later release commit exists by then, only the latest is tagged and the earlier number is skipped.
- A release that removes or renames a public tool or changes a default waits for the owner's yes.
- Release notes list user-visible changes only, never checks, tests, CI or reviews.
