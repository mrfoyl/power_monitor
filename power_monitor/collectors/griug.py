"""
Griug outage collector — covers Numedal and parts of Hallingdal in Innlandet.

Map: https://www.griug.no/driftsmeldinger/
     (iframe: https://quantinsight.net/outage/index.html#7080005052900)

API platform: Quant Insight / Embriq (powerapi.prod.hub.quant.embriq.no)
  GET /outage/netowner/{gsn}/outage
  Returns: { "outages": [...], "plannedOutages": [...] }

Outage object fields:
  actual        bool   — true if this is an active/real fault (not just planned)
  type          str    — "ongoingError" (fault) | "correction"/"troubleshooting" (planned)
  from          str    — ISO-8601 start time
  to            str    — ISO-8601 estimated end time
  substation    dict   — { id, name, description }
  polygon       list   — [[lat, lon], ...] bounding polygon of the affected area

Note on municipality:
  The API does not include municipality names. We compute the centroid of the
  outage polygon and reverse-geocode it with Kartverket's punktsok API —
  the same call used elsewhere in the codebase. One geocoding request is made
  per outage object when outages are present.

Note on customer count:
  The API does not expose customer count. num_affected is reported as 0.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

import requests

from .base import BaseCollector
from ..geocoding import lookup_gps
from ..models import PowerOutage

logger = logging.getLogger(__name__)

_GSN = "7080005052900"
_API_BASE = "https://powerapi.prod.hub.quant.embriq.no/outage"
_USER_AGENT = (
    "PowerMonitor/1.0 (Norwegian outage correlation tool; "
    "https://github.com/mrfoyl/power_monitor)"
)
_TIMEOUT = 15


def _fetch() -> dict:
    resp = requests.get(
        f"{_API_BASE}/netowner/{_GSN}/outage",
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json()


def _polygon_centroid(polygon: list) -> Optional[tuple[float, float]]:
    """Return the (lat, lon) centroid of a [[lat, lon], ...] polygon."""
    if not polygon:
        return None
    try:
        lats = [p[0] for p in polygon]
        lons = [p[1] for p in polygon]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    except (IndexError, TypeError):
        return None


def _geocode_polygon(polygon: list) -> Optional[str]:
    """Return municipality name by reverse-geocoding the polygon centroid."""
    center = _polygon_centroid(polygon)
    if not center:
        return None
    lat, lon = center
    location = lookup_gps(lat, lon)
    if location:
        return location.get("municipality")
    return None


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # API returns ISO-8601, e.g. "2026-04-27T09:00:00Z" or with offset
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class GriugCollector(BaseCollector):
    name = "Griug"
    region = "Numedal / Hallingdal (Nore og Uvdal, Rollag, Nes)"

    def fetch_outages(self) -> List[PowerOutage]:
        """Returns currently active outages (faults and active planned outages)."""
        try:
            data = _fetch()
        except requests.RequestException as e:
            logger.warning("Griug outage API failed: %s", e)
            return []

        now = datetime.now(timezone.utc)
        outages: List[PowerOutage] = []

        # Active faults — outages[] array contains only currently active faults
        for o in data.get("outages", []):
            substation = o.get("substation", {})
            municipality = _geocode_polygon(o.get("polygon", []))
            start_dt = _parse_dt(o.get("from"))
            end_dt = _parse_dt(o.get("to"))
            outages.append(PowerOutage(
                provider=self.name,
                event_id=str(substation.get("id", "")),
                status="Pågående",
                outage_type="Driftsforstyrrelse",
                municipality=municipality or "",
                start_time=start_dt,
                num_affected=0,   # not in API
                customer_message=(
                    substation.get("description") or substation.get("name") or None
                ),
                estimated_restoration=end_dt,
            ))

        # Active planned outages — plannedOutages[] that have already started
        for o in data.get("plannedOutages", []):
            start_dt = _parse_dt(o.get("from"))
            if not start_dt or start_dt > now:
                continue  # future — handled by fetch_upcoming()
            substation = o.get("substation", {})
            municipality = _geocode_polygon(o.get("polygon", []))
            end_dt = _parse_dt(o.get("to"))
            outages.append(PowerOutage(
                provider=self.name,
                event_id=str(substation.get("id", "")) + "_plan",
                status="Pågående",
                outage_type="Utkobling",
                municipality=municipality or "",
                start_time=start_dt,
                num_affected=0,
                customer_message=(
                    substation.get("description") or substation.get("name") or None
                ),
                estimated_restoration=end_dt,
            ))

        return outages

    def fetch_upcoming(self) -> List[PowerOutage]:
        """Returns future scheduled outages (plannedOutages not yet started)."""
        try:
            data = _fetch()
        except requests.RequestException as e:
            logger.warning("Griug outage API failed: %s", e)
            return []

        now = datetime.now(timezone.utc)
        upcoming: List[PowerOutage] = []

        for o in data.get("plannedOutages", []):
            start_dt = _parse_dt(o.get("from"))
            if not start_dt or start_dt <= now:
                continue  # already started or no start time
            substation = o.get("substation", {})
            municipality = _geocode_polygon(o.get("polygon", []))
            end_dt = _parse_dt(o.get("to"))
            upcoming.append(PowerOutage(
                provider=self.name,
                event_id=str(substation.get("id", "")) + "_upcoming",
                status="Planlagt",
                outage_type="Utkobling",
                municipality=municipality or "",
                start_time=start_dt,
                num_affected=0,
                customer_message=(
                    substation.get("description") or substation.get("name") or None
                ),
                estimated_restoration=end_dt,
            ))

        return upcoming
