#!/usr/bin/env python3
"""
PRTG EXE/Script Notification — Power Outage Correlation
========================================================

When a PRTG sensor goes Down this script:
  1. Parses the GPS location from the PRTG group's Location field
  2. Reverse-geocodes it to a Norwegian municipality (Kartverket API)
  3. Queries the configured power outage providers for that municipality
  4. Prints a plain-text result that PRTG includes in the alert notification
  5. Writes an entry to a rolling log file for audit purposes

Exit codes (used by PRTG to set the notification result text):
  0  — completed without errors (outage found OR not found — both are OK)
  1  — fatal error (bad arguments, geocoding failed, all providers failed)

HOW TO INSTALL
--------------
1. Copy this file to PRTG's EXE notification directory:
     C:\\Program Files (x86)\\PRTG Network Monitor\\Notifications\\EXE\\

2. Make sure Python 3.11+ is installed on the PRTG server and on PATH,
   and that the power_monitor package (this project) is importable.
   Easiest: pip install -e <path-to-power_monitor> on the PRTG server,
   OR copy the entire power_monitor/ folder next to this script.

3. Create a notification in PRTG (see README.md for full walkthrough):
     Setup -> Account Settings -> Notifications -> Add Notification
     Type: Execute Program
     Program File: prtg_outage_check.py
     Parameters (copy exactly):
       --device "%device" --group "%group" --sensor "%name" ^
       --status "%status" --location "%location" --down "%down"

4. Assign the notification as a trigger on any sensor or group:
     Sensor -> Notifications tab -> Add State Trigger
     When: Down  ->  Execute: <your notification>

PRTG LOCATION FIELD FORMAT
---------------------------
Set the Location field on each PRTG group/device to the site's GPS
coordinates in one of these formats (all are accepted):
  "61.5120, 9.1234"
  "61.5120,9.1234"
  "61.5120° N, 9.1234° E"   (copy-paste from Google Maps is fine)

To set it: Group -> Edit -> Location tab -> enter coordinates.
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Allow running from PRTG's EXE directory even if power_monitor is not
# installed as a package — add this script's directory and its parent to path.
# ---------------------------------------------------------------------------
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
except ImportError as e:
    print(f"ERROR: Could not import power_monitor: {e}")
    print("Make sure power_monitor/ is next to this script or installed via pip.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Log file location — change if needed. Set to None to disable file logging.
LOG_FILE = Path(_HERE) / "prtg_outage_check.log"

# Max log file size before rotation (bytes)
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# Providers to query, in priority order.
# All providers are queried; results are filtered by municipality.
PROVIDERS = [ElviaCollector, VevigCollector, GlitreCollector, ArvaCollector]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    log = logging.getLogger("prtg_outage_check")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler (captured by PRTG as script output)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)

    # File handler (rolling log for audit)
    if LOG_FILE:
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
                backup = LOG_FILE.with_suffix(".log.1")
                LOG_FILE.rename(backup)
            fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass  # Don't crash if we can't write the log

    return log


log = _setup_logging()


# ---------------------------------------------------------------------------
# GPS parsing
# ---------------------------------------------------------------------------

def parse_location(location_str: str) -> tuple[float, float] | None:
    """
    Parse latitude and longitude from a freeform location string.

    Accepts:
      "61.5120, 9.1234"
      "61.5120,9.1234"
      "61.5120° N, 9.1234° E"
      "61° 30' 43\" N, 9° 7' 24\" E"   (DMS — approximated)
    Returns (lat, lon) or None if unparseable.
    """
    # Extract all decimal numbers (handles degree symbols, N/S/E/W labels)
    nums = re.findall(r"-?\d+(?:\.\d+)?", location_str)
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
            # Sanity check for Norway
            if 57.0 <= lat <= 72.0 and 4.0 <= lon <= 32.0:
                return lat, lon
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def check_outages_for_location(
    lat: float, lon: float
) -> tuple[list[PowerOutage], str, dict] | None:
    """
    Returns (outages, municipality, location_info) or None if geocoding fails.
    """
    location = lookup_gps(lat, lon)
    if not location:
        log.error("Reverse geocoding failed for %.5f, %.5f — no address found", lat, lon)
        return None

    municipality = location["municipality"]
    county = location.get("county", "")
    log.info("GPS %.5f, %.5f -> %s (%s)", lat, lon, municipality, county)

    outages: list[PowerOutage] = []
    for Cls in PROVIDERS:
        collector = Cls()
        try:
            found = collector.fetch_outages()
            matching = [
                o for o in found
                if o.municipality.upper() == municipality.upper()
            ]
            outages.extend(matching)
        except NotImplementedError:
            pass
        except Exception as e:
            log.warning("Provider %s failed: %s", collector.name, e)

    return outages, municipality, location


def _format_result(
    outages: list[PowerOutage],
    municipality: str,
    device: str,
    group: str,
    sensor: str,
    status: str,
    down: str,
) -> str:
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

    lines = [header, f"RESULT : {len(outages)} active power outage(s) found — likely cause!\n"]
    for o in outages:
        lines.append(
            f"  [{o.provider}] {o.outage_type} | "
            f"{o.num_affected} customers affected"
        )
        if o.customer_message:
            lines.append(f"    {o.customer_message}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="PRTG power outage correlation check"
    )
    parser.add_argument("--device",   default="(unknown)", help="%%device")
    parser.add_argument("--group",    default="(unknown)", help="%%group")
    parser.add_argument("--sensor",   default="(unknown)", help="%%name")
    parser.add_argument("--status",   default="Down",      help="%%status")
    parser.add_argument("--location", default="",          help="%%location (lat,lon)")
    parser.add_argument("--down",     default="",          help="%%down")
    # Allow explicit lat/lon override (some PRTG versions expose these directly)
    parser.add_argument("--lat",  type=float, default=None)
    parser.add_argument("--lon",  type=float, default=None)
    args = parser.parse_args()

    log.info(
        "=== PRTG outage check | device=%r group=%r status=%s ===",
        args.device, args.group, args.status,
    )

    # Resolve GPS coordinates
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
    log.info("Parsed coordinates: lat=%.5f lon=%.5f", lat, lon)

    result = check_outages_for_location(lat, lon)
    if result is None:
        return 1

    outages, municipality, _location = result

    output = _format_result(
        outages, municipality,
        args.device, args.group, args.sensor, args.status, args.down,
    )

    print(output)
    log.info(
        "Result: %d outage(s) found in %s",
        len(outages), municipality,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
