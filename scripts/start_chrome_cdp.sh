#!/usr/bin/env bash
# Start Chrome with remote debugging for the authenticated (tier-2) transport.
#
# Some Russian marketplaces refuse datacenter TLS fingerprints outright, so the
# reliable way to read their catalogs is from inside a real browser session. This
# script launches a Chrome dedicated to that job.
#
# SECURITY — the profile this creates is a scraping profile, not your browser:
#   * CDP grants any local process full control of every session in the profile
#     it is attached to. That is why this uses a SEPARATE user-data-dir.
#   * Log into marketplaces here and nothing else. No banking, no email, no work
#     accounts — that is the entire point of the separation.
#   * The debugging port binds to 127.0.0.1 only and is never exposed to a LAN.
#
# Usage:
#   ./scripts/start_chrome_cdp.sh                 # default profile + port 9222
#   ./scripts/start_chrome_cdp.sh --port 9333
#   ./scripts/start_chrome_cdp.sh --profile ~/my-scraping-profile
#   ./scripts/start_chrome_cdp.sh --headless      # no display (detectable!)
#
# Windows users: run scripts/start_chrome_cdp.ps1 instead.

set -euo pipefail

PORT="${CHROME_CDP_PORT:-9222}"
HEADLESS=0

case "$(uname -s)" in
    Darwin) DEFAULT_PROFILE="$HOME/Library/Application Support/Chrome-Scraping" ;;
    *)      DEFAULT_PROFILE="${XDG_DATA_HOME:-$HOME/.local/share}/chrome-scraping" ;;
esac
PROFILE="${CHROME_SCRAPING_PROFILE:-$DEFAULT_PROFILE}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)     PORT="$2"; shift 2 ;;
        --profile)  PROFILE="$2"; shift 2 ;;
        --headless) HEADLESS=1; shift ;;
        -h|--help)  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

find_chrome() {
    if [[ -n "${CHROME_BINARY:-}" && -x "${CHROME_BINARY}" ]]; then
        echo "$CHROME_BINARY"; return 0
    fi
    local candidates=()
    if [[ "$(uname -s)" == "Darwin" ]]; then
        candidates=(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            "/Applications/Chromium.app/Contents/MacOS/Chromium"
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
            "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    else
        for name in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do
            local resolved
            resolved="$(command -v "$name" 2>/dev/null || true)"
            [[ -n "$resolved" ]] && candidates+=("$resolved")
        done
        candidates+=("/usr/bin/google-chrome" "/usr/bin/chromium" "/snap/bin/chromium")
    fi
    for candidate in "${candidates[@]}"; do
        [[ -x "$candidate" ]] && { echo "$candidate"; return 0; }
    done
    return 1
}

CHROME="$(find_chrome)" || {
    echo "ERROR: Chrome/Chromium not found." >&2
    echo "Install Chrome, or point CHROME_BINARY at the executable." >&2
    exit 1
}

# Refuse to double-start: Chrome enforces user-data-dir exclusivity anyway, but a
# clear message beats a silently exiting second process.
if command -v ss >/dev/null 2>&1; then
    LISTENING="$(ss -ltn "sport = :$PORT" 2>/dev/null | grep -c LISTEN || true)"
elif command -v lsof >/dev/null 2>&1; then
    LISTENING="$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | wc -l || true)"
else
    LISTENING=0
fi
if [[ "${LISTENING:-0}" -gt 0 ]]; then
    echo "CDP is already listening on 127.0.0.1:$PORT — nothing to do."
    echo "Verify with: curl -s http://127.0.0.1:$PORT/json/version"
    exit 0
fi

mkdir -p "$PROFILE"

ARGS=(
    "--remote-debugging-port=$PORT"
    "--remote-debugging-address=127.0.0.1"
    "--user-data-dir=$PROFILE"
    "--no-first-run"
    "--no-default-browser-check"
    "--disable-features=Translate"
)
[[ "$HEADLESS" == "1" ]] && ARGS+=("--headless=new" "--disable-gpu")
# Chrome's sandbox cannot run as root, and containers routinely do.
[[ "$(id -u)" == "0" ]] && ARGS+=("--no-sandbox")
# Anchor a tab so Chrome survives a connector closing its last page.
ARGS+=("about:blank")

echo "Chrome     : $CHROME"
echo "Profile    : $PROFILE"
echo "CDP        : http://127.0.0.1:$PORT"
echo
echo "Launching… log into the marketplaces you need IN THIS WINDOW ONLY."
echo "Keep banking and email out of this profile."

nohup "$CHROME" "${ARGS[@]}" >/dev/null 2>&1 &
disown || true

for _ in $(seq 1 30); do
    sleep 0.4
    if curl -sf -m 1 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
        echo
        echo "CDP is up. Verify with: curl -s http://127.0.0.1:$PORT/json/version"
        exit 0
    fi
done

echo
echo "WARNING: Chrome was launched but CDP did not bind within ~12s." >&2
echo "Check for an existing Chrome using this profile, or try another --port." >&2
exit 1
