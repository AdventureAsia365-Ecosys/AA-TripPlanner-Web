# AA-TripPlanner-Web

Standalone, AA-branded B2C web app: browse Adventure Asia destinations on a map, pin the places and
activities that interest you, and watch a day-by-day itinerary assemble from real AA tours. Every
itinerary component resolves to a real AA published tour — nothing is invented. When ready, the
draft is sent to an AA advisor ("Send to advisor"); booking itself belongs to the future
AA-Booking app.

- **Domain and architecture:** [`CONTEXT.md`](CONTEXT.md) — read before designing a change.
- **Ecosystem:** `AA-Ecosys/docs/ecosystem-architecture.md`, ADRs in `AA-Ecosys/docs/adr/`
  (ADR 0002: only CIS writes content), schema owners in `AA-Ecosys/docs/database/schema-ownership.md`.
- Spec: `.kiro/specs/tripplanner-mvp/` · Implementation notes: `docs/implementation-notes/`.

## Architecture

```
Browser → Next.js (Vercel) → BFF /api/* (holds TRIPPLANNER_API_KEY)
        → API Gateway HTTP v2 (X-TripPlanner-Key)
            ├─ Lambda A  browse    stateless reads: tiles, destinations, search (one embed call), routes
            └─ Lambda B  assembly  trip event log, sequencing, narration (Claude), send-to-advisor,
                                   offline extraction ops (direct `lambda invoke` only)
        → RDS Postgres on acc2, shared with AA-CIS-App:
            tripplanner.*  (owned here)   ·   reads CIS: published_tours, v_active_tour_atoms,
            shared.destinations (read-only), photo views   ·   writes shared.llm_call_log
        → Bedrock through the ecosystem Model Gateway (stages tp_compose, tp_search_embed,
          tp_extract, tp_component_embed)
```

| Path | What |
|---|---|
| `frontend/` | Next.js (App Router, TypeScript, Tailwind) + Mapbox GL JS |
| `backend/browse/` | Lambda A handler and routes |
| `backend/assembly/` | Lambda B: events, sequencing, narration + grounding, suggestions, coverage, send-to-advisor |
| `backend/extraction/` | Offline ops (tour days, components, Tour Graph, orphans). Place writers are frozen (ADR 0002; place pipeline moves to CIS, AA-712) |
| `backend/shared/` | DB pool, schemas, auth, Model Gateway client |
| `migrations/` | `tripplanner.*` migrations; each registers `tp-NNN` in `shared.schema_versions` (from tp-007) |

## Local setup

```bash
# frontend
cd frontend && npm ci
cp .env.local.example .env.local     # NEXT_PUBLIC_MAPBOX_TOKEN, BROWSE_API_URL, TRIP_API_URL, TRIPPLANNER_API_KEY
npm run dev                          # http://localhost:3000

# backend (unit tests are offline: no DB, Mapbox or Bedrock)
cd backend && python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=.. pytest
python -m backend.browse.local_server     # optional local Lambda emulation (see local_server.py)
# no DB: catalog fixtures + placeholder photos on :8011 (AA-812; point BROWSE_API_URL there)
CIS_PUBLIC_API_BASE=http://localhost:8011 python -m backend.tests.dev_browse_server
```

## Deploy

- **Frontend:** Vercel — a preview per PR, production on merge to `main`.
- **Lambdas:** merge to `main` touching `backend/**` → `.github/workflows/deploy-lambdas.yml`
  (OIDC role `aa-tripplanner-dev-app-deploy`, defined in AA-CIS-Infra) updates both functions'
  code. The functions, API Gateway, S3 artifacts and secrets themselves are Terraform in
  `AA-CIS-Infra/accounts/aa365/tripplanner.tf`.
- **Migrations:** applied to Dev from an identical copy in `AA-CIS-Infra/db/migrations/tripplanner/`
  by the **DB Migrate** workflow (check → apply), before the code that needs them merges.

## Environment variables

Names only — values come from Secrets Manager / Lambda config (AA-CIS-Infra) or Vercel.

| Where | Variables |
|---|---|
| Lambdas | `CIS_PUBLIC_API_BASE` (AA-812, photo URL base; defaults to the Dev CIS API), `TRIPPLANNER_DATABASE_URL` (or `TRIPPLANNER_DATABASE_URL_ARN`), `TRIPPLANNER_API_KEY`, `BEDROCK_REGION`, `BEDROCK_ACCT_PRIMARY` / `_FALLBACK`, `BEDROCK_ROLE_NAME_PRIMARY` / `_FALLBACK`, `BEDROCK_EXTERNAL_ID_PRIMARY` / `_FALLBACK`, `ADVISOR_NOTIFY_EMAIL`, `REQUIRE_REGISTRATION_BEFORE_HANDOFF` |
| Frontend | `NEXT_PUBLIC_SITE_URL` (AA-812, canonical origin for metadata/sitemap), `NEXT_PUBLIC_MAPBOX_TOKEN` (also the static maps on tour/destination pages), `BROWSE_API_URL`, `TRIP_API_URL`, `TRIPPLANNER_API_KEY` (server-side only) |

`ADVISOR_NOTIFY_EMAIL` still defaults to a test address and the sender is a logging stub — switch
both before real customers (AA-731).

## Program rules

- Ship through a PR; merging to `main` is Nghiệp's call (he may delegate it per session).
- No Terraform / IAM / infra provisioning from this repo (that is AA-CIS-Infra).
- Never write into AA-CIS-App schemas (`gold_aa_internal`, `acp_contract`, …) or
  `shared.destinations`; read the views CIS grants. Only `tripplanner.*` and `shared.llm_call_log`
  are written from here.
