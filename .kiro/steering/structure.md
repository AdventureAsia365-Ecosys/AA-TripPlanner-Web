# Structure: AA_TripPlanner_AI

Single repo, three deployable units: a Next.js frontend and two independent
Lambda functions, plus an offline extraction pipeline that is not deployed
as a service at all (run manually / on a schedule, not request-driven).

```
AA-TripPlanner-Web/
├── frontend/                       # Next.js app (Vercel)
│   ├── app/
│   │   ├── page.tsx                 # map + filter chips + trip panel
│   │   └── api/                      # BFF routes (server-side only,
│   │                                 #   Lambda URLs never exposed to
│   │                                 #   the browser)
│   │       ├── browse/route.ts       # proxies to Lambda A
│   │       └── trip/route.ts         # proxies to Lambda B
│   ├── components/
│   │   ├── MapView.tsx                # Mapbox, tiles, clustering
│   │   ├── FilterChips.tsx            # activity/intensity/season/country
│   │   ├── DestinationPopup.tsx       # hover/click detail, list of
│   │   │                               #   components, Add buttons
│   │   └── TripPanel.tsx              # live itinerary list, drag-reorder,
│   │                                   #   Send to advisor
│   └── lib/mapbox.ts
├── backend/
│   ├── browse/                        # Lambda A — Map/Browse
│   │   ├── handler.py                  # routes: GET tiles, GET
│   │   │                               #   destination detail, GET search
│   │   ├── tiles.py                     # fixed-grid tile query (1°
│   │   │                                #   lat/lng cells)
│   │   └── search.py                    # pgvector semantic search within
│   │                                     #   pre-filtered set
│   ├── assembly/                       # Lambda B — Trip Assembly
│   │   ├── handler.py                   # routes: add/remove component,
│   │   │                                #   reorder, send-to-advisor
│   │   ├── events.py                     # append-only trip_events log
│   │   ├── sequencing.py                  # deterministic day-grouping
│   │   │                                  #   algorithm (geography-based)
│   │   ├── agent.py                        # LLM steps: compose, renarrate
│   │   ├── registration.py                  # Customer dedupe + session
│   │   │                                    #   claim on registration
│   │   └── notify.py                         # advisor notification
│   │                                          #   (email v1)
│   ├── shared/
│   │   ├── bedrock_satellite.py          # STS assume-role + invoke,
│   │   │                                  #   acc3 only (reimplemented,
│   │   │                                  #   not imported — see tech.md)
│   │   └── db.py                          # asyncpg pool
│   └── extraction/                      # offline pipeline, NOT a Lambda,
│       │                                # run manually or on a schedule
│       ├── run.py                        # entrypoint: reads tour_atoms,
│       │                                 #   writes itinerary_components
│       │                                 #   + shared.destinations
│       ├── country_normalize.py           # hard-coded country value
│       │                                  #   mapping (see design.md)
│       ├── geocode.py                      # Mapbox Geocoding, cached into
│       │                                   #   shared.destinations
│       └── verify.py                        # deterministic keyword/
│                                             #   synonym check
├── migrations/
│   ├── 001_tripplanner_schema.sql
│   └── 002_shared_destinations.sql        # ALTER/CREATE on the EXISTING
│                                            #   shared schema — coordinate
│                                            #   with AA-CIS-App migrations,
│                                            #   do not collide with their
│                                            #   numbering
└── .kiro/                                 # this spec
```

## Naming convention
- `tripplanner.*` — everything owned exclusively by this service
  (itinerary_components, customers, sessions, trip_events, trip_drafts).
  Never write into `gold_aa_internal`, `acp_contract`, or any other
  AA-CIS-App-owned schema.
- `shared.destinations` — the one deliberate exception. It lives in the
  existing `shared` schema because it is meant to be a candidate
  cross-program golden record (see PRD §6.1), not a TripPlanner-private
  table.
