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

Pre-push hook (AA-993): `scripts/install-hooks.sh` once per clone. Every `git push` then runs
`scripts/pre-push.sh`, which runs the CI checks the diff against `origin/main` needs (resource IDs and CI script
tests always; backend tests of the changed modules; migration lint; `npm run lint`, `typecheck` and `test` when
`frontend/` changed, never `next build`; docs lint). `AA_PREPUSH=full git push` runs the CI backend command;
`git push --no-verify` skips the hook once. Details: `docs/implementation-notes/AA-993.md`.

## Deploy

- **Frontend deploys:** CI-created via `.github/workflows/vercel-deploy.yml` (only when `frontend/**`
  changes: PR → preview, `main` → production); the Vercel Git integration is off via
  `frontend/vercel.json` (`git.deploymentEnabled: false`). Needs the repo secret `VERCEL_TOKEN`.
- **Lambdas:** merge to `main` touching `backend/**` → `.github/workflows/deploy-lambdas.yml`
  (OIDC role `aa-tripplanner-dev-app-deploy`, defined in AA-CIS-Infra; its ARN is the repo secret
  `AWS_DEPLOY_ROLE_ARN`, AA-902) updates both functions' code. The functions, API Gateway, S3 artifacts and secrets themselves are Terraform in
  `AA-CIS-Infra/accounts/aa365/tripplanner.tf`.
- **Post-deploy verify (AA-993):** both deploy workflows end with `post-deploy-verify.yml` (production
  `/api/health` on the deployed commit + version; each Lambda's CodeSha256 = the zip of the deployed commit, plus
  browse/assembly reads). A failed or skipped verify turns the deploy run red (`result` job). Re-run by hand:
  Actions → "Post-deploy verify" → `app`, `expected_sha` (from `main`).
- **Releases (AA-891):** release-please keeps a release PR open on `main` (version in
  `.release-please-manifest.json`, notes in `CHANGELOG.md`, tag `vX.Y.Z`); merging it releases and deploys
  nothing. `GET /api/health` on the web app shows `version` and `git_sha`.
- **Migrations:** applied to Dev from an identical copy in `AA-CIS-Infra/db/migrations/tripplanner/`
  by the **DB Migrate** workflow (check → apply), before the code that needs them merges.

## Environment variables

Names only — values come from Secrets Manager / Lambda config (AA-CIS-Infra) or Vercel.

| Where | Variables |
|---|---|
| Lambdas | `CIS_PUBLIC_API_BASE` (AA-812, photo URL base; defaults to the Dev CIS API), `TRIPPLANNER_DATABASE_URL` (or `TRIPPLANNER_DATABASE_URL_ARN`), `TRIPPLANNER_API_KEY`, `BEDROCK_REGION`, `BEDROCK_ACCT_PRIMARY`, `BEDROCK_ROLE_NAME_PRIMARY`, `BEDROCK_EXTERNAL_ID_PRIMARY` (acc3, the only LLM satellite since AA-998), `ADVISOR_NOTIFY_EMAIL` (optional log label, see below), `REQUIRE_REGISTRATION_BEFORE_HANDOFF` |
| Frontend | `NEXT_PUBLIC_SITE_URL` (AA-812, canonical origin for metadata/sitemap), `NEXT_PUBLIC_MAPBOX_TOKEN` (also the static maps on tour/destination pages), `BROWSE_API_URL`, `TRIP_API_URL`, `TRIPPLANNER_API_KEY` (server-side only); AA-908 traveller sign-in (server-side only, Production + Preview, set by pipe): `TP_SESSION_SECRET` (at least 32 random characters, seals the session cookies) `TP_COGNITO_BFF_KEY` (= AA-CIS-Infra secret `tripplanner/<env>/cognito-bff-key`) and `TP_COGNITO_SIGNUP_PROOF_KEY` (= AA-CIS-Infra secret `tripplanner/<env>/cognito-signup-proof-key`; without it new addresses cannot sign up); without the first two sign-in answers "not available" and the planner works as a guest |

TripPlanner sends no e-mail: "Send to an advisor" writes the `inquiry.submitted` outbox row and AA-Booking
e-mails staff when it opens the trip case (job `bk_notify_email`, AA-811). `assembly/notify.py` only logs the
hand-off; `ADVISOR_NOTIFY_EMAIL` is an optional label for that log line (empty by default).

