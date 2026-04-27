"""
Elvia outage collector — covers Innlandet, Oslo, Akershus and Østfold.

Endpoint discovered 2026-04-27 via Chrome DevTools → Network → Fetch/XHR on
https://strombruddskart.elvia.no/

Elvia hosts their outage data as a public ArcGIS Online FeatureServer
(services-eu1.arcgis.com). Field schema differs from Glitre/Tromskraft
(which use on-premises ADMS servers), so this collector overrides _parse_feature.

Layers:
  /0 — Point features (one point per outage event)
  /1 — Polygon features (affected area per outage event)
We query layer 0; layer 1 can be added later for spatial lookups.

Field mapping:
  OBJECTID           → event_id
  antallkunder       → num_affected
  avbruddstype       → outage_type  (e.g. "Unplanned", "Planned")
  kommune            → municipality
  nettstasjon        → grid station / substation ID
  poststed           → place name within municipality
  strombruddoppdaget → timestamp outage was detected (null for planned)
  utkoblingstart     → scheduled/actual disconnection start
  utkoblingslutt     → scheduled/actual restoration end
"""

from datetime import datetime, timezone
from typing import List, Optional

from .arcgis import ArcGISCollector
from ..models import PowerOutage

_BASE = (
    "https://services-eu1.arcgis.com"
    "/AcdYbPzrkOfBOQDL/arcgis/rest/services"
    "/avbrudd2_offentlig_visning/FeatureServer"
)

_OUTFIELDS = ",".join([
    "OBJECTID",
    "antallkunder",
    "avbruddstype",
    "kommune",
    "nettstasjon",
    "poststed",
    "strombruddoppdaget",
    "utkoblingslutt",
    "utkoblingstart",
])


class ElviaCollector(ArcGISCollector):
    name = "Elvia"
    region = "Innlandet / Oslo / Akershus / Østfold"
    query_urls = [f"{_BASE}/0"]

    def _query_url(self, base_url: str) -> str:
        return (
            f"{base_url}/query"
            f"?f=json"
            f"&where=1%3D1"
            f"&outFields={_OUTFIELDS}"
            f"&returnGeometry=false"
            f"&cacheHint=true"
        )

    def _parse_timestamp(self, ms: Optional[int]) -> Optional[datetime]:
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    def _parse_feature(self, feature: dict) -> Optional[PowerOutage]:
        attrs = feature.get("attributes", {})
        event_id = attrs.get("OBJECTID")
        if event_id is None:
            return None

        # Use detected time if available, otherwise scheduled start
        start_ms = attrs.get("strombruddoppdaget") or attrs.get("utkoblingstart")
        end_ms = attrs.get("utkoblingslutt")

        outage_type = (attrs.get("avbruddstype") or "").strip()
        # Normalise to Norwegian terms to match the rest of the codebase display
        if outage_type.lower() == "unplanned":
            outage_type = "Driftsforstyrrelse"
        elif outage_type.lower() == "planned":
            outage_type = "Utkobling"

        municipality = (attrs.get("kommune") or "").strip()
        poststed = (attrs.get("poststed") or "").strip()

        return PowerOutage(
            provider=self.name,
            event_id=str(event_id),
            status="Pågående",
            outage_type=outage_type,
            municipality=municipality,
            start_time=self._parse_timestamp(start_ms),
            num_affected=attrs.get("antallkunder") or 0,
            customer_message=poststed or None,
            estimated_restoration=self._parse_timestamp(end_ms),
            grid_level=attrs.get("nettstasjon") or None,
        )
