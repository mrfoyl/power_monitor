"""
Arva (formerly Tromskraft) outage collector — covers Tromsø and Troms county.

Endpoints discovered 2026-04-27 from the ArcGIS webmap item:
https://www.arcgis.com/sharing/rest/content/items/062737b4b0534c7ca50f1e3128c60953/data?f=json
"""

from .arcgis import ArcGISCollector


class ArvaCollector(ArcGISCollector):
    name = "Arva (Tromskraft)"
    region = "Tromsø / Troms"
    query_urls = [
        # Layer 0: active outage points/areas
        "https://arcgis.tromskraft.no/arcgis/rest/services/ADMS_Ekstern/StromstansPublic/FeatureServer/0",
        # Layer 1: affected areas (polygon)
        "https://arcgis.tromskraft.no/arcgis/rest/services/ADMS_Ekstern/StromstansPublic/FeatureServer/1",
    ]
