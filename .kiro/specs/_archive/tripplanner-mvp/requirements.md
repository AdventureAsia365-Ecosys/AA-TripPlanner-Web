# Requirements: AA_TripPlanner_AI (map-first, v0.3)

## 1. Data foundation (extraction pipeline)
User story: As the system, I need real, categorized, geocoded
itinerary_components before the map can show anything meaningful.

- WHEN the extraction pipeline runs, THEN it SHALL read only tours where
  `gold_aa_internal.published_tours.master_status = 'active'`.
- WHEN reading atoms for an active tour, THEN the system SHALL group them
  by `(tour_id, itinerary_day)` from `acp_contract.tour_atoms` (filtering
  `deleted = false`).
- WHEN an atom group represents a single distinguishable place, THEN the
  system SHALL produce exactly one `itinerary_component` for that
  `(tour_id, itinerary_day)`.
- WHEN an atom group shows multiple genuinely distinct destinations
  within the same day (e.g. a travel day), THEN the system SHALL produce
  multiple `itinerary_component` rows sharing that `source_day_index` —
  the system SHALL NOT force exactly one component per day.
- WHEN a group has no atom with a clear, geocodable place name, THEN the
  system SHALL assign it to the tour's already-identified primary
  destination from other atoms in the same tour, rather than leaving
  `destination_id` null.
- WHEN a `(tour_id, itinerary_day)` value has zero atoms, THEN the system
  SHALL produce no component for that day (no fallback re-parse of
  `aa_itineraries`).
- WHEN the LLM assigns an `activity` value to a component, THEN a
  deterministic check SHALL confirm that value (or a close synonym)
  appears in the source atom text before the component is persisted. IF
  the check fails, THEN the system SHALL retry composition once, and IF
  it fails again, SHALL discard that component rather than persist an
  unverified one.
- WHEN a destination name is encountered that is not already in
  `shared.destinations`, THEN the system SHALL geocode it once via the
  Mapbox Geocoding API and persist the result — the same name SHALL
  never be geocoded twice.
- WHEN populating `shared.destinations.country`, THEN the system SHALL
  apply the hard-coded normalization map (`SRI-LANDKA`→`Sri Lanka`,
  `OKINAWA`→`Japan`) before persisting.
- The pipeline SHALL run offline/batch only — nothing in the runtime path
  (Map/Browse or Trip Assembly) SHALL call the extraction pipeline
  directly.

## 2. Map & Browse
User story: As a visitor, I want to see AA's destinations on a map and
narrow them down, so I can find places worth pinning.

- WHEN a visitor loads the app, THEN the system SHALL display all
  destination pins that have at least one component, with no default
  country/region tab.
- WHEN a visitor pans or zooms the map, THEN the frontend SHALL request
  only the fixed 1°-latitude/longitude tiles the current view covers
  (never a raw bounding box).
- WHEN a visitor toggles a closed-enum filter (activity, intensity,
  season, or country), THEN the tile query SHALL narrow results via an
  indexed WHERE clause before any semantic search runs.
- WHEN a visitor types free text into search, THEN the system SHALL run
  a pgvector semantic search ONLY within the set already narrowed by
  active filters, debounced 300-500ms after the visitor stops typing.
- WHEN multiple destination pins fall close together at the current zoom
  level, THEN the frontend SHALL cluster them using Mapbox GL's built-in
  clustering.
- WHEN a visitor hovers or taps a destination pin, THEN after a ~200ms
  debounce the system SHALL show a popup listing every
  `itinerary_component` at that destination (name, activity, duration,
  a short description, and a thumbnail), each with its own Add/Remove
  control.
- IF a component in that popup is already part of the visitor's current
  trip, THEN the popup SHALL show which day it's assigned to.
- The tile-query and destination-detail endpoints SHALL be servable from
  a CDN cache (fixed 30-minute TTL, no active invalidation); the
  free-text search endpoint SHALL NOT be cached.

## 3. Trip Assembly
User story: As a visitor, I want my trip to build itself live as I pin
things, and to be able to fix the order myself.

- WHEN a visitor clicks Add on a component, THEN the system SHALL append
  an `add_component` event to that trip's event log and update the
  visible trip panel and map pin state immediately — with no separate
  "Combine" action required.
- WHEN a visitor clicks Remove on a component already in their trip,
  THEN the system SHALL append a `remove_component` event and update the
  panel immediately.
