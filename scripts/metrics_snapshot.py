#!/usr/bin/env python3
"""Append one line of the project's public reach to the gitignored .live-metrics.jsonl.

GitHub keeps repository traffic for 14 days only, so a daily line is the only
history there will be. Each line holds stars and forks, 14-day views and clones
with their daily rows, distinct external authors of issues and PRs, and PyPI
downloads of the bundle over the last month (pypistats "recent"). At most one
line a day unless --force. A source that fails is written as null with its error;
the other sources still count.

START_MODULE_CONTRACT
PURPOSE: Keep a local daily record of reach numbers (plan baseline T5).
SCOPE: gh api reads (repo, traffic, issues), one pypistats read, one JSONL line per day.
DEPENDS: gh on PATH with the maintainer's token; stdlib argparse,datetime,json,subprocess,urllib.request,pathlib.
LINKS: M-METRICS, R-METRICS.
START_MODULE_MAP
  external_authors   distinct non-owner, non-bot authors of issues, of PRs, and of either
  collect            one row; a failing source becomes null plus an errors entry
  main               once-a-day append to .live-metrics.jsonl
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

REPO = "Vladimir-Human/ru-marketplace-mcp"
OWNER = "Vladimir-Human"
PYPI_RECENT = "https://pypistats.org/api/packages/ru-marketplace-mcp/recent"
OUT = Path(__file__).resolve().parent.parent / ".live-metrics.jsonl"


def gh(path: str) -> Any:
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


def repo_counts() -> dict[str, int]:
    meta = gh(f"repos/{REPO}")
    return {k: meta[k] for k in ("stargazers_count", "forks_count")}


def traffic(kind: str) -> dict[str, Any]:
    data = gh(f"repos/{REPO}/traffic/{kind}")
    daily = {row["timestamp"][:10]: [row["count"], row["uniques"]] for row in data[kind]}
    return {"count": data["count"], "uniques": data["uniques"], "daily": daily}


def all_issues() -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for page in range(1, 51):
        batch = gh(f"repos/{REPO}/issues?state=all&per_page=100&page={page}")
        issues += batch
        if len(batch) < 100:
            return issues
    return issues


def external_authors(issues: list[dict[str, Any]]) -> dict[str, int]:
    """Distinct people other than the owner and bots who opened an issue, a PR, or either."""
    ext = [i for i in issues if i["user"]["login"] != OWNER and not i["user"]["login"].endswith("[bot]")]
    issue_by = {i["user"]["login"] for i in ext if "pull_request" not in i}
    pr_by = {i["user"]["login"] for i in ext if "pull_request" in i}
    return {"issues": len(issue_by), "prs": len(pr_by), "any": len(issue_by | pr_by)}


def pypi_last_month() -> int:
    req = urllib.request.Request(PYPI_RECENT, headers={"User-Agent": "ru-marketplace-mcp-metrics"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return int(json.load(resp)["data"]["last_month"])


def collect() -> dict[str, Any]:
    row: dict[str, Any] = {"date": dt.date.today().isoformat(), "errors": {}}

    def take(key: str, fn: Any) -> None:
        try:
            row[key] = fn()
        except Exception as exc:  # one dead source must not lose the others
            row[key] = None
            row["errors"][key] = f"{type(exc).__name__}: {exc}"[:200]

    take("repo", repo_counts)
    take("views", lambda: traffic("views"))
    take("clones", lambda: traffic("clones"))
    take("authors", lambda: external_authors(all_issues()))
    take("pypi_last_month", pypi_last_month)
    return row


def last_date(path: Path) -> str | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])["date"] if lines else None


def main(argv: list[str] | None = None, out: Path = OUT) -> int:
    parser = argparse.ArgumentParser(description="Append one daily line of reach numbers to .live-metrics.jsonl.")
    parser.add_argument("--force", action="store_true", help="write even if today's line exists")
    args = parser.parse_args(argv)
    if not args.force and last_date(out) == dt.date.today().isoformat():
        print(f"already recorded today in {out.name}")
        return 0
    row = collect()
    with out.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in row.items() if k not in ("views", "clones")}, ensure_ascii=False))
    return 1 if len(row["errors"]) == 5 else 0


if __name__ == "__main__":
    raise SystemExit(main())
