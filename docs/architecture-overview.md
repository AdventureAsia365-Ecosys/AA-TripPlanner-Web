# AA-TripPlanner-AI — Architecture Overview

_Last updated: 2026-09-13. Covers the deployed dev system after the
MVP-completion cycle (UI redesign, embedding backfill, narration route,
edge shared-secret, semantic search wiring)._

AA-TripPlanner is a standalone, AA-branded B2C map-first trip planner. A
visitor browses destinations Adventure Asia already runs tours in, pins
the specific place+activity components that interest them, watches a
day-by-day itinerary assemble live, and hands the draft to an AA advisor.

This document is the single map of **what runs where**, and — importantly —
**which pieces are new/owned by TripPlanner vs. shared with (or reused
from) AA-CIS**.

---

## 1. Top-level shape

```
                 Browser (Next.js app, Vercel)
                        │  (same-origin /api/* only)
                        ▼
        Next.js BFF routes  (server-side, on Vercel)
          /api/browse  ───────────┐        /api/trip
                        │          │            │
     adds X-TripPlanner-Key header │  (secret, server-side only)
                        ▼          ▼            ▼
        API Gateway (HTTP API v2, authorization = NONE)
        ANY /browse/{proxy+}          ANY /trip/{proxy+}
                        │                        │
                        ▼                        ▼
        Lambda A: Browse            Lambda B: Assembly
        (stateless, read,           (stateful event log,
         no LLM)                     Bedrock narration)
                        │                        │
                        └──────────┬─────────────┘
                                   ▼
              Shared RDS PostgreSQL (acc2, us-west-1)
              schemas: tripplanner.*  +  shared.destinations
                                   │
                    ┌──────────────┴───────────────┐
                    ▼                               ▼
        Bedrock Cohere Embed v4          Bedrock Claude (satellite:
        (direct, acc2)                    assume acc3 / acc1 role)
```

Three deployable units + one offline pipeline:
1. **Frontend** — Next.js app on Vercel (own project, own domain).
2. **Lambda A (Browse)** — stateless, cacheable reads.
3. **Lambda B (Assembly)** — stateful per-visitor + LLM narration.
4. **Extraction pipeline** — offline, run manually (NOT a service).

The two-Lambda split is a hard architectural boundary (Browse is
read-heavy/cacheable/no-LLM; Assembly is per-visitor stateful + calls
Bedrock and must never be cached across visitors).

---

## 2. Frontend (FE) — built by this project

Next.js (App Router) + TypeScript + Tailwind, deployed to Vercel.

| Area | File(s) | Notes |
|------|---------|-------|
| Brand theme | `app/globals.css`, `tailwind.config.ts`, `app/layout.tsx` | AA palette (gold `#DB9628`, ink `#1F2933`, off-white), Inter font. TripAdvisor-style light theme. |
| Shell | `components/Header.tsx`, `app/page.tsx` | Brand bar + map + 25rem trip rail. |
| Map | `components/MapView.tsx` | Mapbox GL, 1° tile pins, clustering (gold clusters / ink pins). Renders semantic-search results when active, else tiles-by-bounds. |
| Discover bar | `components/FilterChips.tsx` | Debounced semantic search box + activity chips + intensity/season/country filters. |
| Destination detail | `components/DestinationPopup.tsx` | Lists a destination's components, each with its own Add. |
| Trip rail | `components/TripPanel.tsx` | Live itinerary, drag-reorder, AI narration (Compose/Regenerate), Send-to-advisor + registration. |
| State | `lib/useTrip.tsx` | Guest session/trip ids (in-memory), filters, itinerary, narration, search state. |
| API client | `lib/api.ts` | Talks only to the BFF routes. |
| BFF (server-side) | `app/api/browse/route.ts`, `app/api/trip/route.ts` | Proxy to the Lambdas; hold `BROWSE_API_URL` / `TRIP_API_URL` + `TRIPPLANNER_API_KEY` server-side (never exposed to the browser). |

