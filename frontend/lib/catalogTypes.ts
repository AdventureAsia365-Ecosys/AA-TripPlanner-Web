// AA-812 — shapes of the browse Lambda's /browse/catalog/* answers (backend/browse/catalog.py).
// The tour detail is the CIS contract catalog.tour v1 plus display helpers (country_slug, cover_url,
// photo url / url_small).

import type { RouteProposal } from "./types";

// Where a tour's cover comes from (AA-812 D4/D5): its own photo, or — when it has none — a photo
// of its main destination / of a destination in its country, named by cover_place. A fallback is
// a destination photo and must be labelled so; it never shows the tour itself.
export type CoverSource = "tour" | "destination" | "country" | null;

export interface TourCard {
  source_id: string;
  tour_id: string;
  slug: string;
  title: string;
  subtitle: string | null;
  summary: string | null;
  country: string | null;
  country_slug: string;
  duration_days: number | null;
  duration_raw: string | null;
  best_time_to_go: string | null;
  highlights: string[];
  cover_url: string | null;
  cover_url_small: string | null;
  cover_source: CoverSource;
  cover_place: string | null;
  photo_count: number;
  lifecycle_stage: string | null;
  published_at: string | null;
  updated_at: string | null;
}

export type TourSort = "featured" | "duration_asc" | "duration_desc" | "recent" | "title";

export interface TourList {
  total: number;
  limit: number;
  offset: number;
  tours: TourCard[];
}

export interface TourDay {
  day_number: number;
  title: string;
  body: string;
  places: string[];
}

export interface TourPlace {
  day_number: number | null;
  position: number;
  place_name: string;
  place_display_name: string | null;
  destination_id: string | null;
  destination_name: string | null;
  lat: number | null;
  lng: number | null;
  activity_types: string[];
}

export interface CatalogPhoto {
  photo_id: string;
  place_label: string | null;
  width: number | null;
  height: number | null;
  url: string | null;
  url_small: string | null;
}

export interface TourDetail {
  source_id: string;
  tour_id: string;
  slug: string;
  title: string;
  subtitle: string | null;
  summary: string | null;
  description: string | null;
  highlights: string[];
  country: string | null;
  country_slug: string;
  duration_raw: string | null;
  duration_days: number | null;
  activities: string[];
  inclusions: string | null;
  exclusions: string | null;
  best_time_to_go: string | null;
  group_size: string | null;
  lifecycle_stage: string | null;
  // Whole itinerary text; the page shows it when `days` is empty (legacy, non-canonical text).
  itinerary_text: string | null;
  cover_url: string | null;
  cover_source: CoverSource;
  cover_place: string | null;
  photo_count: number;
  published_at: string | null;
  updated_at: string | null;
  days: TourDay[];
  places: TourPlace[];
  photos: (CatalogPhoto & { position: number })[];
}

export interface CountrySummary {
  country: string;
  slug: string;
  tour_count: number;
  min_days: number | null;
  max_days: number | null;
  cover_url: string | null;
  cover_url_small: string | null;
  updated_at: string | null;
}

export interface CountryPlace {
  destination_id: string;
  name: string | null;
  lat: number;
  lng: number;
  tour_count: number;
  activity_types: string[];
  cover_url: string | null;
}

export interface DestinationPage {
  country: string;
  slug: string;
  tour_count: number;
  min_days: number | null;
  max_days: number | null;
  top_activities: string[];
  cover_url: string | null;
  tours: TourCard[];
  places: CountryPlace[];
  photos: (CatalogPhoto & { destination_id: string })[];
}

export interface SitemapData {
  tours: { slug: string; updated_at: string | null }[];
  countries: { slug: string; country: string; updated_at: string | null }[];
}

export interface TourRoute {
  tour_id: string;
  proposal: RouteProposal;
}
