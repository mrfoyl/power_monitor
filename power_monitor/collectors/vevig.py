"""
Vevig outage collector — covers Gudbrandsdalen in Innlandet:
  Nord-Fron, Sør-Fron, Ringebu, Skjåk, Øyer

Endpoints discovered 2026-04-27 by extracting all geoserver-api paths from
https://avbruddskart.vevig.no/scripts/main.js

API: Custom "geoserver-api" platform (not standard GeoServer).

Primary endpoint: GetApplicationData
  Returns a list of named service areas, each with current outage counts:
    fc  = active fault (unplanned) count
    fcc = customers affected by faults
    pc  = active planned outage count
    pcc = customers affected by planned outages
  Also contains a (currently empty when quiet) `outages` array that would
  hold individual outage records — schema TBD once a live outage is observed.

Secondary endpoints (used for display in the web UI, not needed for CLI):
  content/outageTableData.json   — aggregate summary + mainAreas breakdown
  content/outageGraphData.json   — time-series of customer counts
  GetObjectsByTiles?zoom=&tiles= — map tile points (coordinates + IDs only)

Municipality matching:
  Area labels like "Nord-Fron vest" / "Nord-Fron øst" map to the municipality
  "Nord-Fron". Matching checks whether the label starts with the municipality
  name (case-insensitive), so both sub-areas are caught by a single lookup.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from .base import BaseCollector
from ..models import PowerOutage

logger = logging.getLogger(__name__)

_BASE = "https://avbruddskart.vevig.no/geoserver-api"
_USER_AGENT = (
    "PowerMonitor/1.0 (Norwegian outage correlation tool; "
    "https://github.com/IFKIKT/power_monitor)"
)
_TIMEOUT = 15


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _get(path: str) -> dict:
    resp = requests.get(
        f"{_BASE}/{path}",
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    # The API double-encodes its responses: the JSON body is itself a JSON string.
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _area_to_municipality(label: str) -> str:
    """
    Strip directional suffixes to get the bare municipality name.
    'Nord-Fron vest' → 'Nord-Fron'
    'Ringebu nord'   → 'Ringebu'
    'Skjåk'          → 'Skjåk'
    """
    suffixes = (" vest", " øst", " nord", " sør", " aust")
    name = label.strip()
    for s in suffixes:
        if name.lower().endswith(s):
            name = name[: -len(s)].strip()
            break
    return name


class VevigCollector(BaseCollector):
    name = "Vevig"
    region = "Gudbrandsdalen (Nord-Fron, Sør-Fron, Ringebu, Skjåk, Øyer)"

    def fetch_outages(self) -> List[PowerOutage]:
        try:
            data = _get("GetApplicationData")
        except requests.RequestException as e:
            logger.warning("Vevig GetApplicationData failed: %s", e)
            return []

        areas = data.get("scopes", {}).get("p", {}).get("areas", [])
        outages: List[PowerOutage] = []

        for area in areas:
            label: str = area.get("label", "")
            fc: int = area.get("fc", 0)   # unplanned fault count
            fcc: int = area.get("fcc", 0) # unplanned customers
            pc: int = area.get("pc", 0)   # planned outage count
            pcc: int = area.get("pcc", 0) # planned customers

            if fc == 0 and pc == 0:
                continue  # no active outages in this area

            municipality = _area_to_municipality(label)

            # Emit one record per outage type that is active in this area
            if fc > 0:
                outages.append(PowerOutage(
                    provider=self.name,
                    event_id=f"{area.get('id')}_fault",
                    status="Pågående",
                    outage_type="Driftsforstyrrelse",
                    municipality=municipality,
                    start_time=None,   # not exposed in this endpoint
                    num_affected=fcc,
                    customer_message=f"{fc} fault(s) in {label}",
                ))
            if pc > 0:
                outages.append(PowerOutage(
                    provider=self.name,
                    event_id=f"{area.get('id')}_plan",
                    status="Pågående",
                    outage_type="Utkobling",
                    municipality=municipality,
                    start_time=None,
                    num_affected=pcc,
                    customer_message=f"{pc} planned outage(s) in {label}",
                ))

        return outages

    def fetch_upcoming(self) -> List[PowerOutage]:
        """
        Returns future scheduled outages (uc — not yet started).
        Timestamps matched to areas by customer count where possible.
        """
        try:
            data = _get("GetApplicationData")
        except requests.RequestException as e:
            logger.warning("Vevig GetApplicationData failed: %s", e)
            return []

        p = data.get("scopes", {}).get("p", {})
        areas = p.get("areas", [])
        raw_outages = p.get("outages", [])
        now_ms = int(time.time() * 1000)

        cc_to_start: dict[int, int] = {}
        for o in raw_outages:
            st = o.get("starttime", 0)
            ps = o.get("plannedstart", 0)
            if ps and (st == 0 or st > now_ms):
                cc = o.get("cc", 0)
                if cc not in cc_to_start or ps < cc_to_start[cc]:
                    cc_to_start[cc] = ps

        upcoming: List[PowerOutage] = []
        for area in areas:
            label: str = area.get("label", "")
            uc: int = area.get("uc", 0)
            ucc: int = area.get("ucc", 0)

            if uc == 0:
                continue

            municipality = _area_to_municipality(label)
            planned_start_ms = cc_to_start.get(ucc)
            start_dt = (
                datetime.fromtimestamp(planned_start_ms / 1000, tz=timezone.utc)
                if planned_start_ms else None
            )

            upcoming.append(PowerOutage(
                provider=self.name,
                event_id=f"{area.get('id')}_upcoming",
                status="Planlagt",
                outage_type="Utkobling",
                municipality=municipality,
                start_time=start_dt,
                num_affected=ucc,
                customer_message=f"{uc} scheduled outage(s) in {label}",
            ))

        return upcoming

    def fetch_summary(self) -> Optional[dict]:
        """
        Returns aggregate counts across the whole network, useful for a
        quick 'is anything happening?' check without iterating areas.

        Returns dict with keys: fault_count, fault_customers,
                                plan_count, plan_customers
        or None on error.
        """
        try:
            data = _get("content/outageTableData.json")
            summary = data.get("p", {}).get("summary", {})
            return {
                "fault_count":     _safe_int(summary.get("faultrunning", {}).get("nr")),
                "fault_customers": _safe_int(summary.get("faultrunning", {}).get("customers")),
                "plan_count":      _safe_int(summary.get("planrunning", {}).get("nr")),
                "plan_customers":  _safe_int(summary.get("planrunning", {}).get("customers")),
            }
        except Exception as e:
            logger.warning("Vevig outageTableData failed: %s", e)
            return None
