"""
Etna Nett outage collector — covers Valdres and Land in Innlandet:
  Etnedal, Nord-Aurdal, Nordre Land, Søndre Land, Snertingdal (part of Gjøvik)

Endpoints discovered 2026-04-27 by extracting all geoserver-api paths from
https://avbruddskart.etna.no/scripts/main.js

API: Custom "geoserver-api" platform (same as Vevig — not standard GeoServer).

Area-level fields returned by GetApplicationData:
  fc  / fcc  — active unplanned fault count / customers affected (happening NOW)
  pc  / pcc  — active planned outage count / customers affected (happening NOW)
  uc  / ucc  — upcoming scheduled outage count / customers (NOT YET STARTED)

The individual `outages` array in the same response carries per-outage timestamps
(plannedstart, plannedend, starttime). starttime == 0 means the outage has not
yet started; starttime > now means it is future. We match outages to areas by
customer count (cc == ucc) to get per-area start times for upcoming outages.

Note on Snertingdal:
  Administratively part of Gjøvik municipality; Kartverket returns "Gjøvik"
  for GPS points there, so we override the area label accordingly.
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

_BASE = "https://avbruddskart.etna.no/geoserver-api"
_USER_AGENT = (
    "PowerMonitor/1.0 (Norwegian outage correlation tool; "
    "https://github.com/mrfoyl/power_monitor)"
)
_TIMEOUT = 15

_AREA_OVERRIDES = {
    "snertingdal": "Gjøvik",
}


def _get(path: str) -> dict:
    resp = requests.get(
        f"{_BASE}/{path}",
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _area_to_municipality(label: str) -> str:
    key = label.strip().lower()
    if key in _AREA_OVERRIDES:
        return _AREA_OVERRIDES[key]
    suffixes = (" vest", " øst", " nord", " sør", " aust")
    name = label.strip()
    for s in suffixes:
        if name.lower().endswith(s):
            name = name[: -len(s)].strip()
            break
    return name


def _ms_to_dt(ms: int) -> Optional[datetime]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class EtnaCollector(BaseCollector):
    name = "Etna Nett"
    region = "Valdres / Land (Etnedal, Nord-Aurdal, Nordre Land, Søndre Land)"

    def fetch_outages(self) -> List[PowerOutage]:
        """Returns currently active outages only (fc and pc — happening right now)."""
        try:
            data = _get("GetApplicationData")
        except requests.RequestException as e:
            logger.warning("Etna GetApplicationData failed: %s", e)
            return []

        areas = data.get("scopes", {}).get("p", {}).get("areas", [])
        outages: List[PowerOutage] = []

        for area in areas:
            label: str = area.get("label", "")
            fc: int = area.get("fc", 0)
            fcc: int = area.get("fcc", 0)
            pc: int = area.get("pc", 0)
            pcc: int = area.get("pcc", 0)

            if fc == 0 and pc == 0:
                continue

            municipality = _area_to_municipality(label)

            if fc > 0:
                outages.append(PowerOutage(
                    provider=self.name,
                    event_id=f"{area.get('id')}_fault",
                    status="Pågående",
                    outage_type="Driftsforstyrrelse",
                    municipality=municipality,
                    start_time=None,
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
        Timestamps are taken from the individual outages array and matched
        to areas by customer count.
        """
        try:
            data = _get("GetApplicationData")
        except requests.RequestException as e:
            logger.warning("Etna GetApplicationData failed: %s", e)
            return []

        p = data.get("scopes", {}).get("p", {})
        areas = p.get("areas", [])
        raw_outages = p.get("outages", [])
        now_ms = int(time.time() * 1000)

        # Build a lookup: customer_count → earliest plannedstart (ms)
        # for outages that have not yet started
        cc_to_start: dict[int, int] = {}
        for o in raw_outages:
            st = o.get("starttime", 0)
            ps = o.get("plannedstart", 0)
            # Include if not yet started or start is in the future
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
            start_dt = _ms_to_dt(planned_start_ms) if planned_start_ms else None

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
        try:
            data = _get("content/outageTableData.json")
            summary = data.get("p", {}).get("summary", {})
            return {
                "fault_count":     int(summary.get("faultrunning", {}).get("nr", 0)),
                "fault_customers": int(summary.get("faultrunning", {}).get("customers", 0)),
                "plan_count":      int(summary.get("planrunning", {}).get("nr", 0)),
                "plan_customers":  int(summary.get("planrunning", {}).get("customers", 0)),
            }
        except Exception as e:
            logger.warning("Etna outageTableData failed: %s", e)
            return None
