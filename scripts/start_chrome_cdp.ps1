# Start Chrome with CDP enabled, in a dedicated scraping profile by default.
# Required by the eight connectors that read through a browser: Ozon, Avito,
# Taobao, Megamarket, DNS, Citilink, AliExpress and Lamoda's search path.
#
# Run this BEFORE using those connectors' tools.
# Your everyday Chrome can stay open: this starts a separate Chrome with its
# own --user-data-dir, which Chrome allows next to another profile.
#
# === SECURITY THREAT MODEL ===
# CDP hands full control of the profile it attaches to - every session logged in
# there - to any process running as this user. Bound to 127.0.0.1 ONLY (--remote-debugging-address)
# but any process running as the operator can connect.
#
# Recommendations:
#   - DO NOT run on shared/coworking networks without netstat verification.
#   - DO NOT run while executing untrusted code on the same machine.
#   - A dedicated scraping profile is the DEFAULT here; the main profile needs an
#     explicit -AllowMainProfile. Log into marketplaces only in it, and banking or
#     email stay outside the blast radius. To place it yourself:
#       .\start_chrome_cdp.ps1 -Profile "$env:LOCALAPPDATA\Chrome-Scraping"
#
# The window starts on-screen so a human can log in. Pass -Hidden for the old
# off-screen launch (the connector's own autostart still uses that). -Visible is
# still accepted for older commands; it changes nothing, visible is the default.

param(
    [int]$Port = 9222,
    [Parameter(Mandatory=$false)][string]$Profile,
    [switch]$AllowMainProfile,
    [switch]$Visible,  # accepted for compatibility: the window is visible by default
    [switch]$Hidden
)

if (-not $Profile) {
    if ($AllowMainProfile) {
        $Profile = "$env:LOCALAPPDATA\Google\Chrome\User Data"
        Write-Warning "[!] Using MAIN Chrome profile - banking/email/GitHub sessions exposed!"
        Write-Warning "[!] CDP gives ANY local process full control of these sessions."
    } else {
        $Profile = "$env:LOCALAPPDATA\Chrome-Scraping"
        if (-not (Test-Path $Profile)) {
            New-Item -ItemType Directory -Force -Path $Profile | Out-Null
            Write-Host "[+] Created dedicated scraping profile: $Profile"
            Write-Host "[+] Chrome will start with an empty session - log into marketplaces only."
            Write-Host "[+] Banking/email session NOT present here = blast radius zero."
            Write-Host ""
        }
    }
}

function Get-CdpVersion {
    # A CDP endpoint answers /json/version with its browser version. Read it
    # through System.Net with a 3-second timeout, the same way in Windows
    # PowerShell 5.1 and PowerShell 7.
    try {
        $request = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/json/version")
        $request.Timeout = 3000
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $body = $reader.ReadToEnd()
        $reader.Close()
        $response.Close()
        return $body
    } catch {
        return $null
    }
}

# 1. A CDP endpoint that already answers means the browser is up. Starting a
#    second Chrome for the same profile would only fail; say so and point at
#    the command that brings the window back.
if (Get-CdpVersion) {
    Write-Host "Chrome with CDP is already running on port $Port - nothing to start."
    Write-Host "To bring its window on-screen: marketplace-mcp browser show"
    exit 0
}

$chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) {
    $chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
}
if (-not (Test-Path $chrome)) {
    Write-Error "Chrome not found at standard paths"
    exit 1
}

# 2. A Chrome that already holds THIS profile would refuse to start a second
#    one with the same --user-data-dir. Other Chrome windows are fine.
$profileInUse = $false
try {
    $running = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction Stop
    foreach ($proc in $running) {
        if ($proc.CommandLine -and $proc.CommandLine.IndexOf($Profile, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $profileInUse = $true
            break
        }
    }
} catch {
    Write-Warning "[!] Could not check whether the profile is in use; trying to start anyway."
}
# With -AllowMainProfile the profile is Chrome's own User Data: an everyday
# Chrome holds it without naming it on its command line, so any running
# Chrome counts as holding it.
if ($AllowMainProfile -and (Get-Process chrome -ErrorAction SilentlyContinue)) {
    $profileInUse = $true
}
if ($profileInUse) {
    Write-Error "Another Chrome is already using the profile $Profile. Close that window (or pass a different -Profile) and re-run."
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
if ($Hidden) {
    $chromeArgs += @(
        "--window-position=-32000,-32000",
        "--window-size=1280,720",
        "--start-minimized"
    )
}
# Anchor a tab so Chrome survives a connector closing its last page.
$chromeArgs += "about:blank"
$startParams = @{
    FilePath = $chrome
    ArgumentList = $chromeArgs
}
if ($Hidden) {
    $startParams.WindowStyle = "Hidden"
}
Start-Process @startParams
Start-Sleep -Seconds 2
if (Get-CdpVersion) {
    Write-Host "CDP ready on 127.0.0.1:$Port"
} else {
    Write-Warning "Chrome started but CDP endpoint not responding yet. Wait a few seconds."
}
if ($Hidden) {
    Write-Host "The window is off-screen. To log in or pass a site check: marketplace-mcp browser show"
}
