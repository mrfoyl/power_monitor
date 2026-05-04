<#
.SYNOPSIS
    PRTG EXE/Script Notification — Power Outage Correlation

.DESCRIPTION
    When a PRTG sensor goes Down this script:
      1. Parses GPS coordinates from the PRTG group's Location field
      2. Calls the Power Monitor API server (server.py) via HTTP
      3. Prints a plain-text result — PRTG exposes this as %scriptresult
         in notification templates (email, Teams, etc.)
      4. Writes an entry to a rolling log file for audit purposes

    No Python or third-party packages required.
    Uses only built-in Windows PowerShell 5.1 (pre-installed on all
    Windows Server / Windows 10+ machines).

    Exit codes:
      0  — completed (outage found OR not found — both are OK)
      1  — fatal error (missing URL, bad location, server unreachable)

HOW TO INSTALL
--------------
1. Copy this file to PRTG's EXE notification directory:
     C:\Program Files (x86)\PRTG Network Monitor\Notifications\EXE\

2. Set $ApiUrl below.

3. Create a notification in PRTG:
     Setup -> Account Settings -> Notifications -> Add Notification
     Type: Execute Program
     Program File: prtg_outage_check.ps1
     Parameters (copy exactly):
       -Device "%device" -Group "%group" -Sensor "%name" `
       -Status "%status" -Location "%location" -Down "%down"

4. Add the notification to a trigger on your site groups:
     Group -> Notifications -> Add State Trigger
     When: Down  ->  Execute: Power Outage Check

5. Add %scriptresult to your alert notification message template.

PRTG EXECUTION POLICY
---------------------
PRTG runs PowerShell scripts via:
  powershell.exe -NonInteractive -NoLogo -File "script.ps1" [params]

If the PRTG server's execution policy blocks unsigned scripts, set it once:
  Set-ExecutionPolicy RemoteSigned -Scope LocalMachine

Or unblock just this file after copying:
  Unblock-File "C:\...\Notifications\EXE\prtg_outage_check.ps1"

PRTG LOCATION FIELD FORMAT
---------------------------
Set the Location field on each PRTG group to GPS coordinates:
  "61.5120, 9.1234"        decimal degrees (preferred)
  "61.5120,9.1234"
  "61.5120° N, 9.1234° E"  copy-paste from Google Maps is fine

Group -> Edit -> Settings -> Location field.
#>

param(
    [string]$Device   = "(unknown)",
    [string]$Group    = "(unknown)",
    [string]$Sensor   = "(unknown)",
    [string]$Status   = "Down",
    [string]$Location = "",
    [string]$Down     = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Configuration — edit these values
# ---------------------------------------------------------------------------

# URL of the Power Monitor API server (server.py). Required.
$ApiUrl = ""   # e.g. "http://192.168.1.50:5000"

# API key — must match POWER_MONITOR_API_KEY on the server.
# Leave empty if the server has no API key configured.
$ApiKey = ""

# Log file next to this script. Set to $null to disable.
$LogFile     = Join-Path $PSScriptRoot "prtg_outage_check.log"
$LogMaxBytes = 5MB

# Timeout for API calls (seconds)
$TimeoutSec = 20

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = "{0}  {1,-7}  {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    if ($LogFile) {
        try {
            if (Test-Path $LogFile) {
                if ((Get-Item $LogFile).Length -gt $LogMaxBytes) {
                    Move-Item $LogFile "$LogFile.1" -Force
                }
            }
            Add-Content -Path $LogFile -Value $line -Encoding UTF8
        } catch { }
    }
}

# ---------------------------------------------------------------------------
# Input clamping
# ---------------------------------------------------------------------------

function Limit-String {
    param([string]$Value, [int]$MaxLen = 200)
    if ($Value.Length -gt $MaxLen) { return $Value.Substring(0, $MaxLen) }
    return $Value
}

$Device = Limit-String $Device
$Group  = Limit-String $Group
$Sensor = Limit-String $Sensor
$Status = Limit-String $Status 50
$Down   = Limit-String $Down   50

# ---------------------------------------------------------------------------
# GPS parsing
# ---------------------------------------------------------------------------

function Get-Coordinates {
    param([string]$LocationStr)
    $nums = [regex]::Matches($LocationStr, '-?\d+(?:\.\d+)?')
    if ($nums.Count -ge 2) {
        try {
            $lat = [double]$nums[0].Value
            $lon = [double]$nums[1].Value
            # Validate against Norway bounding box
            if ($lat -ge 57.0 -and $lat -le 72.0 -and $lon -ge 4.0 -and $lon -le 32.0) {
                return @{ Lat = $lat; Lon = $lon }
            }
        } catch { }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Write-Log "INFO" "=== PRTG outage check | device='$Device' group='$Group' status=$Status ==="

if (-not $ApiUrl) {
    $msg = "ERROR: ApiUrl is not configured. Set the `$ApiUrl variable at the top of this script."
    Write-Log "ERROR" $msg
    Write-Host $msg
    exit 1
}

$coords = Get-Coordinates $Location
if (-not $coords) {
    $msg = "ERROR: No usable GPS location for group '$Group'.`n" +
           "  Received -Location=$Location`n" +
           "  Set the group's Location field to GPS coordinates, e.g. '61.5120, 9.1234'."
    Write-Log "ERROR" $msg
    Write-Host $msg
    exit 1
}

$lat = $coords.Lat
$lon = $coords.Lon
Write-Log "INFO" "Coordinates: lat=$lat lon=$lon"

try {
    $uri = "{0}/check?lat={1}&lon={2}&device={3}&group={4}&sensor={5}&status={6}&down={7}&format=text" -f `
        $ApiUrl.TrimEnd('/'),
        $lat,
        $lon,
        [Uri]::EscapeDataString($Device),
        [Uri]::EscapeDataString($Group),
        [Uri]::EscapeDataString($Sensor),
        [Uri]::EscapeDataString($Status),
        [Uri]::EscapeDataString($Down)

    $headers = @{}
    if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

    Write-Log "INFO" "Calling API: $($ApiUrl.TrimEnd('/'))/check"

    $response = Invoke-WebRequest -Uri $uri -Headers $headers `
        -TimeoutSec $TimeoutSec -UseBasicParsing -Method Get

    Write-Host $response.Content
    Write-Log "INFO" "Done"
    exit 0

} catch [System.Net.WebException] {
    $msg = "ERROR: API call failed — $($_.Exception.Message)"
    Write-Log "ERROR" $msg
    Write-Host $msg
    exit 1
} catch {
    $msg = "ERROR: $_"
    Write-Log "ERROR" $msg
    Write-Host $msg
    exit 1
}
