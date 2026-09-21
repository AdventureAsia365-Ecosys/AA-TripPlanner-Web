"""Hard-coded country normalization applied during extraction.

Local workaround for known dirty values in raw_tours.country. The
source-of-truth fix is tracked in Linear AA-571.
"""
from __future__ import annotations

COUNTRY_MAP = {
    "SRI-LANDKA": "Sri Lanka",
    "OKINAWA": "Japan",
}


def normalize_country(raw: str) -> str:
    """Return the normalized country name for a raw source value."""
    if raw is None:
        return raw
    key = raw.strip()
    return COUNTRY_MAP.get(key, COUNTRY_MAP.get(key.upper(), key))
