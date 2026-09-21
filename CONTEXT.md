# AA-TripPlanner-Web — Repo Context

B2C map-based trip planner in the Adventure Asia (AA) ecosystem. An end traveler explores
destinations on an interactive map, assembles a personal multi-day trip, and gets AI-assisted
suggestions and narrative. This repo is a self-contained product: its own frontend, its own
serverless backend, and its own database schema — it does **not** share application code with
AA-CIS-App. What it shares with the rest of the ecosystem is a small set of AWS-level resources
(Bedrock access, OIDC deploy roles, the destinations reference data) provisioned by AA-CIS-Infra.

> Scope of this file: describe the whole repo accurately — what it is, how it is laid out, how it
> runs, and where its boundary with the rest of the AA ecosystem sits. Deep per-subsystem design
> notes belong in their own doc next to the code, not here.

## What it is (role in the AA ecosystem)

- **Product surface**: consumer-facing (B2C). The visitor is a traveler planning a trip, not an
  AA admin or a marketplace tenant. This is the opposite audience from AA-CIS-App (admin +
  tenant content pipeline).
- **Position**: a leaf product. It reads shared reference data (`shared.destinations`) that the
  CIS side owns, and it calls Bedrock for AI assembly. Nothing in the CIS content pipeline
  depends on TripPlanner — the arrow points one way (TripPlanner → shared/Bedrock), never back.
- **Ownership split**: this repo owns its application code and its own schema (`tripplanner.*`).
  AWS resources it needs (Lambda plumbing, OIDC deploy role, cross-account Bedrock trust) are
  owned by **AA-CIS-Infra**, consistent with the ecosystem rule "App owns code, Infra owns
  resources."

## Architecture at a glance

```
Traveler (browser)
      │
      ▼
Next.js 14 frontend  ──(Mapbox GL)──►  interactive map UI
      │  HTTP (JSON)
      ▼
Two Python 3.12 Lambda functions
  ├─ browse      → stateless, cacheable reads (destinations, suggestions)
  └─ assembly    → stateful writes (trip mutations) + Bedrock (AI narrative/plan)
      │
      ▼
Postgres
  ├─ tripplanner.*        (owned here — trips, events, taste vectors)
  └─ shared.destinations  (shared reference data, CIS-owned)
      │
      ▼
Amazon Bedrock  (via cross-account role: acc3 primary → acc1 fallback)
```

### Frontend

- **Next.js 14.2.15** (App Router), deployed on **Vercel**.
- **Mapbox GL** for the interactive map (destination pins, trip routing, day-by-day view).
- Talks to the backend Lambdas over HTTP; no direct DB access from the browser.
- Deploy is Vercel-driven: a PR shows a Vercel preview and CI status; merge to the production
  branch triggers the production deploy. Check the Vercel CI check on the PR for deploy success.

### Backend — two Lambdas, split by statefulness on purpose

- **browse Lambda** — read-only, **stateless and cacheable**. Serves destination browsing and
  suggestion queries. Because it never mutates trip state, its responses can be cached at the
  edge/CDN layer without correctness risk. Keep new read-only endpoints here.
- **assembly Lambda** — **stateful**, owns all trip mutations, and is the only side that calls
  **Bedrock**. Building/AI-narrating a trip is a write that also incurs model cost, so it is
  deliberately isolated from the cacheable read path. Keep anything that writes trip state or
  calls a model here.
- Runtime: **Python 3.12**. The two functions are separate deploy units so the cacheable read
  path and the expensive stateful path scale and cache independently.

### Data model — event-sourced trips

- A trip is stored as an **append-only event stream** rather than a single mutable row. Each user
  action (add destination, reorder day, remove stop) is an event; the current trip state is the
  fold over its events. This gives a natural history/undo and makes concurrent edits reconcilable.
- **Taste vector**: a per-traveler preference signal built from their interactions, combined with
  **geographic** proximity to drive suggestions ("you liked X, and Y is nearby and similar").
- Schema ownership: everything under `tripplanner.*` is owned by this repo's migrations.
  `shared.destinations` is **read-shared** reference data owned by the CIS side — treat it as
  read-only from here; do not migrate or mutate it from this repo.

### AI (Bedrock)

- Only the **assembly** Lambda calls Bedrock, for trip narrative/plan assembly.
- Cross-account access follows the ecosystem Bedrock routing: **acc3 primary → acc1 fallback**
  (the same accounts the CIS side uses; see `docs/ecosystem-architecture.md` for the account map).
- The trust and role wiring that make this call possible live in **AA-CIS-Infra**, not here.

## Deploy & CI

- **Frontend**: Vercel (preview per PR, production on merge to the production branch).
- **Backend Lambdas**: deployed via GitHub Actions assuming the OIDC role
  `aa-tripplanner-dev-app-deploy` (an Infra-owned IAM role). The role's trust is scoped to this
  repo through the org OIDC subject
  `repo:AdventureAsia365-Ecosys*/AA-TripPlanner-Web*:*` — see AA-CIS-Infra's `tripplanner.tf`.
- Because the org has "include repo ID in the OIDC subject" enabled, the trust uses the
  `AdventureAsia365-Ecosys*/...` wildcard form; a plain `repo:Ecosys/...` subject fails
  AccessDenied. This is the same OIDC fact that AA-588 corrected in Infra.

## Boundaries — what this repo does NOT do

- It does not run the content pipeline (Atom/Segment/Route/Slate/Piece/publish) — that is
  AA-CIS-App's domain entirely.
- It does not own or migrate `shared.destinations` — read-only from here.
- It does not provision its own AWS resources or OIDC roles — those live in AA-CIS-Infra.
- **Agents must not merge to the production branch directly.** Ship via PR and let the human
  merge (matches this repo's stated policy and the ecosystem program rules).

## Related documents

- `README.md` (this repo) — setup/run instructions.
- `docs/ecosystem-architecture.md` (root docs repo) — the ecosystem-wide account map, Bedrock
  routing, OIDC design, and how TripPlanner sits next to AA-CIS-App / AA-CIS-Infra.
- `AA-CIS-Infra/tripplanner.tf` — the OIDC deploy role and any TripPlanner-specific AWS resources.