Vercel env (Production): `BROWSE_API_URL`, `TRIP_API_URL` (both = API GW
base), `TRIPPLANNER_API_KEY` (edge shared secret), `NEXT_PUBLIC_MAPBOX_TOKEN`.

---

## 3. Backend (BE) — built by this project

Python 3.12. All handlers keep a framework-agnostic `route()` that is
unit-tested directly; a thin `handler()` adapts the API Gateway event.

### Lambda A — Browse (`backend/browse/`)
- `tiles.py` — fixed 1° grid tile query (filters: activity, intensity,
  country, season).
- `search.py` — pgvector cosine search **within** the filtered set;
  embeds the query with Cohere Embed v4; ranks destinations by best
  component distance. Requires component `embedding` populated.
- `destinations.py` — destination detail (components list).
- `handler.py` — routes GET tiles / destination / search; verifies the
  edge shared-secret first.

### Lambda B — Assembly (`backend/assembly/`)
- `events.py` — append-only `trip_events` + `trip_drafts` projection in
  one transaction; lazy guest-session create on first event;
  `current_itinerary()` read-only projection (incl `text_extract`) for
  narration.
- `sequencing.py` — deterministic nearest-neighbor day ordering (geo
  heuristic, not routing).
- `agent.py` — `compose` / `renarrate` narration via Bedrock. **Buffered
  invoke** (not streaming) — the route returns the full narration as JSON.
- `registration.py` — customer dedupe by phone/email + session claim.
- `notify.py` — advisor notification (v1 = logging stub; real email
  deferred).
