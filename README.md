# AA-TripPlanner-Web

Standalone, AA-branded B2C web app: browse Adventure Asia destinations on
a map, pin the places and activities that interest you, and watch a
day-by-day itinerary assemble live. Every itinerary component resolves to
a real AA published tour — nothing is invented. When ready, the draft is
sent to an AA advisor who turns it into a bookable trip.

Spec: `.kiro/specs/tripplanner-mvp/` · Steering: `.kiro/steering/`

## Layout
```
frontend/    Next.js (App Router, TS, Tailwind) + Mapbox GL JS — Vercel
backend/
  browse/      Lambda A — Map/Browse (stateless, cacheable, no LLM)
  assembly/    Lambda B — Trip Assembly (stateful, event log, Bedrock)
  shared/      db pool, Pydantic schemas, Bedrock satellite
  extraction/  offline pipeline: atoms -> geocoded itinerary_components
migrations/  001 tripplanner schema, 002 shared.destinations
docs/        setup + assessment notes
```

## Architecture
Browser → Next.js (Vercel) → BFF (Next.js API routes) → two Lambdas:
- **Lambda A (browse)** behind CloudFront: tiles, destination detail,
  search. Never calls Bedrock.
- **Lambda B (assembly)**: owns the trip event log, deterministic
  sequencing, and two LLM steps (`compose`, `renarrate`) via a Bedrock
  satellite (acc3 → acc1).

Both read/write one shared Postgres (with AA-CIS-App): schema
`tripplanner.*` (owned) + `shared.destinations` (shared golden record).

## Setup
- Mapbox tokens: see [docs/mapbox-setup.md](docs/mapbox-setup.md)
- Task-by-task feasibility: see [docs/task-assessment.md](docs/task-assessment.md)

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local   # fill NEXT_PUBLIC_MAPBOX_TOKEN
npm run dev
```

### Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # fill DB URL + tokens
pytest
```

## Program rules
- No merge to `main` by agents — human-only. Open a PR per unit of work.
- No Terraform / IAM / infra provisioning by agents.
- Never write into `gold_aa_internal`, `acp_contract`, or other
  AA-CIS-App schemas (extraction reads them only).
