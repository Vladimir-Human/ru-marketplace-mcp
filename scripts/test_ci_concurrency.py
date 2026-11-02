"""A push to main must not cancel the nightly live canary, and the canary must not
cancel a merge verification.

`live` is the only job that produces automatic live evidence, and ci.yml runs it on
a schedule and on demand. It shares `refs/heads/main` with every push to main, so a
workflow-level `concurrency` group that ignores the event name puts both runs in one
slot: with `cancel-in-progress: true` whichever starts second silently kills the
first. A cancelled canary reports nothing at all — it does not go red, it just stops
existing — so the loss is invisible in the run history.

These tests read the workflow files as text rather than through a YAML parser: they are
checked into the repository, the facts under test are two scalars in a block we
control, and the project does not declare a YAML dependency for its own tests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
IMAGE_CHECK = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "image-check.yml"


def _section(header: str, workflow: Path = CI) -> str:
    """Return the indented body of the `header:` line in the workflow."""
    text = workflow.read_text(encoding="utf-8")
    indent = len(header) - len(header.lstrip())
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.rstrip() == header:
            body: list[str] = []
            for line in lines[index + 1 :]:
                stripped = line.strip()
                if stripped and (len(line) - len(line.lstrip())) <= indent:
                    break
                body.append(line)
            return "\n".join(body)
    raise AssertionError(f"{workflow.name} no longer contains a {header!r} line")


def _child_scalar(header: str, key: str, workflow: Path = CI) -> str:
    """Read one scalar that sits directly under the `header:` line.

    Only the immediate children count: a job body contains steps, and a step also
    has an `if:`, so matching at any depth would read the wrong line.
    """
    indent = len(header) - len(header.lstrip()) + 2
    matches = re.findall(rf"^ {{{indent}}}{re.escape(key)}:\s*(?P<value>\S.*?)\s*$", _section(header, workflow), re.M)
    assert len(matches) == 1, f"expected exactly one {key!r} directly under {header!r}, found {len(matches)}"
    return matches[0]


def _concurrency(key: str) -> str:
    return _child_scalar("concurrency:", key)


def _render_group(group: str, *, event: str, ref: str = "refs/heads/main") -> str:
    """Resolve the contexts the group is allowed to use."""
    values = {"github.workflow": "CI", "github.ref": ref, "github.event_name": event}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        assert key in values, (
            f"the concurrency group uses {key!r}, which this test cannot resolve; "
            "extend _render_group rather than dropping the assertion"
        )
        return values[key]

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", replace, group)


TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<op>!=|==|&&|\|\||!|\(|\))"
    r"|'(?P<text>[^']*)'"
    r"|(?P<literal>true|false)(?![A-Za-z0-9_])"
    r"|(?P<context>github\.[A-Za-z_][A-Za-z0-9_.]*)"
    r")"
)


def _tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(expression):
        match = TOKEN.match(expression, position)
        assert match, f"cannot tokenize {expression[position:]!r}"
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


class _Reader:
    """Just enough of the GitHub expression grammar to read cancel-in-progress."""

    def __init__(self, expression: str, event: str) -> None:
        self.tokens = _tokenize(expression)
        self.index = 0
        self.event = event

    def _peek(self) -> tuple[str | None, str | None]:
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return None, None

    def parse_or(self) -> bool:
        value = self.parse_and()
        while self._peek() == ("op", "||"):
            self.index += 1
            value = self.parse_and() or value
        return value

    def parse_and(self) -> bool:
        value = self.parse_unary()
        while self._peek() == ("op", "&&"):
            self.index += 1
            value = self.parse_unary() and value
        return value

    def parse_unary(self) -> bool:
        if self._peek() == ("op", "!"):
            self.index += 1
            return not self.parse_unary()
        if self._peek() == ("op", "("):
            self.index += 1
            value = self.parse_or()
            assert self._peek() == ("op", ")"), "unbalanced parentheses"
            self.index += 1
            return value
        return self.parse_comparison()

    def _operand(self) -> str:
        kind, text = self._peek()
        self.index += 1
        if kind in {"text", "literal"}:
            assert text is not None
            return text
        if kind == "context":
            assert text == "github.event_name", f"cannot evaluate {text!r}"
            return self.event
        raise AssertionError(f"unexpected operand {text!r}")

    def parse_comparison(self) -> bool:
        left = self._operand()
        operator = self._peek()
        if operator in {("op", "=="), ("op", "!=")}:
            self.index += 1
            right = self._operand()
            return left == right if operator[1] == "==" else left != right
        assert left in {"true", "false"}, f"expected a comparison or a boolean, got {left!r}"
        return left == "true"


def _cancels(expression: str, *, event: str) -> bool:
    if expression in {"true", "false"}:
        return expression == "true"
    match = re.fullmatch(r"\$\{\{\s*(?P<body>.+?)\s*\}\}", expression, re.S)
    assert match, f"cancel-in-progress must be a literal or an expression, got {expression!r}"
    reader = _Reader(match.group("body"), event)
    value = reader.parse_or()
    assert reader.index == len(reader.tokens), f"trailing tokens in {expression!r}"
    return value


def test_a_push_and_the_scheduled_canary_do_not_share_a_group() -> None:
    group = _concurrency("group")
    assert _render_group(group, event="push") != _render_group(group, event="schedule"), (
        "a push to main and the nightly canary would occupy one concurrency slot, "
        "so whichever starts second cancels the other"
    )


def test_separate_refs_still_do_not_cancel_each_other() -> None:
    group = _concurrency("group")
    assert _render_group(group, event="push", ref="refs/heads/main") != _render_group(
        group, event="push", ref="refs/pull/1/merge"
    )


def test_the_scheduled_canary_is_never_cancelled() -> None:
    assert _cancels(_concurrency("cancel-in-progress"), event="schedule") is False, (
        "a cancelled nightly run produces no evidence and no failure, so the scheduled canary must not be cancellable"
    )


def test_a_superseded_push_still_cancels() -> None:
    assert _cancels(_concurrency("cancel-in-progress"), event="push") is True


def test_the_live_job_stays_schedule_and_dispatch_only() -> None:
    condition = _child_scalar("  live:", "if")
    assert "schedule" in condition and "workflow_dispatch" in condition, (
        "the live job must stay off ordinary pushes; a per-push live run would "
        "hammer the marketplaces and is not what the canary is for"
    )


def test_the_live_job_runs_the_doctor_drift_alarm() -> None:
    """The nightly job must run the doctor status check and fail on its alarm.

    The canary's live tests counted drift_detected as a pass, so real parser
    drift never failed anything. Doctor separates the outcomes by exit code:
    1 means a parser drifted — the alarm this job exists to raise — while 2
    means sources could not be judged (blocked, no CDP, wrong country), the
    expected nightly result from GitHub's datacenter runners that must not
    fail the job. The doctor round runs through scripts/live_check.py so it
    stays inside the shared live request budget.
    """
    step = _section("      - name: Doctor drift alarm")
    assert "live_check.py doctor" in step, "the nightly job must run the doctor status check under the live budget"
    assert "--status-file" in step, "the doctor round must leave its machine-readable status snapshot"
    assert "0|2)" in step, "healthy and blocked/inconclusive outcomes must keep the job green"
    assert re.search(r"^\s*1\).*exit 1", step, re.M), "the doctor alarm (parser drift, exit 1) must fail the job"


def test_the_doctor_alarm_runs_before_the_canary_on_one_ledger() -> None:
    """The alarm must get its live requests before the canary spends them.

    Both steps book into one ledger: the repo-root conftest.py gives
    `pytest -m live` the default `.live-budget.json`, live_check.py defaults to
    the same file, and neither step resets it. The cap is 8 requests per source
    per run, and the canary's WB tests spend most of WB's 8 — so with the
    canary first, the doctor's wb_selfcheck is refused, reads inconclusive
    (exit 2) and the job stays green while WB actually drifted (2026-11-01
    audit). The alarm has priority, so its step must come first.
    """
    text = CI.read_text(encoding="utf-8")
    doctor = text.index("- name: Doctor drift alarm")
    canary = text.index("- name: Run live canaries")

    assert doctor < canary, "the doctor alarm must run before the canary, or the canary starves it of WB's budget"
    live_job = _section("  live:")
    assert "LIVE_BUDGET_LEDGER" not in live_job, "a per-step ledger would split the one budget both steps share"
    assert "live_check.py reset" not in live_job, "a reset between the steps would hand the alarm a fresh cap"


def test_a_broken_live_check_fails_the_alarm() -> None:
    """R-DOCTOR-EXIT-CODES: exit 4 alarms on checker/doctor/setup/usage failure.

    The step keeps 2 non-alarm because a blocked marketplace must not page
    anyone. A failed checker or doctor invocation is a broken alarm instead.
    This inspects workflow text only; it does not execute the nightly job.
    """
    step = _section("      - name: Doctor drift alarm")

    assert re.search(r"^\s*4\).*exit 1", step, re.M), "exit 4 (checker/doctor/setup/usage failure) must fail the job"
    assert "exit 4" in step, "the step summary must name what exit 4 means"
    assert "checker/doctor/setup/usage failure (alarm)" in step
    assert "doctor never ran" not in step, "exit 4 also covers a checker that ran and failed"


def test_the_image_check_workflow_uses_an_event_aware_concurrency_group() -> None:
    """A manual image build must not cancel a pull-request build (audit S4-F16).

    The image check runs on a pull request and on a manual dispatch. Without
    the event name in the group both share one slot, and with
    `cancel-in-progress: true` whichever starts second kills the first - the
    failure `test_ci_concurrency.py` exists to prevent for ci.yml.
    """
    group = _child_scalar("concurrency:", "group", workflow=IMAGE_CHECK)

    assert "github.event_name" in group
    assert _render_group(group, event="push") != _render_group(group, event="workflow_dispatch")


@pytest.mark.parametrize(
    ("expression", "event", "expected"),
    [
        ("true", "schedule", True),
        ("false", "push", False),
        ("${{ github.event_name != 'schedule' }}", "push", True),
        ("${{ github.event_name != 'schedule' }}", "schedule", False),
        ("${{ github.event_name == 'push' }}", "schedule", False),
        ("${{ github.event_name == 'push' || github.event_name == 'pull_request' }}", "push", True),
        ("${{ github.event_name == 'push' || github.event_name == 'pull_request' }}", "schedule", False),
        (
            "${{ github.event_name != 'schedule' && github.event_name != 'workflow_dispatch' }}",
            "workflow_dispatch",
            False,
        ),
        ("${{ !(github.event_name == 'schedule') }}", "schedule", False),
    ],
)
def test_the_expression_reader_understands_the_shapes_it_claims_to(expression: str, event: str, expected: bool) -> None:
    """The reader above is only trustworthy if it is exercised on known answers."""
    assert _cancels(expression, event=event) is expected