## Reading the AI guards (AA-805)

There is no public endpoint for these on purpose (the browse/trip APIs only carry the edge key).
Read them in the database, read-only, through the usual ECS exec pattern of the root workspace.

- **Narration cache hits** — `tripplanner.narration_cache`, one row per cached itinerary:
  `count(*)` rows, `sum(hits)` reuses, `count(*) FILTER (WHERE hits > 0)` rows reused at least once,
  `max(last_hit_at)` last reuse. Only narrations that used the model (`source` `llm` / `mixed`) are
  stored; a trip made only of AA master day text is never cached, so it never shows a hit. A cached
  answer also carries `"cached": true` in the `/narrate` response. `hits` counts reuses since the row
  was last written: an entry older than `NARRATION_CACHE_TTL_HOURS` (default 168) is not served, and
  the next model narration overwrites it with `hits = 0`, so `sum(hits)` is a recent-window figure,
  not a lifetime total. Expired rows stay in the table until then, so filter on `created_at` for the
  live entries.
- **Daily spend vs the cap** — `tripplanner.llm_spend_daily` (UTC `day` x `stage`: `cost_usd`,
  `calls`, `unpriced_calls`). The cap compares today's sum over `tp_compose` + `tp_search_embed`
  with `TP_DAILY_SPEND_CAP_USD` (code default 5.0 when the Lambda env does not set it). Over the
  cap, `/narrate` answers with `"ai_budget_exhausted": true` and `/browse/search` with the keyword
  ranking (200, `"ai_budget_exhausted": true`). When the counter or the route config cannot be read,
  or the model provider fails, both degrade the same way with `"ai_paused": true` (AA-642 S0, fail
  closed).
- **Throttling** — API Gateway route settings (AA-CIS-Infra): `POST /trip/{trip_id}/narrate`
  2 rps / burst 5, `GET /browse/search` 10 / 20. A throttled call gets 429 before the Lambda runs.
- **Per-visitor AI limits (AA-1015, tp-023)** — the Lambdas count each request that would call a model in
  `tripplanner.ai_rate_counter` (fixed windows): 10 narrations per hour per guest session, 30 searches per minute
  per traveller IP, twice that signed in (`TP_RATE_*` env, defaults in `backend/config.py`). Over a limit the answer is
  429 `{"error":"rate_limited","scope":…,"retry_after_s":…}` with `retry-after`, and one `ai_rate_limited` log line
  (no key, IP or session). The planner shows a calm EN / VI line, no spinner. The BFF sends the traveller IP
  (`x-tp-client-ip`) and the signed-in state (`x-tp-signed-in`, search only); the Lambdas trust them only with the
  right `x-tripplanner-key`. Without a trusted IP there is no per-IP limit; a counter that cannot be written lets the
  request through with an `ai_rate_counter_error` line (the spend cap still fails closed).
- **Stage caps and the 80 % alert (AA-1015)** — `TP_DAILY_SPEND_CAP_SEARCH_USD` / `TP_DAILY_SPEND_CAP_COMPOSE_USD`
  (unset by default = only the combined `TP_DAILY_SPEND_CAP_USD`) stop one stage without touching the other. At 80 %
  and at 100 % of a cap each container prints one `llm_spend_alert` line a UTC day (`threshold` near / cap, `level`
  warning / error); `budget_status` also returns `near_cap`.
- **Search on inactive tours** — `backend/tests/fixtures/search_labelled_30.json` is the labelled
  30-query set for live runs: every destination returned must have at least one embedded component
  of a tour with `published_tours.master_status = 'active'` and `deleted_at IS NULL`. Join exactly as
  `backend/browse/search.py` `ACTIVE_TOUR_SQL` does (`pt.tour_id::text = c.source_tour_id`, AA tenant
  set for RLS); a component whose `source_tour_id` matches no `published_tours.tour_id` is dropped from
  search, so count those separately rather than reading them as inactive-tour hits.

## Program rules

- Ship through a PR; merging to `main` is Nghiệp's call (he may delegate it per session).
- No Terraform / IAM / infra provisioning from this repo (that is AA-CIS-Infra).
- Never write into AA-CIS-App schemas (`gold_aa_internal`, `acp_contract`, …) or
  `shared.destinations`; read the views CIS grants. Only `tripplanner.*` and `shared.llm_call_log`
  are written from here.
