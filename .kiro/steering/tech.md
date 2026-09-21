# Tech: AA_TripPlanner_AI

## Stack
- Frontend: Next.js (App Router), TypeScript, Tailwind. Own Vercel
  project, own domain — not a route inside AA-CIS-App. Map via Mapbox GL
  JS (free tier, built-in clustering).
- Backend: Python 3.12, TWO separate Lambda functions — not one. See
  "Why two Lambdas" below.
- Data: PostgreSQL — the SAME RDS instance AA-CIS-App already uses (acc2,
  us-west-1). Two schemas involved:
  - `tripplanner.*` — owned by this service (itinerary_components,
    customers, sessions, trip_events, trip_drafts)
  - `shared.destinations` — a table in AA-CIS-App's existing `shared`
    schema (already used for `tenants`, `membership_plans`, `audit_log`).
    This is deliberate: destinations/country data is a candidate golden
    record for AA's wider Master Data Management gap (Strategic Analysis,
    CIO Lens, gap G-D), so it does not live in a TripPlanner-private
    schema.
  - pgvector is already enabled on this instance (confirmed via STEP 0:
    version 0.8.1) — reuse it, do not add a new vector DB service.
- LLM: AWS Bedrock, via the same satellite pattern already live for
  AA-CIS-App/ACPv2 — assume a role on account 786888028788 (acc3,
  primary), fallback to account 867490540162 (acc1). Sonnet-tier for both
  LLM steps (`compose` and `renarrate` — see design.md). There is no
  Haiku-tier "parse brief" step in this build (no chat intake).
- Geocoding: Mapbox Geocoding API (already needed for the map itself, no
  new vendor) — called once per new place name, result cached into
  `shared.destinations` so it's never called twice for the same place.
- CDN: CloudFront in front of the Map/Browse Lambda's two read routes
  only (tile queries and destination-detail queries). Never in front of
  the Trip Assembly Lambda — its state is per-visitor and must never be
  cached across visitors.
- Secrets: AWS Secrets Manager, same account (acc2). No AWS access keys
  hardcoded anywhere — IAM role only.

## Why two Lambdas, not one
Map/Browse and Trip Assembly have fundamentally different operating
characteristics:
- Map/Browse: stateless, read-heavy, must be fast and cacheable, never
  calls an LLM.
- Trip Assembly: stateful (per-visitor event log), calls Bedrock, cannot
  be cached across visitors.
Splitting them lets CloudFront sit in front of one and never the other,
and lets each scale/be reasoned about independently. This is a hard
architectural boundary, not a style preference — do not merge them into
one Lambda for convenience.

## Data source: real atoms, not mock data, not raw itinerary text
Verified via STEP 0 (read-only, S3-mediated ECS exec) before this build
started:
- `gold_aa_internal.published_tours`: 74 rows total, only 31 have
  `master_status = 'active'`.
- `acp_contract.tour_atoms`: 3316 atoms across 71 tours; **all 31 active
  tours have atoms** (1289 atoms). No active tour is missing atom data
  entirely.
