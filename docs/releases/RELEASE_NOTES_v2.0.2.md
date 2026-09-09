# v2.0.2

Patch release for macOS CDP usability.

## Fixed

- CDP-backed marketplace calls no longer bring the dedicated scraping Chrome to
  the foreground or switch the operator to its macOS Space.
- With `CHROME_STEALTH=1` (the default), both Playwright and raw-CDP paths open
  temporary tabs in the background and re-hide only the configured scraping
  profile after navigation and tab close.
- `scripts/start_chrome_cdp.sh` hides the dedicated macOS browser after CDP is
  ready. `CHROME_STEALTH=0` retains the previous visible behavior.
- The macOS profile-PID probe now preserves POSIX profile paths when tested from
  a non-macOS host.

Marketplace extraction, price ranking, and source coverage are unchanged in
this patch. The release is therefore scoped to CDP browser behavior rather than
a claim of refreshed live-marketplace verification.
