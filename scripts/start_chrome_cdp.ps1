# Start Chrome with CDP enabled, in a dedicated scraping profile by default.
# Required by the seven connectors that read through a browser: Ozon, Avito,
# Taobao, Megamarket, DNS, Citilink and Lamoda's search path.
#
# Run this BEFORE using those connectors' tools.
# All Chrome windows must be closed first (Chrome refuses CDP if already running).
#
# === SECURITY THREAT MODEL ===
# CDP hands full control of the profile it attaches to — every session logged in
# there — to any process running as this user. Bound to 127.0.0.1 ONLY (--remote-debugging-address)
# but any process running as the operator can connect.
#
# Recommendations:
#   - DO NOT run on shared/coworking networks without netstat verification.
#   - DO NOT run while executing untrusted code on the same machine.
#   - A dedicated scraping profile is the DEFAULT here; the main profile needs an
#     explicit -AllowMainProfile. Log into marketplaces only in it, and banking or
#     email stay outside the blast radius. To place it yourself:
#       .\start_chrome_cdp.ps1 -Profile "$env:LOCALAPPDATA\Chrome-Scraping"

param(
    [int]$Port = 9222,
    [Parameter(Mandatory=$false)][string]$Profile,
    [switch]$AllowMainProfile,
    [switch]$Visible
)

if (-not $Profile) {
    if ($AllowMainProfile) {
        $Profile = "$env:LOCALAPPDATA\Google\Chrome\User Data"
        Write-Warning "[!] Using MAIN Chrome profile — banking/email/GitHub sessions exposed!"
        Write-Warning "[!] CDP gives ANY local process full control of these sessions."
    } else {
        $Profile = "$env:LOCALAPPDATA\Chrome-Scraping"
        if (-not (Test-Path $Profile)) {
            New-Item -ItemType Directory -Force -Path $Profile | Out-Null
            Write-Host "[+] Created dedicated scraping profile: $Profile"
            Write-Host "[+] Chrome will start with an empty session — log into marketplaces only."
            Write-Host "[+] Banking/email session NOT present here = blast radius zero."
            Write-Host ""
        }
    }
}

$chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) {
    $chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
}
if (-not (Test-Path $chrome)) {
    Write-Error "Chrome not found at standard paths"
    exit 1
}

# Check if Chrome is already running
$existing = Get-Process chrome -ErrorAction SilentlyContinue
if ($existing) {
    Write-Warning "Chrome is already running. CDP requires Chrome to start fresh."
    Write-Warning "Close all Chrome windows, then re-run this script."
    exit 2
}

Write-Host "Starting Chrome with CDP on port $Port (bound to 127.0.0.1)"
Write-Host "Profile: $Profile"
Write-Host ""
Write-Host "[!] CDP gives full Chrome session control to any local process."
Write-Host "    For dedicated scraping profile: pass -Profile <path>"
Write-Host ""
$chromeArgs = @(
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=$Profile",
    "--no-first-run",
    "--no-default-browser-check"
)
if (-not $Visible) {
    $chromeArgs += @(
        "--window-position=-32000,-32000",
        "--window-size=1280,720",
        "--start-minimized",
        "about:blank"
    )
}
$startParams = @{
    FilePath = $chrome
    ArgumentList = $chromeArgs
}
if (-not $Visible) {
    $startParams.WindowStyle = "Hidden"
}
Start-Process @startParams
Start-Sleep -Seconds 2
$test = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing -ErrorAction SilentlyContinue
if ($test.StatusCode -eq 200) {
    Write-Host "CDP ready on 127.0.0.1:$Port"
} else {
    Write-Warning "Chrome started but CDP endpoint not responding yet. Wait a few seconds."
}
