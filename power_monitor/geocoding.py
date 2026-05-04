"""
Address, postal code, and GPS lookup via Kartverket's free address APIs.

Docs:
  Address/postnr search: https://ws.geonorge.no/adresser/v1/
  Reverse geocoding:     https://ws.geonorge.no/adresser/v1/punktsok

No API key required. Be conservative — one request per user query only.
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://ws.geonorge.no/adresser/v1/sok"
_PUNKTSOK_URL = "https://ws.geonorge.no/adresser/v1/punktsok"
_USER_AGENT = (
    "PowerMonitor/1.0 (Norwegian outage correlation tool; "
    "https://github.com/IFKIKT/power_monitor)"
)
_TIMEOUT = 10


def _get(params: dict) -> dict:
    resp = requests.get(
        _BASE_URL,
        params=params,
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json()


# Municipality number prefix → county name (2024 Norwegian county structure)
_COUNTY_BY_PREFIX = {
    "03": "Oslo",
    "11": "Rogaland",
    "15": "Møre og Romsdal",
    "18": "Nordland",
    "31": "Østfold",
    "32": "Akershus",
    "33": "Buskerud",
    "34": "Innlandet",
    "39": "Vestfold",
    "40": "Telemark",
    "42": "Agder",
    "46": "Vestland",
    "50": "Trøndelag",
    "55": "Troms",
    "56": "Finnmark",
}


def _county_from_municipality_no(knr: str) -> str:
    """Derive county name from the first two digits of the municipality number."""
    return _COUNTY_BY_PREFIX.get(knr[:2], "")


def _extract_hit(hit: dict) -> dict:
    knr = hit.get("kommunenummer", "").strip()
    return {
        "municipality": hit.get("kommunenavn", "").strip(),
        "municipality_no": knr,
        "county": _county_from_municipality_no(knr),
        "full_address": (
            f"{hit.get('adressetekst', '')} {hit.get('postnummer', '')} "
            f"{hit.get('poststed', '')}"
        ).strip(),
        "postnummer": hit.get("postnummer", ""),
        "poststed": hit.get("poststed", ""),
    }


def lookup_postnummer(postnr: str) -> Optional[dict]:
    """
    Return location info for a Norwegian 4-digit postal code.
    Returns None if not found.
    """
    try:
        data = _get({"postnummer": postnr.strip(), "treffPerSide": 1})
        hits = data.get("adresser", [])
        if not hits:
            return None
        return _extract_hit(hits[0])
    except requests.RequestException as e:
        logger.error("Kartverket lookup failed for postnr %s: %s", postnr, e)
        return None


def lookup_gps(lat: float, lon: float, radius: int = 500) -> Optional[dict]:
    """
    Reverse geocode a GPS coordinate to location info using Kartverket's
    punktsok (point search) API.  Returns the nearest address within `radius`
    metres, or None if nothing is found.

    The result dict has the same keys as lookup_postnummer / lookup_address.
    """
    try:
        resp = requests.get(
            _PUNKTSOK_URL,
            params={
                "lat": lat,
                "lon": lon,
                "radius": radius,
                "treffPerSide": 1,
                "utkoordsys": 4258,  # WGS84
            },
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        hits = resp.json().get("adresser", [])
        if not hits:
            # Widen search radius once before giving up
            if radius < 2000:
                return lookup_gps(lat, lon, radius=2000)
            return None
        return _extract_hit(hits[0])
    except requests.RequestException as e:
        logger.error("Kartverket punktsok failed for %.5f,%.5f: %s", lat, lon, e)
        return None


def lookup_address(address: str) -> Optional[dict]:
    """
    Return location info for a free-text Norwegian address string.
    Returns None if not found.
    """
    try:
        data = _get({"sok": address.strip(), "treffPerSide": 1})
        hits = data.get("adresser", [])
        if not hits:
            return None
        return _extract_hit(hits[0])
    except requests.RequestException as e:
        logger.error("Kartverket lookup failed for address %r: %s", address, e)
        return None
