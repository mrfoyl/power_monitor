"""
Shared base for utilities that publish outage data via ArcGIS REST (MapServer/FeatureServer).

The ArcGIS REST query API is well documented:
https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/

Typical endpoint pattern:
  https://<host>/arcgis/rest/services/<folder>/<service>/FeatureServer/<layer>/query?f=json&where=1=1&outFields=*&returnGeometry=false
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

import requests

from .base import BaseCollector
from ..models import PowerOutage

logger = logging.getLogger(__name__)

# Conservative settings — we are a light read-only consumer
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "PowerMonitor/1.0 (Norwegian outage correlation tool; conservative polling; "
    "https://github.com/your-org/power_monitor)"
)


class ArcGISCollector(BaseCollector):
    """
    Base collector for ArcGIS ADMS outage services.

    Subclasses declare `query_urls` as a list of MapServer/FeatureServer layer
    base URLs (without /query). All endpoints must return standard ADMS outage
    attributes (EVENTID, STATE_TXT, STARTTIME, TYPE_TXT, MUNICIPAL_TXT, NUM_AB).
    """

    query_urls: List[str] = []

    def _query_url(self, base_url: str) -> str:
        return (
            f"{base_url}/query"
            "?f=json"
            "&where=1%3D1"
            "&outFields=*"
            "&returnGeometry=false"
        )

    def _parse_timestamp(self, ms: Optional[int]) -> Optional[datetime]:
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    def _parse_feature(self, feature: dict) -> Optional[PowerOutage]:
        attrs = feature.get("attributes", {})
        event_id = attrs.get("EVENTID")
        if event_id is None:
            return None
        return PowerOutage(
            provider=self.name,
            event_id=str(event_id),
            status=attrs.get("STATE_TXT", "").strip(),
            outage_type=attrs.get("TYPE_TXT", "").strip(),
            municipality=(attrs.get("MUNICIPAL_TXT") or "").strip(),
            start_time=self._parse_timestamp(attrs.get("STARTTIME")),
            num_affected=attrs.get("NUM_AB") or 0,
            customer_message=(attrs.get("CUSTOMER_WEB_TEXT") or "").strip() or None,
            grid_level=(attrs.get("GRIDLEVEL_TXT") or "").strip() or None,
        )

    def fetch_outages(self) -> List[PowerOutage]:
        outages: List[PowerOutage] = []
        seen: set[str] = set()

        for base_url in self.query_urls:
            url = self._query_url(base_url)
            try:
                resp = requests.get(
                    url,
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.json()

                if "error" in data:
                    logger.warning("%s returned error: %s", base_url, data["error"])
                    continue

                for feature in data.get("features", []):
                    outage = self._parse_feature(feature)
                    if outage and outage.event_id not in seen:
                        seen.add(outage.event_id)
                        outages.append(outage)

            except requests.RequestException as e:
                logger.warning("Failed to fetch %s: %s", base_url, e)

        return outages
