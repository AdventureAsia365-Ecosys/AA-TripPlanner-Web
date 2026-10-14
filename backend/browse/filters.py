"""Shared filter parsing + SQL WHERE building for the browse Lambda.

Closed-enum filters (activity, intensity_level, country, season) are
applied as indexed WHERE clauses BEFORE any semantic search runs. Values
are validated against the taxonomy so nothing user-supplied reaches SQL
unchecked; all values are passed as bound parameters (never interpolated).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from backend.shared.schemas import Activity, Intensity


# AA-712: the country filter without a join (see BrowseFilters.component_where_sql).
COMPONENT_COUNTRY_CLAUSE = ("AND c.destination_id = ANY(ARRAY(SELECT dc.id FROM shared.destinations dc "
                            "WHERE dc.country = ${i}))")


@dataclass
class BrowseFilters:
    activity: Optional[str] = None
    intensity_level: Optional[str] = None
    country: Optional[str] = None
    season: Optional[int] = None  # a single month 1..12

    @classmethod
    def from_query(cls, q: dict[str, Any]) -> "BrowseFilters":
        def _one(key: str) -> Optional[str]:
            v = q.get(key)
            if isinstance(v, list):
                v = v[0] if v else None
            if v is None or v == "":
                return None
            return str(v)

        activity = _one("activity")
        if activity is not None and activity not in {a.value for a in Activity}:
            activity = None  # ignore unknown enum value rather than error
        intensity = _one("intensity_level")
        if intensity is not None and intensity not in {i.value for i in Intensity}:
            intensity = None
        season_raw = _one("season")
        season: Optional[int] = None
        if season_raw is not None:
            try:
                m = int(season_raw)
                if 1 <= m <= 12:
                    season = m
            except ValueError:
                season = None
        return cls(
            activity=activity,
            intensity_level=intensity,
            country=_one("country"),
            season=season,
        )

    def where_sql(self, start_index: int = 1,
                  country_clause: str = "AND d.country = ${i}") -> tuple[str, list[Any]]:
        """Return (sql_fragment, params). sql_fragment begins with 'AND'
        for each active filter, using $N placeholders starting at
        start_index. `country` filters via the joined destinations table (``d``) unless
        country_clause says otherwise."""
        clauses: list[str] = []
        params: list[Any] = []
        idx = start_index

        if self.activity is not None:
            clauses.append(f"AND c.activity = ${idx}")
            params.append(self.activity)
            idx += 1
        if self.intensity_level is not None:
            clauses.append(f"AND c.intensity_level = ${idx}")
            params.append(self.intensity_level)
            idx += 1
        if self.country is not None:
            clauses.append(country_clause.replace("{i}", str(idx)))
            params.append(self.country)
            idx += 1
        if self.season is not None:
            clauses.append(f"AND ${idx} = ANY(c.season_months)")
            params.append(self.season)
            idx += 1

        return (" ".join(clauses), params)

    def component_where_sql(self, start_index: int = 1) -> tuple[str, list[Any]]:
        """Like where_sql, but every clause reads only itinerary_components (alias ``c``): the
        country becomes ``c.destination_id = ANY(ARRAY(<destinations of that country>))`` — an
        InitPlan array, not a join — so the clauses can sit inside an index-ordered vector scan
        (AA-712). Same rows as where_sql over the destinations join."""
        return self.where_sql(start_index, country_clause=COMPONENT_COUNTRY_CLAUSE)