- `handler.py` — routes add/remove/reorder/send-to-advisor/**narrate**;
  verifies the edge shared-secret first.

### Shared (`backend/shared/`)
- `db.py` — asyncpg pool; DSN from `TRIPPLANNER_DATABASE_URL` (local) or
  `TRIPPLANNER_DATABASE_URL_ARN` (Secrets Manager, prod).
- `bedrock_satellite.py` — **hybrid Bedrock**: `embed()` is a DIRECT acc2
  Cohere call; `invoke()` / `invoke_stream()` (Claude) assume the
  cross-account satellite role (acc3 primary → acc1 fallback).
- `auth.py` — edge shared-secret check (`X-TripPlanner-Key`). Disabled
  when the key env is empty (local/tests).

### Extraction (`backend/extraction/`) — offline, not deployed
- `run.py` — full pipeline: read active tours → group atoms → LLM
  categorize → verify → geocode → insert components (+ embedding).
- `run_deterministic.py` — fast seeder (no Bedrock); inserts with
  `embedding = NULL`.
- `geocode.py` — Mapbox forward geocoding, cached into
  `shared.destinations`.
- `country_normalize.py`, `verify.py` — dirty-country map + deterministic
  activity grounding.
- `backfill_embeddings.py` — **NEW** one-off: fills the NULL embeddings
  left by the deterministic seeder (Cohere Embed v4, idempotent).

### Bedrock model IDs (verified live)
- Embed: `us.cohere.embed-v4:0` (direct on acc2, 1536-dim).
- Compose/renarrate: `global.anthropic.claude-sonnet-4-6` (via satellite;
  the invoker roles allow the `global.` profile, NOT `us.`).

---

## 4. Data (shared RDS, acc2 us-west-1)

Same RDS instance AA-CIS uses. Two schema areas:

| Schema | Owner | Tables |
|--------|-------|--------|
| `tripplanner.*` | **TripPlanner (this project)** | `itinerary_components`, `customers`, `sessions`, `trip_events`, `trip_drafts` |
| `shared.destinations` | Deliberately shared (candidate golden record) | destinations w/ `lat`/`lng`, cached geocode |

Read-only sources (owned by AA-CIS, never written by TripPlanner):
`acp_contract.tour_atoms`, `gold_aa_internal.published_tours`.

Current data state (dev): 379 destinations, 1081 itinerary_components
(all trace to the 31 active tours). Embeddings backfilled via
`backfill_embeddings.py`.

**Coordinates caveat:** `lat/lng` are not in the source data — they are
derived by Mapbox forward geocoding (`limit=1`, name + tour country) and
cached in `shared.destinations`. A minority of same-named places geocode
to the wrong location (e.g. a "Seoraksan National Park" landing in the
USA). This affects only map-pin placement, not search ranking
(embedding-based). Known data-quality limitation, not a code defect.

---

## 5. What was added to Infra SHARED with AA-CIS

TripPlanner has **no separate Terraform root**. It rides inside the
existing AA-CIS Terraform:

- **`AA-CIS-Infra/accounts/aa365/tripplanner.tf`** — self-contained file
  in the AA-CIS aa365 root. Adds ONLY `tripplanner*` / `aa-tripplanner-dev-*`
  resources; reads existing `module.vpc.*` / `module.rds.*` outputs but
  creates NO new VPC/RDS. Contents:
  - 2 Lambdas (browse, assembly) + shared exec role, in the AA-CIS VPC.
  - API Gateway HTTP API v2 (`ANY /browse/{proxy+}`, `ANY /trip/{proxy+}`).
  - S3 artifacts bucket + OIDC deploy role (the app repo ships Lambda code
    via `lambda:UpdateFunctionCode`).
  - Secrets: `tripplanner/dev/database-url`, **`tripplanner/dev/api-key`**
    (NEW — edge shared secret, `random_password`), injected as Lambda env.
  - Direct Cohere embed IAM grant + caller-side `sts:AssumeRole` for the
    satellite invoker roles.
- **`AA-CIS-Infra/accounts/acc3-bedrock` + `accounts/acc1-bedrock`** — the
  cross-account **trust** side: the TripPlanner Lambda exec role is added
  as a trusted principal on `AA3-Bedrock-Invoker` (acc3) /
  `AA-Bedrock-Invoker` (acc1), reusing the exact satellite pattern
  AA-CIS/ACPv2 already uses. These roles allow the **`global.`** Claude
  profile.
- **`versions.tf`** — added the `hashicorp/random` provider (for the api
  key).

Shared-but-not-modified: the VPC, RDS instance, the GitHub OIDC provider,
and the AA-CIS REST API (`modules/api_gateway`, VPC-Link to ECS/FastAPI) —
that REST API is AA-CIS's, entirely separate from TripPlanner's HTTP API.

**Explicit non-reuse** (verified): TripPlanner does NOT depend on
`AA-ACP-Core`, `AA-ACP-App`, or AAA. The Bedrock satellite call is
re-implemented fresh in this repo (not imported from AA-CIS-App).

---

## 6. Auth model (MVP)

- API Gateway authorization = **NONE** at the edge.
- Both Lambdas verify an **edge shared secret** (`X-TripPlanner-Key`),
  which only the server-side BFF knows (`backend/shared/auth.py`). This is
  a coarse "is this our BFF" gate, not per-user auth (guests are anonymous
  by design). Verified live: direct call without header → 401; BFF (with
  header) → 200.
- Deferred hardening: a JWT/Lambda authorizer, CloudFront in front of the
  Browse read routes, and Mapbox token URL-restriction.

---

## 7. Deferred / not built (tracked)

- **Auto-refresh when new tours arrive**: today the extraction pipeline is
  manual. New tours in `acp_contract.tour_atoms` do NOT automatically flow
  into `tripplanner.itinerary_components` — someone must re-run extraction.
  A scheduled/triggered re-run (+ making `run.py` idempotent/upsert) is a
  separate future task.
- Real advisor email (SES + verified domain) — currently a logging stub.
- CloudFront in front of Browse read routes.
- Mapbox token URL-restriction.
- Fixing the minority of mis-geocoded destinations (source-of-truth
  cleanup).
- Rotating the exposed dev RDS admin password.