- Atom shape confirmed real and usable: `text` is a short place+activity
  phrase (e.g. "Taj Mahal — examine Shah Jahan's architectural
  ambitions"), tagged with `itinerary_day`. This is close to
  extraction-ready — it does NOT need to be parsed fresh from
  `aa_itineraries` free text.
- Known limitations, accepted for this build (do not build workarounds
  for these): atom count per tour-day varies hugely (1 to ~8 atoms/day
  across sampled tours); some tours have gaps in their `itinerary_day`
  sequence (a day with zero atoms simply produces no component for that
  day).
- `raw_tours.country` (and therefore `published_tours` joined data) has
  known dirty values — `SRI-LANDKA` and `OKINAWA` were found as separate
  values from `Sri Lanka` and `Japan`. A small hard-coded normalization
  map is applied during extraction (see design.md) as a local fix. The
  source-of-truth fix is tracked separately: Linear issue AA-571.

## Explicit non-reuse (verified against aa-ecosys-repos skill)
- Do NOT depend on `AA-ACP-Core` (the `acpcore` pip package). It is dead:
  2 commits total, ACPv1-only event schemas, no atom or grounding logic
  in it, nobody calls it today.
- Do NOT depend on `AA-ACP-App`. Abandoned, 0 production traffic.
- Do NOT depend on AAA (the mobile/Laravel project). Status
  inactive/uncertain — treat as fully absent.
- The real Bedrock satellite invocation code and ACPv2's grounding
  utilities live INSIDE the `AA-CIS-App` repo as application code, not as
  an installable package. Re-implement the satellite call (STS
  assume-role + Bedrock invoke) fresh in this repo — it is a small,
  well-understood pattern. Do not attempt to import anything from the
  AA-CIS-App repo.

## Infra that IS reused — via Terraform/IAM, not via code import
- `AA-CIS-Infra/accounts/acc3-bedrock/` and `accounts/acc1-bedrock/`
  already define the cross-account trust roles used by AA-CIS-App's ECS
  task. Both new Lambdas' execution roles need to be added as additional
  trusted principals on those same roles — a Terraform change applied
  manually by Nghiep (MFA, workflow_dispatch), never something an agent
  runs automatically.
- Same RDS instance, new/existing schemas only — no new database
  instance to provision.
- New CloudFront distribution — also Terraform, also human-applied.

## Grounding approach (deliberately simpler than ACPv2's F1-F9 gates)
After the LLM assigns activity/intensity/duration/season to an atom
group, a DETERMINISTIC check (not a second LLM call) confirms the
assigned activity keyword (or a close synonym) actually appears in that
group's raw atom text before the component is accepted. This mirrors
ACPv2's `find_novel_numeric_claims()` discipline, applied to category
assignment instead of numeric claims. On failure, reject and retry once,
then skip that group (no component produced) rather than accepting an
unverified guess.

## What Kiro must NOT do
- No Terraform / AWS infrastructure provisioning of any kind.
- No AWS credential creation, no hardcoded keys anywhere.
- No merge to main — human-only merge is a standing program rule. Open a
  PR (`gh pr create`) after each logical unit of work instead.
- Do not build a fallback that re-parses `aa_itineraries` free text for
  tour-days with zero atoms — accepted as an out-of-scope edge case for
  this build.

## Current deployed state (updated 2026-09-13)
See docs/architecture-overview.md + docs/change-report-mvp-completion.md
for the full picture. Deltas from the original plan above:

- **Edge auth**: API Gateway stays `authorization = NONE`; instead both
  Lambdas verify a shared secret header `X-TripPlanner-Key`
  (backend/shared/auth.py), which only the server-side BFF sends. Secret
  is `tripplanner/dev/api-key` (Terraform `random_password`), injected as
  the Lambda env `TRIPPLANNER_API_KEY` and set in Vercel env. Disabled
  when the env is empty (local/tests). A JWT/authorizer + CloudFront are
  still deferred.
- **Compose model id**: `global.anthropic.claude-sonnet-4-6` (NOT `us.`).
  The satellite invoker roles (AA3-Bedrock-Invoker / AA-Bedrock-Invoker)
  only allow the `global.` inference profile; the `us.` id → AccessDenied.
  Embed stays `us.cohere.embed-v4:0` (direct on acc2, 1536-dim).
- **Narration**: reachable via `POST /trip/{id}/narrate` (compose |
  renarrate). Uses a BUFFERED `bedrock invoke` (not
  InvokeModelWithResponseStream) — the route returns the whole narration
  as JSON, so streaming buys nothing and needs no extra IAM.
- **Embeddings**: the deterministic seeder leaves `embedding = NULL`;
  `backend/extraction/backfill_embeddings.py` fills them (idempotent).
  Semantic search only returns rows with a populated embedding.
- **Search UI**: the FilterChips search box calls the semantic
  `/browse/search` endpoint (debounced); the map shows ranked results.
- **Frontend deploy (Vercel, Root Directory = frontend)**: copy
  `frontend/.vercel` to the repo root, then run
  `vercel deploy --prod --yes --archive=tgz` FROM THE REPO ROOT (remote
  build applies Root Directory correctly). Do NOT run `vercel --prod` from
  `frontend/` (looks for `frontend/frontend` → ENOENT). Do NOT
  `vercel pull` from the repo root (writes a bad microfrontends
  vercel.json).
- **Local AWS + MFA**: terraform/boto3 under a non-interactive shell hit
  an MFA prompt for the assume-role profiles. Workaround:
  `aws configure export-credentials --profile <p> --format env`, eval the
  `export AWS_*` lines, then `unset AWS_PROFILE`.

## Deferred (tracked)
- Auto-refresh: new tours atomized into `acp_contract.tour_atoms` do NOT
  automatically appear in TripPlanner — extraction is manual/offline. A
  scheduled/triggered re-run (+ idempotent `run.py`) is a future task.
- Real advisor email (SES), CloudFront over Browse, Mapbox token URL
  restriction, mis-geocoded-destination cleanup, RDS admin pw rotation.
