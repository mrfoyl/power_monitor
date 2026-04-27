#!/usr/bin/env python3
"""
PRTG EXE/Script Notification — Power Outage Correlation
========================================================

When a PRTG sensor goes Down this script:
  1. Parses the GPS location from the PRTG group's Location field
  2. Either calls a remote Power Monitor API server (recommended) OR
     runs the outage check locally (requires power_monitor installed)
  3. Prints a plain-text result — PRTG exposes this as %scriptresult in
     notification templates (email, Teams, etc.)
  4. Writes an entry to a rolling log file for audit purposes

Exit codes:
  0  — completed without errors (outage found OR not found — both are OK)
  1  — fatal error (bad arguments, geocoding failed, server unreachable)

REMOTE MODE (recommended)
--------------------------
Run server.py on a central server, then set OUTAGE_API_URL below to point
at it. The PRTG script needs no Python dependencies except `requests`.

  OUTAGE_API_URL = "http://192.168.1.50:5000"

The PRTG server only needs:
  - Python 3.x  (any version, just for this script)
  - pip install requests

LOCAL MODE
----------
Leave OUTAGE_API_URL = "" to run the check directly on the PRTG server.
Requires Python 3.11+ and the full power_monitor package installed.

HOW TO INSTALL
--------------
1. Copy this file to PRTG's EXE notification directory:
     C:\\Program Files (x86)\\PRTG Network Monitor\\Notifications\\EXE\\

2. Set OUTAGE_API_URL below (remote mode) or install power_monitor (local).

3. Create a notification in PRTG (see README.md for full walkthrough):
     Setup -> Account Settings -> Notifications -> Add Notification
     Type: Execute Program
     Program File: prtg_outage_check.py
     Parameters (copy exactly):
       --device "%device" --group "%group" --sensor "%name" ^
       --status "%status" --location "%location" --down "%down"

4. Add the notification to a trigger on your site groups:
     Group -> Notifications -> Add State Trigger
     When: Down  ->  Execute: Power Outage Check

5. Add %scriptresult to your alert notification message template.

PRTG LOCATION FIELD FORMAT
---------------------------
Set the Location field on each PRTG group to GPS coordinates:
  "61.5120, 9.1234"        decimal degrees (preferred)
  "61.5120,9.1234"
  "61.5120° N, 9.1234° E"  copy-paste from Google Maps is fine

Group -> Edit -> Settings -> Location field.
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import requests as _requests

# ---------------------------------------------------------------------------
# Configuration — edit these values
# ---------------------------------------------------------------------------

# URL of the Power Monitor API server (server.py).
# Set to "" to run the check locally instead.
OUTAGE_API_URL = ""   # e.g. "http://192.168.1.50:5000"

# API key — must match POWER_MONITOR_API_KEY on the server.
# Leave empty if the server has no API key configured.
OUTAGE_API_KEY = ""

# Log file next to this script. Set to None to disable.
LOG_FILE = Path(__file__).resolve().parent / "prtg_outage_check.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# Timeout for remote API calls (seconds)
REQUEST_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Local mode imports (only needed when OUTAGE_API_URL is empty)
# ---------------------------------------------------------------------------

if not OUTAGE_API_URL:
    _HERE = Path(__file__).resolve().parent
    for _candidate in (_HERE, _HERE.parent):
        if (_candidate / "power_monitor").is_dir():
            if str(_candidate) not in sys.path:
                sys.path.insert(0, str(_candidate))
            break

    try:
        from power_monitor.collectors.elvia import ElviaCollector
        from power_monitor.collectors.vevig import VevigCollector
        from power_monitor.collectors.glitre import GlitreCollector
        from power_monitor.collectors.arva import ArvaCollector
        from power_monitor.geocoding import lookup_gps
        from power_monitor.models import PowerOutage
        _LOCAL_PROVIDERS = [ElviaCollector, VevigCollector, GlitreCollector, ArvaCollector]
    except ImportError as e:
        print(f"ERROR: Could not import power_monitor: {e}")
        print("Set OUTAGE_API_URL to use remote mode, or install power_monitor locally.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    log = logging.getLogger("prtg_outage_check")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)

    if LOG_FILE:
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
                LOG_FILE.rename(LOG_FILE.with_suffix(".log.1"))
            fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass
    return log


log = _setup_logging()


# ---------------------------------------------------------------------------
# GPS parsing
# ---------------------------------------------------------------------------

def parse_location(location_str: str) -> tuple[float, float] | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", location_str)
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
            if 57.0 <= lat <= 72.0 and 4.0 <= lon <= 32.0:
                return lat, lon
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Remote mode
# ---------------------------------------------------------------------------

def _check_remote(
    lat: float, lon: float,
    device: str, group: str, sensor: str, status: str, down: str,
) -> str:
    """Call the Power Monitor API server and return the plain-text result."""
    params = {
        "lat":    lat,
        "lon":    lon,
        "device": device,
        "group":  group,
        "sensor": sensor,
        "status": status,
        "down":   down,
        "format": "text",
    }
    headers = {}
    if OUTAGE_API_KEY:
        headers["X-API-Key"] = OUTAGE_API_KEY

    url = f"{OUTAGE_API_URL.rstrip('/')}/check"
    log.info("Calling remote API: %s", url)
    resp = _requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Local mode
# ---------------------------------------------------------------------------

def _check_local(
    lat: float, lon: float,
    device: str, group: str, sensor: str, status: str, down: str,
) -> str:
    """Run the outage check in-process (no remote server needed)."""
    location = lookup_gps(lat, lon)
    if not location:
        raise RuntimeError(f"Geocoding failed for {lat:.5f}, {lon:.5f}")

    municipality = location["municipality"]
    log.info("GPS %.5f, %.5f -> %s (%s)", lat, lon, municipality, location.get("county", ""))

    outages = []
    for Cls in _LOCAL_PROVIDERS:
        collector = Cls()
        try:
            found = collector.fetch_outages()
            outages.extend(
                o for o in found
                if o.municipality.upper() == municipality.upper()
            )
        except NotImplementedError:
            pass
        except Exception as e:
            log.warning("Provider %s failed: %s", collector.name, e)

    header = (
        f"Device : {device}\n"
        f"Group  : {group}\n"
        f"Sensor : {sensor}\n"
        f"Status : {status}  (down {down})\n"
        f"Area   : {municipality}\n"
        f"{'-' * 60}\n"
    )
    if not outages:
        return (
            header
            + "RESULT : No active power outages detected in this area.\n"
            + "         Investigate other causes (hardware, connectivity, config).\n"
        )
    lines = [header, f"RESULT : {len(outages)} active power outage(s) found -- likely cause!\n"]
    for o in outages:
        lines.append(f"  [{o.provider}] {o.outage_type} | {o.num_affected} customers affected")
        if o.customer_message:
            lines.append(f"    {o.customer_message}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="PRTG power outage correlation check")
    parser.add_argument("--device",   default="(unknown)")
    parser.add_argument("--group",    default="(unknown)")
    parser.add_argument("--sensor",   default="(unknown)")
    parser.add_argument("--status",   default="Down")
    parser.add_argument("--location", default="")
    parser.add_argument("--down",     default="")
    parser.add_argument("--lat",  type=float, default=None)
    parser.add_argument("--lon",  type=float, default=None)
    args = parser.parse_args()

    mode = "remote" if OUTAGE_API_URL else "local"
    log.info(
        "=== PRTG outage check [%s] | device=%r group=%r status=%s ===",
        mode, args.device, args.group, args.status,
    )

    # Resolve GPS
    if args.lat is not None and args.lon is not None:
        coords = (args.lat, args.lon)
    elif args.location:
        coords = parse_location(args.location)
    else:
        coords = None

    if not coords:
        msg = (
            f"ERROR: No usable GPS location for group '{args.group}'.\n"
            f"  Received --location={args.location!r}\n"
            f"  Set the group's Location field to GPS coordinates, e.g. '61.5120, 9.1234'."
        )
        log.error(msg)
        print(msg)
        return 1

    lat, lon = coords
    log.info("Coordinates: lat=%.5f lon=%.5f", lat, lon)

    try:
        if OUTAGE_API_URL:
            output = _check_remote(lat, lon, args.device, args.group,
                                   args.sensor, args.status, args.down)
        else:
            output = _check_local(lat, lon, args.device, args.group,
                                  args.sensor, args.status, args.down)
    except Exception as e:
        msg = f"ERROR: {e}"
        log.error(msg)
        print(msg)
        return 1

    print(output)
    log.info("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