- WHEN a trip's component set changes for the first time (first
  component added), THEN the system SHALL run a deterministic
  geography-based sequencing step to assign day order, THEN (after a
  2-3 second debounce with no further Add/Remove activity) call the
  `compose` LLM step to write connective narration for that order.
- WHEN a visitor manually drags a component to a different position,
  THEN the system SHALL append a `reorder` event, immediately recompute
  day groupings deterministically (no LLM call), and (after the same
  debounce) call the `renarrate` LLM step — which SHALL NOT re-select or
  reorder components, only write narration for the fixed order given.
- WHEN multiple Add/Remove actions occur within the debounce window,
  THEN the system SHALL call the LLM step at most once for that burst of
  activity.
- The `renarrate`/`compose` LLM response SHALL stream to the frontend via
  SSE rather than waiting for the full response.
- WHEN a visitor's trip exceeds ~25 days, THEN the system SHALL show a
  non-blocking warning — it SHALL NOT prevent further additions.
- The system SHALL maintain the trip's current state as a queryable
  projection (`trip_drafts`), kept in sync with the event log — the event
  log SHALL remain the canonical source of history; the projection SHALL
  be treated as a read cache, not a second source of truth.

## 4. Dates & seasonal filtering
User story: As a visitor, I want the map to only show me what's actually
usable for my travel dates.

- WHEN a visitor sets travel dates, THEN the system SHALL filter
  available components to those whose `season_months` includes the
  relevant month(s) — dates SHALL be settable before or during pinning,
  and SHALL support both a specific calendar range and a relative day
  count.
- IF a visitor changes dates after already adding a component that is no
  longer seasonally suitable, THEN the system SHALL flag that component
  in the trip panel rather than silently leaving it unmarked.

## 5. Guest and registered customers
User story: As a visitor, I want to try the planner without an account,
but not lose my work if I decide to register.

- WHEN a visitor starts a session, THEN the system SHALL NOT require
  login, and SHALL allow full browsing, pinning, and live trip assembly
  without an account.
- IF a visitor closes the app without registering, THEN their session
  and trip SHALL NOT be recoverable (no persistence for guests).
- WHEN a visitor registers (name, phone, email) — triggered only at
  "Send to advisor" when `require_registration_before_handoff` is on —
  THEN the system SHALL check for an existing `Customer` record matching
  that phone or email BEFORE creating a new one, and SHALL reuse the
  existing `customer_id` if found.
- WHEN registration completes, THEN the system SHALL attach the
  resulting `customer_id` to the visitor's EXISTING session and trip —
  the trip built under the prior guest session SHALL NOT be lost or
  replaced with a new empty trip.
- WHEN a registered customer returns in a later session (recognized by
  their account, mechanism TBD in design.md), THEN their previously
  saved components and trips SHALL be available.

## 6. Send to advisor
User story: As a visitor with a trip I like, I want to hand it to a real
person who can make it bookable.

- WHEN a visitor clicks "Send to advisor", THEN the system SHALL check
  the `require_registration_before_handoff` flag.
- IF the flag is on AND the visitor is not yet registered, THEN the
  system SHALL present a registration form (name, phone, email) and
  SHALL block sending until it is completed.
- WHEN a trip is sent, THEN the system SHALL set that trip's status to
  `sent`, capture the full event log up to that point, and trigger a
  notification to the advisor (for this build: a fixed single email
  address, configurable, not hardcoded in application logic beyond a
  single config value).
- WHEN a registered customer edits a trip after it has already been
  sent once, THEN the system SHALL allow the edit (the trip is NOT
  locked) — a subsequent notification to the advisor SHALL only be sent
  if the customer clicks "Send to advisor" again, not automatically on
  every edit.
- Guest-sent trips (only possible when the registration flag is off,
  e.g. internal demo) SHALL be treated as a one-time final submission —
  no edit-after-send flow is required for this case.

## 7. Explicitly out of scope for this build
- Starting a trip from an uploaded photo or pasted link.
- Real-time multi-person collaborative planning.
- Real routing/travel-time between components (geographic heuristics
  only).
- Any language other than English.
- A fallback extraction path for tour-days with zero atoms.
- Automated checking of whether a component's source tour is still
  active at any point other than what Map/Browse already filters for at
  query time (no additional staleness-check step at send-to-advisor —
  the advisor's manual logistics review covers this).
