"""R-PYPI-PUBLISH: test the actual disabled workflow and its artifact/version guard."""
# START_MODULE_CONTRACT
# PURPOSE: Prove publishing stays owner-gated and rejects a bad release before the upload action.
# SCOPE: Local workflow text and execution of its Python guard on temporary wheel/sdist data.
# DEPENDS: pytest; stdlib; committed CI/publish workflows and maintainer docs.
# LINKS: M-PYPI-BUNDLE; R-PYPI-PUBLISH.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _job: Select one workflow job without reading another job's nested scalars.
# _tag_guard: Extract the actual Python guard that CI executes before publication.
# test_publish_is_owner_gated_and_uses_only_oidc: Gate, environment, permissions and pinned action.
# test_publish_guard_checks_real_wheel_metadata_against_tag: Run actual guard on valid/conflicting artifacts.
# test_bundle_ci_uses_plain_pip_and_installed_console_scripts / test_existing_ci_job_names_are_preserved: CI contract.
# END_MODULE_MAP

from __future__ import annotations

import re
import tarfile
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH = ROOT / ".github" / "workflows" / "pypi-publish.yml"

# START_BLOCK_WORKFLOW_INPUTS


def _job(workflow: Path, name: str) -> str:
    text = workflow.read_text(encoding="utf-8")
    match = re.search(rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S)
    assert match is not None, f"Missing {name} job"
    return match["body"]


def _tag_guard() -> str:
    text = _job(PUBLISH, "publish")
    match = re.search(r"uv run python - <<'PY'\n(?P<body>.*?)          PY", text, re.S)
    assert match is not None, "Publishing has lost its wheel/tag check"
    return textwrap.dedent(match["body"])


# END_BLOCK_WORKFLOW_INPUTS
# START_BLOCK_PUBLISHING


def test_publish_is_owner_gated_and_uses_only_oidc():
    text = PUBLISH.read_text(encoding="utf-8")
    job = _job(PUBLISH, "publish")
    assert re.search(r"^    if: vars\.PYPI_PUBLISH == 'true'$", job, re.M)
    assert re.search(r"^    environment: pypi$", job, re.M)
    permissions = re.search(r"^    permissions:\n(?P<body>(?:^      [^\n]+\n)+)", job, re.M)
    assert permissions is not None
    values = dict(re.findall(r"^      ([\w-]+): (\w+)$", permissions["body"], re.M))
    assert values == {"id-token": "write", "contents": "read"}
    assert 'tags:\n      - "v*"' in text
    assert "  workflow_dispatch:" in text
    assert "uses: pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086 # v1.14.3" in job
    assert "packages-dir: dist/pypi/" in job
    assert "secrets." not in text
    # id-token is a required permission, not an API-token input.
    assert not re.search(r"^\s+(?:password|token):", text, re.M)
    ci_actions = set(
        re.findall(r"uses: ((?:actions/checkout|astral-sh/setup-uv)@[^\n]+)", CI.read_text(encoding="utf-8"))
    )
    publish_actions = set(re.findall(r"uses: ((?:actions/checkout|astral-sh/setup-uv)@[^\n]+)", text))
    assert publish_actions == ci_actions


@pytest.mark.parametrize(
    ("tag", "ref_type", "wheel_version", "message"),
    [
        ("v2.6.0", "tag", "2.6.0", None),
        ("v2.6.0", "tag", "9.9.9", "Wheel version 9.9.9 does not match tag v2.6.0"),
        ("main", "branch", "2.6.0", "Select a v\\* tag"),
        ("release", "tag", "2.6.0", "Select a v\\* tag"),
    ],
)
def test_publish_guard_checks_real_wheel_metadata_against_tag(
    tag, ref_type, wheel_version, message, tmp_path, monkeypatch, capsys
):
    out = tmp_path / "dist" / "pypi"
    out.mkdir(parents=True)
    # The filename intentionally claims another version: only METADATA is authoritative.
    with zipfile.ZipFile(out / "ru_marketplace_mcp-0.0.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr(
            "ru_marketplace_mcp-0.0.0.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: ru-marketplace-mcp\nVersion: {wheel_version}\n",
        )
    with tarfile.open(out / "ru_marketplace_mcp-0.0.0.tar.gz", "w:gz"):
        pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_TAG", tag)
    monkeypatch.setenv("RELEASE_REF_TYPE", ref_type)
    guard = compile(_tag_guard(), str(PUBLISH), "exec")
    if message is not None:
        with pytest.raises(SystemExit, match=message):
            exec(guard, {})
    else:
        exec(guard, {})
        assert f"[R-PYPI-PUBLISH] ok: wheel version {wheel_version} matches {tag}" in capsys.readouterr().out


# END_BLOCK_PUBLISHING
# START_BLOCK_CI_AND_DOCS


def test_bundle_ci_uses_plain_pip_and_installed_console_scripts():
    job = _job(CI, "pypi-bundle")
    assert "name: PyPI bundle installs" in job
    assert "uv run python scripts/build_pypi_bundle.py" in job
    assert 'uv run python -m venv "$RUNNER_TEMP/pypi-venv"' in job
    assert '"$RUNNER_TEMP/pypi-venv/bin/python" -m pip install dist/pypi/*.whl' in job
    assert 'echo "$RUNNER_TEMP/pypi-venv/bin" >> "$GITHUB_PATH"' in job
    assert "marketplace-mcp --help" in job
    assert "ru-marketplace-mcp --help" in job
    assert '"$RUNNER_TEMP/pypi-venv/bin/python" scripts/e2e_stdio_check.py' in job
    assert "uv run python scripts/e2e_stdio_check.py" not in job
    assert "--system-site-packages" not in job
    assert 'PYTHONPATH: ""' in job


def test_existing_ci_job_names_are_preserved():
    expected = {
        "lint": "Lint and types",
        "test": "Tests (${{ matrix.os }}, py${{ matrix.python }})",
        "coverage": "Coverage (ubuntu, py3.12)",
        "smoke": "Server smoke test",
        "live": "Live endpoint canaries (nightly / manual)",
        "dsh-bundle": "dsh bundle installs and activates",
    }
    for name, title in expected.items():
        assert re.search(rf"^    name: {re.escape(title)}$", _job(CI, name), re.M)


def test_owner_registration_has_one_documented_source():
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "## PyPI (maintainer)" in deployment
    assert "Account → Publishing → Add a new pending publisher" in deployment
    for field, value in {
        "PyPI project name": "ru-marketplace-mcp",
        "Owner": "Vladimir-Human",
        "Repository": "ru-marketplace-mcp",
        "Workflow": "pypi-publish.yml",
        "Environment": "pypi",
    }.items():
        assert f"| {field} | `{value}` |" in deployment
    assert "repository variable `PYPI_PUBLISH` with value `true`" in deployment
    assert "Until step 3, the whole publish job is skipped" in deployment
    for name in ("README.md", "docs/RELEASE_CHECKLIST.md"):
        doc = (ROOT / name).read_text(encoding="utf-8")
        assert "DEPLOYMENT.md#pypi-maintainer" in doc
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pip install ru-marketplace-mcp" not in readme
    assert "uvx ru-marketplace-mcp" not in readme


# END_BLOCK_CI_AND_DOCS
