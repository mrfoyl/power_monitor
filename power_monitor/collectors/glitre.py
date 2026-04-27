"""
Glitre Nett outage collector — covers Numedal, Drammen, Kongsberg, Lier area.

Endpoints confirmed working 2026-04-27 via browser DevTools inspection of
https://stromstans.glitrenett.no/?app=glitrenettavbruddsinformasjon
(config at /config/webmap.js revealed the ArcGIS service URLs).
"""

from .arcgis import ArcGISCollector


class GlitreCollector(ArcGISCollector):
    name = "Glitre Nett"
    region = "Numedal / Drammen / Kongsberg"
    query_urls = [
        "https://gis-pub.glitrenett.no/pubserver/rest/services/Public/SpontanousOutagesPublic_South/MapServer/1",
        "https://gis-pub.glitrenett.no/pubserver/rest/services/Public/SpontanousOutagesPublic_East/MapServer/1",
    ]
