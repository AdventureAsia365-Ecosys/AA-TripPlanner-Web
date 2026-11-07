# AA-1038 R5 slice 3 — leads, brand and the per-tenant AI cap (AA-TripPlanner-Web part)

Session S272 (lane l10). Root ADR 0019 d10, d11, d2 / b19 (+ the S272 d11 amendment). Contract `lead.submitted.v1`
(root, new) and `tenant.entitlements.v1` 1.5 (CIS 390). Other parts: AA-CIS-App (CIS 390, kit core `brandTheme.ts`),
AA-Partner (`crm_tp_lead_pull`), root (contracts, ADR, DM-7.1, ERD design page).

## Decisions

- **Store in the outbox, deliver by the tenant's channel.** tp-026 lets `tripplanner.outbox` carry `lead.submitted`
  rows with `delivery_channel` / `delivered_at` / `delivery_attempts` / `delivery_error`: the outbox already commits
  with the trip's `sent` event, has a tenant (tp-025) and a redaction marker (tp-021). `v_inquiry_outbox` filters its
  event, so Booking never sees a lead.
- **Refuse before storing** when the lead could not be delivered (no route, the route view not there yet, a webhook /
  e-mail channel this deployment cannot use): a lead that can never leave is personal data with no purpose.
- **CRM channel = pull + ack over HTTP** (the CRM has no DB grant here, ADR 0008a). New route class `/trip/m2m/v1/*` in
  the assembly Lambda, reached by the existing `ANY /trip/{proxy+}` route: an aa-m2m token (CRM client, scope
  `tripplanner.leads/deliver`) replaces the edge key on these paths only, verified before any SQL with the shared
  verifier byte copy (`groups_claim="scope"`, as CIS / Booking). The path's tenant is bound (RLS) and the payload's
  tenant is checked again on the way out.
- **Webhook / e-mail inline after the commit**, retried by the daily `tp_lead_deliver`; the 7-day hold ends every
  undelivered lead (any channel) with an ids-only payload. The expiry runs live on the schedule (the contract's promise,
  not a clean-up of history); a manual run with `"dry_run": true` counts only.
