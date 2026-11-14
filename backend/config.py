"""Central configuration for the AA-TripPlanner backend.

All environment-driven values live here so nothing is hardcoded inline
across modules. In production the DB connection string comes from AWS
Secrets Manager (acc2); locally it comes from the environment / .env.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional
from datetime import datetime, timezone


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


# --- Database (shared RDS instance, acc2, us-west-1) ---
# Full DSN, e.g. postgresql://user:pass@host:5432/dbname
DATABASE_URL = _get("TRIPPLANNER_DATABASE_URL")

# Schemas this service touches. tripplanner is owned (user state written here). In shared it writes llm_call_log
# only (the gateway config is read through the catalog contract view since AA-1027 E5). CIS content is read only through the catalog views
# (ADR 0001); since AA-712 X4 nothing else of CIS (gold_aa_internal, acp_contract, shared.destinations)
# and no frozen tripplanner place table is read, and the PLACES_SOURCE switch is gone.
SCHEMA_TRIPPLANNER = "tripplanner"
SCHEMA_SHARED = "shared"

# --- Tenancy (AA-1038 R5 slice 1, root ADR 0019 d10: the "one shadow deploy") ---
# tp-025 gives sessions, trip_drafts, trip_events and outbox a tenant_id and a shadow RLS policy for the runtime role:
# no tenant context = every row, a tenant context (GUC app.tenant_id) = that tenant's rows only. The assembly Lambda
# binds DEFAULT_TENANT_ID on every request (backend/shared/tenant.py); there is no tenant header or path yet, so
# every request is the demo tenant (slug aa-tripplanner, kind demo in shared.tenants). The same fixed id is the
# backfill and the column-default fallback of tp-025.
DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000002"


def tenant_uuid(raw: Optional[str], name: str) -> str:
    """The canonical text form of a tenant uuid. Raises RuntimeError (the value is not echoed) when `raw` is not a
    uuid, so a bad TP_DEFAULT_TENANT_ID fails the cold start instead of binding a non-uuid (lesson S254: a slug where
    a uuid belongs was refused per call and the failure was swallowed)."""
    try:
        return str(uuid.UUID((raw or "").strip()))
    except (ValueError, AttributeError, TypeError):
        raise RuntimeError(f"{name} must be a uuid") from None


# Override for a test or a second environment only (Lambda env TP_DEFAULT_TENANT_ID); unset or blank = the demo tenant.
DEFAULT_TENANT_ID = tenant_uuid((_get("TP_DEFAULT_TENANT_ID") or "").strip() or DEMO_TENANT_ID, "TP_DEFAULT_TENANT_ID")


def is_aa_hosted(tenant_id: Optional[str]) -> bool:
    """AA's own planner: the AA-hosted tenant aa-tripplanner (DEMO_TENANT_ID, the fixed id CIS 379 seeds and guards:
    no other tenant may hold it) or the bare path's tenant (DEFAULT_TENANT_ID). It serves the AA catalogue and has no
    tenant AI budget. Keyed on the tenant id, never on `kind`: every synthetic agency on Dev is kind demo too, and
    each lists its own tours (AA-1094, S278 finding). The view catalog.v_tenant_tripplanner_v1 carries no plan code,
    so the fixed id is the explicit attribute. A value that is not a uuid is not AA-hosted."""
    if tenant_id is None:
        return False
    try:
        canonical = str(uuid.UUID(str(tenant_id).strip()))
    except (ValueError, AttributeError, TypeError):
        return False
    return canonical in (DEFAULT_TENANT_ID, DEMO_TENANT_ID)

# AA-1038 R5 slice 2: the /{slug} path. The BFF resolves the slug through GET /browse/tenant/{slug} (CIS view
# catalog.v_tenant_tripplanner_v1, CIS 379) and sends the assembly Lambda a signed tenant assertion in this header
# (backend/shared/tenant_assertion.py). No header = the bare path = DEFAULT_TENANT_ID, as in slice 1.
TENANT_ASSERTION_HEADER = "x-tp-tenant"
# The longest life an assertion may claim (exp - iat) and the clock skew allowed on iat, in seconds. The BFF signs a
# fresh one per request with a 120 s life.
TENANT_ASSERTION_MAX_LIFETIME_S = 300  # technical-constant: auth perimeter (audit 4.6)
TENANT_ASSERTION_SKEW_S = 30
# AA-1092 (S278 audit M19): the assertion key has its own secret, no longer derived from the edge key alone.
# On Lambda it is read by name, tripplanner/<ENVIRONMENT>/tenant-assertion-key: JSON {"current", "previous",
# "previous_until", "edge_derived_until"} (backend/shared/tenant_assertion.py; filled and rotated by AA-CIS-Infra
# tools/identity/tripplanner_assertion_key.py). TP_TENANT_ASSERTION_KEY in the env (a plain key, the "current" value)
# wins: local dev and tests. Cached like the edge key (API_KEY_CACHE_SECONDS).
TENANT_ASSERTION_KEY = _get("TP_TENANT_ASSERTION_KEY", "")
TENANT_ASSERTION_KEY_SECRET_NAME_TEMPLATE = "tripplanner/{environment}/tenant-assertion-key"
# A dedicated key shorter than this is a configuration error (fail closed), never a weak key in use.
TENANT_ASSERTION_KEY_MIN_LENGTH = 32  # technical-constant: crypto size (audit 4.8)
# The longest grace a rotation may give the previous key (or the edge-derived key at the cut-over): a forgotten
# timestamp far in the future must not keep an old key alive. Longer = ignored (logged), the old key is refused.
TENANT_ASSERTION_MAX_GRACE_S = 7 * 24 * 3600
# AA-1092 review round 2: a signature miss re-reads the secret once when the cached copy is at least this old, so a
# web task that already signs with a new current (first fill or rotation) is not refused for the rest of a warm
# container's API_KEY_CACHE_SECONDS. Throttle: at most one extra read per container per this many seconds, so forged
# assertions cannot turn into a Secrets Manager read each.
TENANT_ASSERTION_REFRESH_MIN_AGE_S = 30  # technical-constant: secret re-read throttle, auth perimeter (audit 4.6)

# --- CIS public API (AA-812) ---
# Base URL that catalog photo paths are relative to: {base}{path}?size=large|small (CIS
# migration 200). Browse responses carry absolute photo URLs built from it.
CIS_PUBLIC_API_BASE = _get("CIS_PUBLIC_API_BASE", "https://api-cis.lumiguides.it.com")

# --- Bedrock ---
# Region for all Bedrock calls (acc2 and acc3 are both us-west-1).
BEDROCK_REGION = _get("BEDROCK_REGION", "us-west-1")

# LLM (compose/renarrate) — Claude via the satellite pattern. acc2
# (where the Lambdas run) is a channel-program account and CANNOT invoke Claude
# ("Access to this model is not available for channel program accounts"),
# so the app assumes a cross-account role on acc3, the only LLM satellite (AA-998 retired
# the former acc1 fallback) — same as AA-CIS-App. Models MUST be called by inference-profile id
# (the bare foundation-model id fails with ValidationException).
# The satellite account ID comes from the Lambda environment (AA-CIS-Infra tripplanner.tf); this
# repo is public, so no account ID is written here (AA-902). Unset -> the satellite is skipped
# with a clear error in the failover log (llm_gateway._satellite_target); acc2 still runs.
BEDROCK_ACCT_PRIMARY = _get("BEDROCK_ACCT_PRIMARY", "")   # acc3
# acc3 invoker role and ExternalId. BEDROCK_ROLE_NAME is kept only as a legacy default
# when BEDROCK_ROLE_NAME_PRIMARY is set empty.
BEDROCK_ROLE_NAME = _get("BEDROCK_ROLE_NAME", "")  # legacy single-role fallback
BEDROCK_ROLE_NAME_PRIMARY = _get("BEDROCK_ROLE_NAME_PRIMARY", "AA3-Bedrock-Invoker")
BEDROCK_EXTERNAL_ID_PRIMARY = _get("BEDROCK_EXTERNAL_ID_PRIMARY", "aa296-satellite-bedrock-acc3")
# AA-685: which model each call uses (and its Bedrock profile per account) comes from the Model
# Gateway tables in the shared RDS — stages tp_compose (Claude via the satellite role) and
# tp_search_embed (Cohere Embed v4: acc2 first, then satellite
# rows by route, AA-862 — the embed fall-through reuses the BEDROCK_ACCT_PRIMARY /
# ROLE_NAME_PRIMARY / EXTERNAL_ID_PRIMARY values above; the account ID must be set on both Lambdas). See
# backend/shared/llm_gateway.py. Embeddings must stay 1536-dim to match VECTOR(1536).
EMBED_DIM = 1536

# --- Advisor notification (log label only) ---
# TripPlanner sends no e-mail: AA-Booking e-mails staff about a new inquiry (bk_notify_email, AA-811).
# Optional label for the hand-off log line in assembly/notify.py; no personal address as a default.
ADVISOR_NOTIFY_EMAIL = _get("ADVISOR_NOTIFY_EMAIL", "")

# --- Edge shared-secret (API Gateway auth is NONE; the app checks this) ---
# A shared secret the BFF must send in the X-TripPlanner-Key header. See shared/auth.py.
# AA-878: on Lambda the key comes from Secrets Manager, read by a NAME derived from the
# existing ENVIRONMENT env (tripplanner/<ENVIRONMENT>/api-key), never a plaintext env value.
# TRIPPLANNER_API_KEY in the env still wins (local dev, and the rollout window before Infra
# drops it from the Lambda env). Off Lambda with no env key the gate is DISABLED (local, unit
# tests); on Lambda a missing/unreadable key answers 503 (fail closed).
TRIPPLANNER_API_KEY = _get("TRIPPLANNER_API_KEY", "")
API_KEY_HEADER = "x-tripplanner-key"
# Set by Terraform on both Lambdas (dev today); only used to derive the secret name.
ENVIRONMENT = _get("ENVIRONMENT", "")
API_KEY_SECRET_NAME_TEMPLATE = "tripplanner/{environment}/api-key"
# How long a key read from Secrets Manager is reused per container: a rotation lands
# within this window without a redeploy.
API_KEY_CACHE_SECONDS = 600
# After a failed read, answer 503 straight away for this long instead of calling
# Secrets Manager on every request.
API_KEY_RETRY_AFTER_SECONDS = 10

# --- Leads to the agency tenant (AA-1038 R5 slice 3, root ADR 0019 d11, contract lead.submitted.v1) ---
# A lead not delivered within this many days is redacted anyway (data standards DM-7.1 row tripplanner.outbox: at most
# 7 days). The value is clamped to LEAD_HOLD_DAYS_MIN..LEAD_HOLD_DAYS_MAX, so an env typo (TP_LEAD_HOLD_DAYS=30) can
# never stretch the promise; a value that is not a number counts as the maximum. The floor is 3: the daily expiry
# needs two tick intervals of headroom inside the hold, one between runs and one failed tick it absorbs
# (backend/assembly/leads.py hold_cutoff, EXPIRY_TICKS_ABSORBED).
LEAD_HOLD_DAYS_MAX = 7
LEAD_HOLD_DAYS_MIN = 3


def lead_hold_days(raw: Optional[str]) -> int:
    """The hold window in days, clamped to LEAD_HOLD_DAYS_MIN..LEAD_HOLD_DAYS_MAX (unset / not a number = the max)."""
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return LEAD_HOLD_DAYS_MAX
    return max(LEAD_HOLD_DAYS_MIN, min(LEAD_HOLD_DAYS_MAX, days))


LEAD_HOLD_DAYS = lead_hold_days(_get("TP_LEAD_HOLD_DAYS", str(LEAD_HOLD_DAYS_MAX)))
# How many leads one tenant may receive in 24 hours (a script on a tenant's public planner could otherwise store
# traveller contacts and flood the agency's inbox or CRM without limit). Past it, a send is refused before anything is
# stored (429 lead_cap_reached).
TP_LEAD_TENANT_DAILY_CAP = int(_get("TP_LEAD_TENANT_DAILY_CAP", "200"))
# The crm channel takes a lead for a tenant only when the CRM listed that tenant's leads within this many seconds
# (plus one beacon window, ratelimit.BEACON_WINDOW_SECONDS): a CRM that cannot pull a tenant (agency locked,
# offboarding, not provisioned yet) means 503 lead_channel_unavailable and nothing stored. The CRM pulls every minute.
LEAD_CRM_PULL_FRESH_S = int(_get("TP_LEAD_CRM_PULL_FRESH_S", "600"))
# The webhook channel signs each POST with a key per tenant, derived from this master secret (Secrets Manager, read by
# name with the Lambda role; TP_LEAD_WEBHOOK_KEY in the env wins: local dev, tests). Absent = the webhook channel is
# not available: a tenant routed to a webhook gets 503 lead_channel_unavailable and nothing is stored.
LEAD_WEBHOOK_KEY_SECRET_TEMPLATE = "tripplanner/{environment}/lead-webhook-key"
TP_LEAD_WEBHOOK_KEY = _get("TP_LEAD_WEBHOOK_KEY", "")
# The e-mail channel sends from this address through SES (the AA sender, Reply-To the traveller). Unset = the e-mail
# channel is not available (503 lead_channel_unavailable, nothing stored).
TP_LEAD_EMAIL_FROM = (_get("TP_LEAD_EMAIL_FROM", "") or "").strip()
# Seconds one inline webhook POST may take after the request committed (the daily run retries a failure).
LEAD_WEBHOOK_TIMEOUT_S = float(_get("TP_LEAD_WEBHOOK_TIMEOUT_S", "3"))
# How long the send path waits for the webhook host to resolve before it refuses (503, nothing stored): the target is
# checked to be a public address BEFORE the lead is stored (backend/assembly/leads.py webhook_target_problem).
LEAD_WEBHOOK_RESOLVE_TIMEOUT_S = float(_get("TP_LEAD_WEBHOOK_RESOLVE_TIMEOUT_S", "2"))
# The crm channel is offered only once the CRM can pull from this deployment (AA-CIS-Infra: the aa-m2m resource
# server tripplanner.leads with scope deliver for the CRM client, TRIPPLANNER_API_URL on the CRM API and worker).
# Off (the default) = a crm-routed tenant gets 503 lead_channel_unavailable and nothing is stored, like a webhook /
# e-mail channel without its key or sender: a lead nobody can collect is personal data with no purpose.
LEAD_CRM_PULL_ENABLED = (_get("TP_LEAD_CRM_PULL_ENABLED", "") or "").strip().lower() in ("1", "true", "yes", "on")

# --- Guest credential (AA-827) ---
# The BFF holds a random token in an HttpOnly cookie and forwards it in this header on every
# mutating call; the session row stores only sha256(token) (migration tp-011). session_id is an
# identifier, never a credential. See assembly/guest_session.py.
GUEST_TOKEN_HEADER = "x-tripplanner-guest"
# AA-908: the signed-in traveller's Cognito EXTERNAL access token ("Bearer <jwt>"), forwarded by the BFF from its
# sealed session cookie. Verified in assembly/account.py; never logged or stored.
ACCESS_TOKEN_HEADER = "x-tripplanner-access"
# Until this instant a request without a token (old BFF / old bundle) is still accepted for a
# session that has no hash yet, and a legacy session is bound to the first token it sees. After
# it, a session_id alone gets 401. CLOSED: set to an instant already past (S239). Dev has no real
# guests, so there is no one to carry over and no reason to wait the planned 72 h; the grace code
# path stays in guest_session.py (and its tests) but is dormant. A code constant on purpose:
# changing it is a reviewed deploy, not a Lambda env edit.
GUEST_LEGACY_GRACE_UNTIL = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)

# --- Behavioural constants (from design.md non-functional table) ---
HOVER_DEBOUNCE_MS = 200
SEARCH_DEBOUNCE_MS = 400
COMPOSE_DEBOUNCE_S = 2.5
CDN_TTL_SECONDS = 30 * 60
TILE_DEGREES = 1.0
LONG_TRIP_WARN_DAYS = 25
GUEST_SESSION_DAYS = 90

# AA-1034 R0 (root ADR 0019 d8/d11, B2B-only model): Adventure Asia sells to partner agencies only and keeps no
# end-traveller record of its own. While this is on (the default), POST /trip/{id}/send-to-advisor answers 410
# `b2b_only` before any write: no `sent` event, no registration (tripplanner.customers), no inquiry.submitted outbox
# row, so no Booking case. The planner shows a demo notice instead. A code default (not set on the Lambdas); `false`
# turns the frozen hand-off back on. Leads go to the tenant's CRM later (lead.submitted.v1, R2+).
def demo_flag(raw: Optional[str]) -> bool:
    """On unless the value is exactly ``false`` (any case): an unset or mistyped value keeps the demo."""
    return (raw or "true").strip().lower() != "false"


ADVISOR_HANDOFF_DEMO = demo_flag(_get("ADVISOR_HANDOFF_DEMO"))

# Feature flag: require registration before send-to-advisor.
REQUIRE_REGISTRATION_BEFORE_HANDOFF = (
    _get("REQUIRE_REGISTRATION_BEFORE_HANDOFF", "true").lower() == "true"
)

# --- AI Phase 0 guards (AA-805) ---
# Daily hard stop on TripPlanner model spend (the customer-facing stages together, UTC day), read from the shared
# ledger through catalog.v_llm_app_spend_today_v1 (CIS 323; tripplanner.llm_spend_daily, tp-010, is no longer
# written since AA-1027 E5). Infra sets it explicitly on both Lambdas (AA-1015); the code
# default is the same 5.0. When reached, narration falls back to AA text + neutral day lines and free-text search
# to the keyword ranking (AA-642 S0).
TP_DAILY_SPEND_CAP_USD = float(_get("TP_DAILY_SPEND_CAP_USD", "5.0"))


def _optional_usd(name: str) -> Optional[float]:
    raw = (_get(name, "") or "").strip()
    return float(raw) if raw else None


# AA-1015: separate daily caps per stage, so search and narration cannot use each other's share. Unset (the default)
# = no stage cap: only the combined cap above applies, as before. Both caps apply when a stage cap is set.
TP_DAILY_SPEND_CAP_SEARCH_USD = _optional_usd("TP_DAILY_SPEND_CAP_SEARCH_USD")
TP_DAILY_SPEND_CAP_COMPOSE_USD = _optional_usd("TP_DAILY_SPEND_CAP_COMPOSE_USD")
# AA-1015: share of a cap at which the gateway prints the "near cap" alert line (and budget_status says near_cap).
SPEND_NEAR_CAP_RATIO = 0.8

# --- Route deadline (AA-642 S263 E1) ---
# Wall-time budget of one text-generation request across every model / account of its route
# (llm_gateway.generate_text). The route's own `route_deadline_s` (CIS migration 320) is used when the config view
# carries it; otherwise the default. Either way it is capped at the MAX: the API Gateway HTTP API answers 504 after
# 30 s, so a slow attempt must leave time to degrade to the basic narration and write the ledger rows.
LLM_ROUTE_DEADLINE_DEFAULT_S = 20.0
LLM_ROUTE_DEADLINE_MAX_S = 25.0
# Below this many seconds left no new attempt starts (the request's 'deadline' ledger row is written instead).
LLM_ROUTE_DEADLINE_MIN_ATTEMPT_S = 1.0

# --- Stage x model request parameters (AA-1027 E4) ---
# Kill switch for llm_gateway.request_params (the shared.stage_model_params row of a stage and model, read through
# the CIS config view, CIS 350): "off" sends none of them (the request bodies of before E4). Same name as the CIS
# gateway's switch. Unset = on; with no params row (Dev today) nothing changes either way.
LLM_STAGE_PARAMS = (_get("LLM_STAGE_PARAMS", "on") or "on").strip().lower() not in ("off", "0", "false", "no")

# --- AI route limits (AA-1015, migration tp-023) ---
# Fixed windows counted in tripplanner.ai_rate_counter (backend/shared/ratelimit.py). Capacity plan S261 §2.5:
# 10 narrations per hour per session, 30 searches per minute per traveller IP, a signed-in traveller gets
# TP_RATE_SIGNED_IN_FACTOR times that. Only requests that would call a model are counted (a cached narration, an
# all-AA-text narration or an empty query is not).
TP_RATE_NARRATE_PER_HOUR = int(_get("TP_RATE_NARRATE_PER_HOUR", "10"))
TP_RATE_SEARCH_PER_MINUTE = int(_get("TP_RATE_SEARCH_PER_MINUTE", "30"))
TP_RATE_SIGNED_IN_FACTOR = int(_get("TP_RATE_SIGNED_IN_FACTOR", "2"))
# AA-1038 slice 3: "Send to advisor" on an agency tenant's planner (a lead with the traveller's contact), per hour: per
# guest session and per traveller IP (signed-in factor as above). Counted only when the request would store a lead.
TP_RATE_SEND_PER_HOUR = int(_get("TP_RATE_SEND_PER_HOUR", "5"))
TP_RATE_SEND_IP_PER_HOUR = int(_get("TP_RATE_SEND_IP_PER_HOUR", "20"))
# AA-1041 slice 2: the planning chat (POST /trip/{id}/chat, stage tp_plan_chat), per hour: messages per guest session
# and per traveller IP (signed-in factor as above). Counted only when the message would reach a model.
TP_RATE_CHAT_PER_HOUR = int(_get("TP_RATE_CHAT_PER_HOUR", "20"))
TP_RATE_CHAT_IP_PER_HOUR = int(_get("TP_RATE_CHAT_IP_PER_HOUR", "40"))

# --- Planning chat money caps (AA-1041 slice 2, migration tp-029) ---
# Per UTC month, from tripplanner.ai_spend_month (backend/shared/monthly_spend.py): the whole app's chat spend and each
# tenant's own (the AA demo tenant included). Tech lead, S277: 2 USD a month on Dev. At a cap the chat pauses with a
# plain message; the rest of the planner is untouched. The daily app cap and an agency tenant's AI budget
# (llm_gateway.budget_status) apply too.
TP_PLAN_CHAT_MONTHLY_CAP_USD = float(_get("TP_PLAN_CHAT_MONTHLY_CAP_USD", "2.0"))
TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD = float(_get("TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD", "2.0"))
# Sent by the BFF (web task) with the traveller's address and whether a traveller session is signed in. Trusted ONLY
# when the request also carries the right x-tripplanner-key (only the BFF has it); otherwise ignored.
CLIENT_IP_HEADER = "x-tp-client-ip"
SIGNED_IN_HEADER = "x-tp-signed-in"

# How long a narration in tripplanner.narration_cache is reused for the same itinerary.
NARRATION_CACHE_TTL_HOURS = int(_get("NARRATION_CACHE_TTL_HOURS", "168"))

# --- Admin-editable settings (S279 thresholds lane L5, AA-1096) ---
# The rate limits, the planning chat caps, the lead cap and hold, the narration cache TTL, the max_tokens and steps
# below and the two hand-off switches are read through backend/shared/settings.py from the CIS view
# catalog.v_app_settings_v1 (edited on the CIS admin page Platform > Settings). The values in this module (the env var
# when set, else the code default) are only its fallback while the view cannot be read: one release, then the env vars
# go (Infra lane L6). The three below had no env var: they are code defaults only.
NARRATION_MAX_TOKENS = 1200  # technical-constant: fallback of tripplanner.ai.narration_max_tokens
PLAN_CHAT_MAX_STEPS = 3  # technical-constant: fallback of tripplanner.ai.plan_chat_max_steps
PLAN_CHAT_MAX_TOKENS = 700  # technical-constant: fallback of tripplanner.ai.plan_chat_max_tokens
