"""Which store the place / tour-graph readers read (AA-712 slice 6b, ADR 0007).

``legacy`` (default): the TripPlanner tables frozen since slice 6a (``tripplanner.itinerary_components``,
``tour_stop``, ``tour_leg``, ``tour_junction``) and ``shared.destinations``. Their tour key
``source_tour_id`` is a published tour_id for some tours and a catalog source_id for others.

``cis``: the CIS catalog views of migration 228 (contract ``AA-Ecosys/docs/contracts/catalog.graph.v1.json``):
``catalog.v_tour_component`` (id <- component_id, source_tour_id <- source_id, source_day_index <- day_index),
``v_tour_stop``, ``v_tour_leg``, ``v_tour_junction`` and ``v_destination``. Keyed by source_id only. The views
join ``catalog.v_tour`` (live tours only) and read with their owner's rights, so these reads need neither the
active-tour EXISTS nor the AA tenant (``set_catalog_tenant``).

The flag is read at call time from ``config.PLACES_SOURCE`` (Lambda env, fixed per container), so tests can
switch it with monkeypatch.
"""
from __future__ import annotations

import logging

from backend import config

LEGACY = "legacy"
CIS = "cis"
VALUES = (LEGACY, CIS)

log = logging.getLogger(__name__)
_warned: set[str] = set()


def current() -> str:
    """``legacy`` or ``cis``. An unknown value reads as ``legacy`` (logged once)."""
    raw = (config.PLACES_SOURCE or LEGACY).strip().lower()
    if raw in VALUES:
        return raw
    if raw not in _warned:
        _warned.add(raw)
        log.warning("PLACES_SOURCE=%r is not one of %s; reading legacy", raw, VALUES)
    return LEGACY


def is_cis() -> bool:
    return current() == CIS


def pick(legacy_sql: str, cis_sql: str) -> str:
    """The SQL for the current source."""
    return cis_sql if is_cis() else legacy_sql