- **Per-tenant AI cap in `budget_status`**, after the app caps, from CIS 390's `catalog.v_tenant_ai_budget_v1`
  (fail closed for an agency tenant; the demo tenant / bare path keep today's behaviour). Search learns its tenant from
  the same signed assertion in the browse Lambda (no DB binding there: `tenant.using`). The ledger row takes the tenant
  from the request context, so nothing in the request body can name it.
- **Review fix pass (S272).** (1) `tp_lead_deliver` runs the expiry FIRST in its own transaction, then the retry
  with a 120 s wall-clock budget (`RETRY_BUDGET_S`; the rest is `deferred`), a breaker per tenant (one failed attempt
  = that tenant is not tried again this run) and per channel (`webhook_key_unavailable` / `email_sender_unavailable`
  stop the channel). An expired undelivered lead adds `attention: ["lead_expired_undelivered"]` to the result, which
  `jobs/handler.attention` reports, so the scheduled tick fails loudly (the jobs Lambda error alarm) before anyone
  wonders where a lead went; expiries are counted per channel (`expired_crm`, ... in `lead_deliver_run`).
  (2) The crm channel is gated like the other two: `TP_LEAD_CRM_PULL_ENABLED` (default off) on the assembly Lambda;
  off = 503 `lead_channel_unavailable`, nothing stored. (3) `Traveller.phone` is pinned to E.164 with its country
  code (`^\+[1-9][0-9 ()-]{5,39}$`) in the contract, `leads.validate` and the form (hint + `PHONE_RE`), because the
  CRM normalises without a region; the CRM also keeps a phone it cannot normalise as text in the inquiry message.
- **Review fix pass 2 (S272).** Each item has its reproduction as a test (`test_aa1038_lead_review.py`,
  `test_aa1038_lead_deliver.py`, `lib/leadForm.test.ts`, `lib/botCheck.test.ts`, `migrations/tests/test_tp026_pg.py`).
  (1) **True 7-day maximum.** `hold_cutoff` = now - (`LEAD_HOLD_DAYS` - one tick interval - 1 h margin) for the daily
  EventBridge tick (`EXPIRY_TICK_INTERVAL` mirrors Infra `cron(30 19 * * ? *)`; a test replays that schedule with a
  lead written 1 minute after a tick and a tick up to 59 minutes late). Chosen over an hourly schedule or an
  `expires_at` column: no Infra or migration change. `TP_LEAD_HOLD_DAYS` is clamped to 2..7 (`config.lead_hold_days`;
  a non-number = 7). `MAX_ATTEMPTS` 3 (the expiry is idempotent) and a re-queued run is attention at once.
  (2) **Whole backlog each run.** The expiry loops batches of `EXPIRE_MAX` (each its own transaction) until none is
  left or `EXPIRE_BUDGET_S` (90 s) is used, before the retry part; a batch that raises is tried again at once
  (`EXPIRE_TRIES`); rows a concurrent ack holds (`SKIP LOCKED`) are looked at again; rows left ask for attention
  (`lead_expiry_incomplete`).
  (3) **Send limits.** BotID Basic on `POST /api/trip/send` in the BFF (own pathname, like narrate), then in the
  Lambda `ratelimit.hit` scopes `send` (per guest session, 5/h) and `send_ip` (per IP, 20/h), counted only when a lead
  would be stored, then a per-tenant 24-hour cap (`TP_LEAD_TENANT_DAILY_CAP`, 200) counted inside the send
  transaction; every refusal stores nothing. tp-026 adds `outbox_lead_tenant_created_idx` for that count.
  (4) **Reject path.** `_text` applies NFC before the length check (the CRM's `clean_text` rule); the form does the same
  with code points; one rule pinned by shared vectors (root `docs/contracts/rules/`, byte copy
  `backend/tests/contracts/lead.submitted.v1.traveller-rules.json`) run by the Lambda, the form and the CRM tests. A
  rejected ack logs an ERROR line and the daily run reports `lead_rejected_by_crm` (counts per reason, no PII); the
  CRM raises its ops alert `tp_lead_rejected`.
  (5) **Per-tenant crm readiness = a pull beacon.** Each M2M list call records a beacon for that tenant (the
  `ai_rate_counter` table, keyed hash, scope `crm_pull`, 5-minute windows, purged with it); the crm channel stores a
  lead only when the CRM listed the tenant within `TP_LEAD_CRM_PULL_FRESH_S` (600 s), else 503
  `lead_channel_unavailable`. The CRM enters the agency before listing, so a locked / offboarding / unprovisioned
  agency never refreshes it. Chosen over a CIS readiness column: CIS does not hold the CRM's lock state, and this
  needs no migration or contract change between CIS and the CRM. A beacon read error fails closed (refused).
  (6) **Hardening.** `serve_m2m` serves only a tenant whose route resolves to crm now (404 `tenant_not_on_crm`);
  an ack never changes a final state; the webhook refuses a non-public address (`webhook_urlnotpublic`); the inline
  SES client has 2 s / 3 s timeouts and no SDK retry; tp-026 guards the `v_inquiry_outbox` filter (whitespace-
  normalised view definition must end with the one predicate), tested on a real PostgreSQL with widened views.
- **Brand** via the kit core layer only (`ui/core/brandTheme.ts`): no per-page colours, the planner just renders the
  block. BO-8 badge only with `tp.branding`.

## Changed

- `migrations/026_lead_outbox_delivery.sql` (+ `migrations/tests/test_tp026_pg.py`) — the migration-only PR.
- Backend: `assembly/leads.py`, `assembly/handler.py` (`_send_lead`, `serve_m2m`, the M2M branch of `_handle`),
  `shared/m2m_auth.py`, `shared/tenant_budget.py`, `shared/tenant.py` (`current()`, `using()`, source `m2m`),
  `shared/llm_gateway.py` (tenant cap, ledger tenant), `browse/handler.py` + `browse/search.py` (search tenant, pause
  reasons), `jobs/lead_deliver.py` + registry, `config.py`.
- Frontend: `components/LeadForm.tsx`, `lib/leadForm.ts`, `TripPanel`, `PlannerApp` (agency), `lib/plannerTenant.ts`,
  `lib/useTrip.tsx`, `lib/api.ts` (lead, tenant on search), `/api/trip` (`lead`), `/api/browse` (signed search),
  `app/[tenant]/plan/page.tsx` (brand `<style>`).
- Tests: `test_aa1038_leads.py`, `test_aa1038_tenant_budget.py`, `test_aa1038_leads_pg.py` (CI PG list),
  `lib/leadForm.test.ts`; guard tests adjusted: `test_extraction_ops.py` (a `name/` is a scope, not a table),
  `test_read_guard.py` (`m2m_*` routes are not trip reads), `test_jobs_pg.py` (third daily kind).

## Tradeoffs

- The CRM pull is per agency per minute (CRM side); fine at pilot scale.
- The webhook key is derived per tenant from one TripPlanner master secret: AA hands the agency its key (a small ops
  script, follow-up); rotating the master rotates every tenant's key.
- `narration_cache` and `ai_rate_counter` stay tenant-less: the cached text is AA master text + AI for the same
  itinerary, no tenant data; the limiter keys are per session / IP.

- The per-tenant daily cap protects the agency and AA's SES from a send loop, but a determined sender with many IPs
  could fill one tenant's cap for a day (the tenant then gets 429 for real travellers). 200 a day is far above pilot
  traffic; raise it per deployment with `TP_LEAD_TENANT_DAILY_CAP`.
- The webhook is still retried only by the daily run (an endpoint down for a minute delays a lead up to a day), and
  `channel_ready` treats the webhook channel as ready once the master secret exists, before AA has handed any agency
  its key; the key-handover script is the follow-up that must ship before a tenant is routed to a webhook.
- The beacon makes a crm tenant refuse leads for up to 10 minutes after a CRM outage starts (and after the CRM is
  first switched on): intended, a lead is stored only when it can leave.

## Should know

- Merge order: tp-026 migration PR (applied by Migration Apply), CIS 390 (applied), the CIS kit PR and its Kit Sync PR
  here, then this code PR (it imports `ui/core/brandTheme.ts`; the branch carries the tp-026 commit until rebase).
- AA-CIS-Infra follow-ups before the channels work live: the aa-m2m resource server `tripplanner.leads` (scope
  `deliver`) for the CRM client and `TRIPPLANNER_API_URL` on the CRM task; Secrets Manager
  `tripplanner/<env>/lead-webhook-key` readable by the assembly and jobs roles; `TP_LEAD_EMAIL_FROM` + SES send for both.
- These Infra changes are deploy PREREQUISITES of each channel, not notes: switch `TP_LEAD_CRM_PULL_ENABLED=true` on
  the assembly Lambda only together with (or after) the CRM's M2M scope and `TRIPPLANNER_API_URL`; give the jobs role
  the webhook key and SES send in the same apply as the assembly role (else every retry stops at the channel breaker
  and the expiry ends the lead, loudly). Until then every channel answers 503 `lead_channel_unavailable` and stores
  nothing; the slice is not live-verified before the crm pull is on.
