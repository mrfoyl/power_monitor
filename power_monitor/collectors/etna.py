"""
Etna Nett outage collector — covers Valdres and Land in Innlandet:
  Etnedal, Nord-Aurdal, Nordre Land, Søndre Land, Snertingdal (part of Gjøvik)

Endpoints discovered 2026-04-27 by extracting all geoserver-api paths from
https://avbruddskart.etna.no/scripts/main.js

API: Custom "geoserver-api" platform (same as Vevig — not standard GeoServer).

Primary endpoint: GetApplicationData
  Returns a list of named service areas, each with current outage counts:
    fc  = active fault (unplanned) count
    fcc = customers affected by faults
    pc  = active planned outage count
    pcc = customers affected by planned outages
    uc  = active unscheduled(?) count
    ucc = customers affected

Note on Snertingdal:
  Snertingdal is an area label used by Etna but is administratively part of
  Gjøvik municipality. Reverse geocoding a GPS point in Snertingdal will
  return "Gjøvik" as the municipality, so we map the area label accordingly.
"""

import json
import logging
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

# Etna uses "Snertingdal" as an area label, but Kartverket returns "Gjøvik"
# for GPS points in that area.
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
    # Same double-encoding as Vevig: the JSON body is itself a JSON string.
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _area_to_municipality(label: str) -> str:
    """
    Normalise area label to a municipality name.
    Checks overrides first, then strips directional suffixes.
    """
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


class EtnaCollector(BaseCollector):
    name = "Etna Nett"
    region = "Valdres / Land (Etnedal, Nord-Aurdal, Nordre Land, Søndre Land)"

    def fetch_outages(self) -> List[PowerOutage]:
        try:
            data = _get("GetApplicationData")
        except requests.RequestException as e:
            logger.warning("Etna GetApplicationData failed: %s", e)
            return []

        areas = data.get("scopes", {}).get("p", {}).get("areas", [])
        outages: List[PowerOutage] = []

        for area in areas:
            label: str = area.get("label", "")
            fc: int = area.get("fc", 0)    # unplanned fault count
            fcc: int = area.get("fcc", 0)  # unplanned customers
            pc: int = area.get("pc", 0)    # planned outage count
            pcc: int = area.get("pcc", 0)  # planned customers
            uc: int = area.get("uc", 0)    # unscheduled count
            ucc: int = area.get("ucc", 0)  # unscheduled customers

            if fc == 0 and pc == 0 and uc == 0:
                continue  # no active outages in this area

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
            if uc > 0:
                outages.append(PowerOutage(
                    provider=self.name,
                    event_id=f"{area.get('id')}_unsch",
                    status="Pågående",
                    outage_type="Driftsforstyrrelse",
                    municipality=municipality,
                    start_time=None,
                    num_affected=ucc,
                    customer_message=f"{uc} unscheduled outage(s) in {label}",
                ))

        return outages

    def fetch_summary(self) -> Optional[dict]:
        """
        Returns aggregate counts across the whole network.

        Returns dict with keys: fault_count, fault_customers,
                                plan_count, plan_customers
        or None on error.
        """
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
