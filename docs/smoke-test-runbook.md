# Smoke-test runbook — AA-TripPlanner-Web

The end-to-end smoke test (tasks.md Task 8) exercises the whole loop
against real data. It cannot run until the human-gated prerequisites
below are in place. This runbook lists those prerequisites, the exact
commands, and the manual click-path to verify.

## Status of automated verification (done, offline)
- Backend: **48 unit tests pass** (bedrock 6, extraction 12, browse 12,
  assembly 18) — `pytest`, no DB/Mapbox/Bedrock needed.
- Frontend: **`next build` passes** (types + compile), both BFF routes
  present.

## Prerequisites (human-gated — must be done before the live smoke test)

| # | Prerequisite | Owner | Notes |
|---|---|---|---|
| P1 | DB tunnel to shared RDS (acc2, us-west-1) | Nghiep | set `TRIPPLANNER_DATABASE_URL` in `backend/.env` |
| P2 | Apply `002_shared_destinations.sql` then `001_tripplanner_schema.sql` | Nghiep | 002 first (FK). Coordinate `shared` numbering with AA-CIS-App |
| P3 | Mapbox tokens | Nghiep | `MAPBOX_GEOCODING_TOKEN` (backend), `NEXT_PUBLIC_MAPBOX_TOKEN` (frontend) — see docs/mapbox-setup.md |
| P4 | Bedrock trust: Lambda roles as trusted principals on acc3/acc1 roles | Nghiep (Terraform, AA-CIS-Infra) | set `BEDROCK_ROLE_NAME` in env |
| P5 | Run extraction against the 31 active tours | Nghiep + agent | writes real components; spends geocode + Bedrock quota |

## Step 1 — apply migrations (P2)
```bash
psql "$TRIPPLANNER_DATABASE_URL" -f migrations/002_shared_destinations.sql
psql "$TRIPPLANNER_DATABASE_URL" -f migrations/001_tripplanner_schema.sql
```
Confirm: `\dt tripplanner.*` shows 5 tables; `\d shared.destinations`
exists with the unique lower(name) index.

## Step 2 — run extraction (P5)
```bash
cd backend && ../.venv/bin/python -m extraction.run   # or python -m backend.extraction.run from repo root
```
Confirm: prints "Extraction complete: N components from M groups"; spot-
check that every `source_tour_id` matches an active `published_tours`
row, and that `activity` values are grounded (verify.py already enforces
this at write time).

## Step 3 — run the two Lambdas locally (or deploy)
Point the frontend BFF at whatever host serves them:
`BROWSE_API_URL`, `TRIP_API_URL` in `frontend/.env.local`.

## Step 4 — run the frontend
```bash
npm --prefix frontend run dev
```

## Step 5 — manual click-path (the actual smoke test)
1. Load the app → map shows destination pins (clustered where dense),
   no default country tab.
2. Pan/zoom → pins refresh for the visible 1° tiles.
3. Toggle an activity chip → pins narrow (indexed filter before search).
4. Type free text → results re-rank by semantic similarity within the
   filtered set (debounced).
5. Click a pin with 2+ components → popup lists each with Add/Remove.
6. Add one component → trip panel updates immediately; pin/popup shows
   "In your trip — Day N".
7. Add a second component from a different destination → after the
   debounce, compose narration streams in; the day order is the
   deterministic geographic sequence.
8. Drag-reorder a day → renarrate runs (order preserved, components
   unchanged; no re-selection).
9. Click Send to advisor → (flag on) registration form appears; submit
   name + phone/email → confirm notification fires to
   `ADVISOR_NOTIFY_EMAIL`.
10. Edit the trip again after sending → still editable; re-sending sends
    a new notification (no auto-notify on every edit).

## Acceptance
- Every component surfaced on the map traces back to a real, active tour
  in `published_tours` (grounding).
- Guest trip survives registration (same trip, now attached to the
  customer).
- Trip > 25 days shows a non-blocking warning.

## Deploy prerequisites (separate from the smoke test — human-gated)
- Lambda packaging + Function URLs / API Gateway (Terraform)
- CloudFront in front of Lambda A's read routes only (Terraform)
- Vercel project + domain + env vars for the frontend
- Merge the 7 stacked PRs (human-only)
