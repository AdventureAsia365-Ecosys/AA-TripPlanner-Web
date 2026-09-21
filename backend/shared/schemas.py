"""Pydantic models shared across the backend.

These enums are the closed taxonomy confirmed in design.md. They are the
enforcement point for grounding: the compose/extraction LLM output is
validated against these models, so the LLM can only *select* from known
values, never invent them.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Activity(str, Enum):
    trekking = "trekking"
    cultural_heritage = "cultural_heritage"
    wildlife_nature = "wildlife_nature"
    water_activities = "water_activities"
    culinary = "culinary"
    wellness_relaxation = "wellness_relaxation"
    adventure_sport = "adventure_sport"
    local_immersion = "local_immersion"


class Intensity(str, Enum):
    leisurely = "leisurely"
    moderate = "moderate"
    active = "active"
    strenuous = "strenuous"


class DurationHint(str, Enum):
    half_day = "half_day"
    full_day = "full_day"
    overnight = "overnight"
    multi_night = "multi_night"


class ExtractedComponent(BaseModel):
    """One itinerary_component as proposed by the extraction LLM.

    `name` must be a real place; `activity` is verified against the source
    atom text downstream (verify.py) before persistence.
    """

    name: str = Field(min_length=1)
    activity: Activity
    intensity_level: Intensity
    duration_hint: DurationHint
    season_months: list[int]
    text_extract: str = Field(min_length=1)

    @field_validator("season_months")
    @classmethod
    def _months_in_range(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("season_months must not be empty")
        for m in v:
            if m < 1 or m > 12:
                raise ValueError(f"season_months out of range: {m}")
        return sorted(set(v))


class ComponentPublic(BaseModel):
    """Component shape returned to the frontend by Lambda A."""

    id: str
    name: str
    activity: str
    intensity_level: str
    duration_hint: str
    text_extract: str
    thumbnail_url: Optional[str] = None
    in_trip_day: Optional[int] = None


class DestinationPin(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    component_count: int
