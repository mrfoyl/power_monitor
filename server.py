"""
Power Monitor API server
========================

Exposes a single HTTP endpoint that the PRTG EXE script (or anything else)
can call to check for power outages near a GPS location.

Usage:
    python server.py

Endpoints:
    GET /check?lat=61.5120&lon=9.1234
        Query params:
            lat, lon    GPS coordinates (required)
            device      PRTG device name  (optional, shown in output)
            group       PRTG group name   (optional, shown in output)
            sensor      PRTG sensor name  (optional, shown in output)
            status      PRTG status       (optional, shown in output)
            down        Time down         (optional, shown in output)
            format      "text" (default) or "json"

    GET /health
        Returns {"status": "ok"} — use as a PRTG HTTP sensor to monitor
        the server itself.

Configuration:
    Edit the CONFIG block below, or set environment variables:
        POWER_MONITOR_HOST      Bind address    (default: 0.0.0.0)
        POWER_MONITOR_PORT      Port            (default: 5000)
        POWER_MONITOR_API_KEY   Optional API key. If set, all /check requests
                                must include header  X-API-Key: <key>
                                or query param       api_key=<key>

Running in production:
    pip install gunicorn
    gunicorn -w 2 -b 0.0.0.0:5000 server:app
"""

import html
import os
import logging
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, request, jsonify, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from power_monitor.collectors.elvia import ElviaCollector
from power_monitor.collectors.vevig import VevigCollector
from power_monitor.collectors.etna import EtnaCollector
from power_monitor.collectors.griug import GriugCollector
from power_monitor.collectors.glitre import GlitreCollector
from power_monitor.collectors.arva import ArvaCollector
from power_monitor.geocoding import lookup_gps
from power_monitor.models import PowerOutage

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST    = os.environ.get("POWER_MONITOR_HOST", "0.0.0.0")
PORT    = int(os.environ.get("POWER_MONITOR_PORT", 5000))
API_KEY = os.environ.get("POWER_MONITOR_API_KEY", "")  # empty = no auth

PROVIDERS = [ElviaCollector, VevigCollector, EtnaCollector, GriugCollector, GlitreCollector, ArvaCollector]

# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("power_monitor.server")

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],        # no blanket limit; applied per-route
    storage_uri="memory://",  # in-process store; switch to redis:// for multi-worker
)

_MAX_PARAM = 200   # max length for PRTG context strings passed as query params


def _trunc(s: str, n: int = _MAX_PARAM) -> str:
    return s[:n] if s else s


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _authorized() -> bool:
    if not API_KEY:
        return True
    return (
        request.headers.get("X-API-Key") == API_KEY
        or request.args.get("api_key") == API_KEY
    )


# ---------------------------------------------------------------------------
# Outage logic
# ---------------------------------------------------------------------------

def _fetch_outages(lat: float, lon: float) -> tuple[list[PowerOutage], str] | None:
    """
    Reverse-geocode and query all providers.
    Returns (outages, municipality) or None if geocoding fails.
    """
    location = lookup_gps(lat, lon)
    if not location:
        return None

    municipality = location["municipality"]
    log.info("GPS %.5f, %.5f -> %s (%s)", lat, lon, municipality, location.get("county", ""))

    outages: list[PowerOutage] = []
    for Cls in PROVIDERS:
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

    return outages, municipality


def _text_response(
    outages: list[PowerOutage],
    municipality: str,
    device: str,
    group: str,
    sensor: str,
    status: str,
    down: str,
) -> str:
    # Escape fields from external sources before embedding in notification text.
    # PRTG inserts %scriptresult into HTML email/Teams templates — unescaped HTML
    # from a compromised API would otherwise render as links or markup.
    safe_municipality = html.escape(municipality)
    header = (
        f"Device : {device}\n"
        f"Group  : {group}\n"
        f"Sensor : {sensor}\n"
        f"Status : {status}  (down {down})\n"
        f"Area   : {safe_municipality}\n"
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
            lines.append(f"    {html.escape(o.customer_message)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


@app.get("/check")
@limiter.limit("30 per minute")
def check():
    if not _authorized():
        return Response("Unauthorized", status=401)

    # Parse coordinates
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return Response("Missing or invalid lat/lon parameters", status=400)

    # Optional PRTG context fields — clamped to prevent oversized log/output entries
    device = _trunc(request.args.get("device", "(unknown)"))
    group  = _trunc(request.args.get("group",  "(unknown)"))
    sensor = _trunc(request.args.get("sensor", "(unknown)"))
    status = _trunc(request.args.get("status", "Down"), 50)
    down   = _trunc(request.args.get("down",   ""), 50)
    fmt    = request.args.get("format", "text").lower()

    log.info("Check request: device=%r group=%r lat=%.5f lon=%.5f", device, group, lat, lon)

    result = _fetch_outages(lat, lon)
    if result is None:
        msg = f"Geocoding failed for {lat:.5f}, {lon:.5f} — no address found nearby"
        log.error(msg)
        if fmt == "json":
            return jsonify({"error": msg}), 502
        return Response(f"ERROR: {msg}\n", status=502)

    outages, municipality = result
    log.info("Found %d outage(s) in %s", len(outages), municipality)

    if fmt == "json":
        return jsonify({
            "municipality": municipality,
            "outage_count": len(outages),
            "outages": [
                {
                    "provider":    o.provider,
                    "type":        o.outage_type,
                    "status":      o.status,
                    "affected":    o.num_affected,
                    "message":     o.customer_message,
                    "start_time":  o.start_time.isoformat() if o.start_time else None,
                }
                for o in outages
            ],
        })

    return Response(
        _text_response(outages, municipality, device, group, sensor, status, down),
        mimetype="text/plain",
    )


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting Power Monitor API on %s:%d", HOST, PORT)
    if API_KEY:
        log.info("API key authentication enabled")
    else:
        log.warning("No API key set — server is open to anyone on the network")
    app.run(host=HOST, port=PORT)
