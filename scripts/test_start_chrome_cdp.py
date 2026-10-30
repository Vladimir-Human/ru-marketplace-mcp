"""The Chrome launchers must not demand closing the everyday Chrome.

Chrome allows a second instance with its own ``--user-data-dir``, so the
launchers refuse only the case that actually breaks: another Chrome holding
*this* profile. The checks are static — the scripts are read, never run,
because running them starts a real browser.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PS1 = REPO_ROOT / "scripts" / "start_chrome_cdp.ps1"
SH = REPO_ROOT / "scripts" / "start_chrome_cdp.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_powershell_launcher_no_longer_refuses_any_running_chrome():
    text = _read(PS1)

    # Any running Chrome counts only for -AllowMainProfile: Chrome's own User
    # Data is held by an everyday Chrome that does not name it on its command line.
    any_chrome = [line for line in text.splitlines() if "Get-Process chrome" in line]
    assert any_chrome
    assert all("$AllowMainProfile -and" in line for line in any_chrome)
    assert "Close all Chrome windows" not in text
    assert "CDP requires Chrome to start fresh" not in text


def test_the_powershell_launcher_checks_the_cdp_endpoint_first():
    text = _read(PS1)

    assert "/json/version" in text
    assert "already running on port" in text
    # The operator is told how to see the window that is already up.
    assert "marketplace-mcp browser show" in text


def test_the_powershell_launcher_refuses_only_the_same_profile():
    text = _read(PS1)

    assert "Get-CimInstance Win32_Process" in text
    # Anchored, optionally quoted (audit S2-F6): a sibling profile is not this one.
    assert "$profilePattern = '--user-data-dir=\"?' + [regex]::Escape($Profile) + '(\"|\\s|$)'" in text
    assert "-match $profilePattern" in text
    assert "IndexOf($Profile" not in text
    assert "Another Chrome is already using the profile" in text
    assert "exit 2" in text


def test_the_powershell_launcher_points_at_the_browser_show_verb_when_hidden():
    text = _read(PS1)

    assert "The window is off-screen" in text
    assert "marketplace-mcp browser show" in text


def test_the_powershell_launcher_stays_readable_by_windows_powershell_5_1():
    """v2.5.2: em-dashes in the BOM-less script broke the 5.1 parser.

    The file must stay ASCII without a BOM, or Windows PowerShell 5.1 reads a
    long dash as a quote and the script stops parsing. Line endings are not
    checked: 5.1 reads LF and CRLF alike, and a Windows checkout with
    core.autocrlf turns LF into CRLF on disk.
    """
    raw = PS1.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf"), "a BOM would change how 5.1 reads the first line"
    assert all(byte < 128 for byte in raw), "the launcher must stay ASCII for Windows PowerShell 5.1"


def test_the_shell_launcher_applies_the_same_three_steps():
    text = _read(SH)
    # 1. The CDP endpoint already answers.
    assert "/json/version" in text
    assert "already running on port" in text
    # 2. The same profile is locked by another Chrome.
    assert 'pgrep -f -- "--user-data-dir=${PROFILE}( |\\$)"' in text
    assert "another Chrome is already using the profile" in text
    assert "exit 2" in text
    # 3. Chrome starts even when other Chrome windows are open.
    assert "Close all Chrome windows" not in text


def test_the_manual_launchers_are_visible_by_default():
    """The owner decision: a human launch is on-screen; hidden is opt-in."""
    ps1 = _read(PS1)
    assert "[switch]$Hidden" in ps1
    # -Visible is still accepted, but it no longer drives the off-screen branch.
    assert "[switch]$Visible" in ps1
    assert "if (-not $Visible)" not in ps1
    assert ps1.count("if ($Hidden)") >= 2
    assert "--window-position=-32000,-32000" in ps1

    sh = _read(SH)
    assert "--hidden)" in sh
    assert "HIDDEN=1" in sh
    # macOS hides the app only when --hidden was asked for.
    assert '"$HIDDEN" == "1"' in sh
    assert "CHROME_STEALTH" not in sh.split("nohup")[-1]


def test_the_powershell_launcher_quotes_the_profile_path():
    """(audit S2-F1) Start-Process joins arguments with bare spaces: "User Data" split in two."""
    text = _read(PS1)

    assert '"--user-data-dir=`"$Profile`""' in text
    assert "$Profile.TrimEnd('\\')" in text


def test_the_powershell_launcher_fails_when_cdp_never_binds():
    """(audit S2-F5) Like the shell launcher: wait about 12 s, then exit 1."""
    text = _read(PS1)
    tail = text.split("Start-Process @startParams", 1)[1]

    assert "for ($i = 0; $i -lt 30; $i++)" in tail
    assert "exit 1" in tail.split('Write-Host "CDP ready')[0]
    assert "Wait a few seconds" not in text


def test_the_shell_launcher_asks_the_endpoint_before_saying_already_running():
    """(audit S2-F2) Any listener on the port is not Chrome: probe /json/version first."""
    text = _read(SH)
    probe = text.index('curl -sf -m 1 "http://127.0.0.1:$PORT/json/version"')
    already = text.index("already running on port")
    listener = text.index('ss -ltn "sport = :$PORT"')

    assert probe < already < listener
    assert "does not answer as a CDP endpoint" in text
