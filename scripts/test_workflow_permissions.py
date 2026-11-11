"""Every workflow limits its GITHUB_TOKEN explicitly.

R-CI-TOKEN-SCOPE: each workflow under .github/workflows declares token
permissions itself, at the top level or on every job, so the repository's
default (read) is not the only guard; ci.yml, which runs on pull requests,
is read-only at the top level and no job widens it. Found by CodeQL
(actions/missing-workflow-permissions) on 2026-11-11.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"


def _jobs(text: str) -> dict[str, str]:
    """Job name -> job body, read from the top-level jobs: mapping."""
    start = re.search(r"^jobs:\n", text, re.M)
    assert start is not None, "workflow has no top-level jobs:"
    body = text[start.end() :]
    end = re.search(r"^\S", body, re.M)
    body = body[: end.start()] if end else body
    heads = list(re.finditer(r"^  ([A-Za-z0-9_-]+):[ \t]*$", body, re.M))
    return {
        head.group(1): body[head.end() : heads[i + 1].start() if i + 1 < len(heads) else len(body)]
        for i, head in enumerate(heads)
    }


def _top_permissions(text: str) -> str | None:
    match = re.search(r"^permissions:[ \t]*\n((?:  [^\n]*\n)+)", text, re.M)
    return match.group(1) if match else None


def test_every_workflow_limits_the_token() -> None:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, "no workflows found"
    unlimited = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if _top_permissions(text) is not None:
            continue
        jobs = _jobs(text)
        assert jobs, f"{path.name} has no jobs"
        unlimited += [
            f"{path.name}:{name}" for name, body in jobs.items() if not re.search(r"^    permissions:", body, re.M)
        ]
    assert unlimited == [], f"jobs that inherit the repository token default: {unlimited}"


def test_ci_is_read_only_and_no_job_widens_it() -> None:
    text = CI.read_text(encoding="utf-8")
    assert _top_permissions(text) == "  contents: read\n"
    widened = [name for name, body in _jobs(text).items() if re.search(r"^    permissions:", body, re.M)]
    assert widened == [], f"ci.yml jobs that set their own permissions: {widened}"
