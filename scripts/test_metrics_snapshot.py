"""R-METRICS: one reach line a day, external authors only, a dead source never loses the others."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import metrics_snapshot as ms


def _issue(login: str, pr: bool = False) -> dict:
    item: dict = {"user": {"login": login}}
    if pr:
        item["pull_request"] = {"url": "x"}
    return item


def test_external_authors_skip_the_owner_and_bots_and_split_issues_from_prs():
    issues = [
        _issue("alice"),
        _issue("alice"),
        _issue("bob"),
        _issue("bob", pr=True),
        _issue(ms.OWNER),
        _issue(ms.OWNER, pr=True),
        _issue("dependabot[bot]", pr=True),
        _issue("carol", pr=True),
    ]

    assert ms.external_authors(issues) == {"issues": 2, "prs": 2, "any": 3}


def test_a_line_already_written_today_is_not_repeated(tmp_path, monkeypatch):
    out = tmp_path / "metrics.jsonl"
    calls = []
    monkeypatch.setattr(ms, "collect", lambda: calls.append(1) or {"date": dt.date.today().isoformat(), "errors": {}})

    assert ms.main([], out=out) == 0
    assert ms.main([], out=out) == 0
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1
    assert ms.main(["--force"], out=out) == 0
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2
    assert len(calls) == 2
    assert b"\r\n" not in out.read_bytes()


def test_a_failing_source_is_recorded_and_the_rest_still_count(monkeypatch):
    def fake_gh(path: str):
        if "/traffic/" in path:
            raise subprocess.CalledProcessError(1, ["gh", "api", path])
        if "/issues?" in path:
            return [_issue("alice"), _issue("bob", pr=True)]
        return {"stargazers_count": 189, "forks_count": 32}

    monkeypatch.setattr(ms, "gh", fake_gh)
    monkeypatch.setattr(ms, "pypi_last_month", lambda: 1087)

    row = ms.collect()

    assert row["repo"] == {"stargazers_count": 189, "forks_count": 32}
    assert row["views"] is None and row["clones"] is None
    assert set(row["errors"]) == {"views", "clones"}
    assert row["authors"] == {"issues": 1, "prs": 1, "any": 2}
    assert row["pypi_last_month"] == 1087
    json.dumps(row)


def test_every_source_failing_exits_non_zero(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(ms, "gh", boom)
    monkeypatch.setattr(ms, "pypi_last_month", boom)

    assert ms.main(["--force"], out=tmp_path / "m.jsonl") == 1
