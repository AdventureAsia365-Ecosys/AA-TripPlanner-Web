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
