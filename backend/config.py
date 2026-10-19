"""Central configuration for the AA-TripPlanner backend.

All environment-driven values live here so nothing is hardcoded inline
across modules. In production the DB connection string comes from AWS
Secrets Manager (acc2); locally it comes from the environment / .env.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


# --- Database (shared RDS instance, acc2, us-west-1) ---
# Full DSN, e.g. postgresql://user:pass@host:5432/dbname
DATABASE_URL = _get("TRIPPLANNER_DATABASE_URL")

# Schemas this service touches. tripplanner is owned (user state written here;
# the place tables are frozen, read-only since AA-712 slice 6a). In shared it
# reads destinations / photo views / gateway config and writes llm_call_log only.
SCHEMA_TRIPPLANNER = "tripplanner"
SCHEMA_SHARED = "shared"

# CIS source schemas this service READS (never writes).
SCHEMA_GOLD = "gold_aa_internal"
SCHEMA_ACP = "acp_contract"

# --- Place / tour-graph read source (AA-712 slice 6b, ADR 0007) ---
# "legacy" = the frozen TripPlanner tables (tripplanner.itinerary_components, tour_stop, tour_leg,
# tour_junction + shared.destinations); "cis" = the CIS catalog views of migration 228
# (catalog.v_tour_component, v_tour_stop, v_tour_leg, v_tour_junction, v_destination). Only the
# browse readers follow it in slice 6b; the assembly readers switch with the tp-013 id-map (slice 6c),
# so set it to "cis" on both Lambdas together, in that window. Unknown values read as "legacy".
PLACES_SOURCE = _get("PLACES_SOURCE", "legacy")

# --- CIS public API (AA-812) ---
# Base URL that catalog photo paths are relative to: {base}{path}?size=large|small (CIS
# migration 200). Browse responses carry absolute photo URLs built from it.
CIS_PUBLIC_API_BASE = _get("CIS_PUBLIC_API_BASE", "https://api-cis.lumiguides.it.com")

# --- Bedrock ---
# Region for all Bedrock calls. Verified live: acc2/acc3/acc1 all us-west-1.
BEDROCK_REGION = _get("BEDROCK_REGION", "us-west-1")

# LLM (compose/renarrate) — Claude via the satellite pattern. acc2
# (where the Lambdas run) is a channel-program account and CANNOT invoke Claude
# ("Access to this model is not available for channel program accounts"),
# so the app assumes a cross-account role: acc3 primary, acc1 fallback —
# same as AA-CIS-App/ACPv2. Verified live on acc1: us.anthropic.claude-
# sonnet-4-6 invokes OK. Models MUST be called by inference-profile id
# (the bare foundation-model id fails with ValidationException).
# Satellite account IDs come from the Lambda environment (AA-CIS-Infra tripplanner.tf); this repo
# is public, so no account ID is written here (AA-902). Unset -> that satellite is skipped with a
# clear error in the failover log (llm_gateway._satellite_target), acc2 and the other one still run.
BEDROCK_ACCT_PRIMARY = _get("BEDROCK_ACCT_PRIMARY", "")   # acc3
BEDROCK_ACCT_FALLBACK = _get("BEDROCK_ACCT_FALLBACK", "")  # acc1
# The satellite invoker role has a DIFFERENT name on each account:
#   acc3 (primary)  -> AA3-Bedrock-Invoker
#   acc1 (fallback) -> AA-Bedrock-Invoker
# so a single role name cannot cover both. BEDROCK_ROLE_NAME is kept only as
# a legacy fallback default if a per-account name is not set.
BEDROCK_ROLE_NAME = _get("BEDROCK_ROLE_NAME", "")  # legacy single-role fallback
BEDROCK_ROLE_NAME_PRIMARY = _get("BEDROCK_ROLE_NAME_PRIMARY", "AA3-Bedrock-Invoker")
BEDROCK_ROLE_NAME_FALLBACK = _get("BEDROCK_ROLE_NAME_FALLBACK", "AA-Bedrock-Invoker")
BEDROCK_EXTERNAL_ID_PRIMARY = _get("BEDROCK_EXTERNAL_ID_PRIMARY", "aa296-satellite-bedrock-acc3")
BEDROCK_EXTERNAL_ID_FALLBACK = _get("BEDROCK_EXTERNAL_ID_FALLBACK", "aa296-satellite-bedrock")
# AA-685: which model each call uses (and its Bedrock profile per account) comes from the Model
# Gateway tables in the shared RDS — stages tp_compose / tp_extract (Claude via the satellite
# roles) and tp_search_embed / tp_component_embed (Cohere Embed v4: acc2 first, then satellite
# rows by route, AA-862 — the embed fall-through reuses the BEDROCK_ACCT_* / ROLE_NAME_* /
# EXTERNAL_ID_* values above; the account IDs must be set on both Lambdas). See
# backend/shared/llm_gateway.py. Embeddings must stay 1536-dim to match VECTOR(1536).
EMBED_DIM = 1536

# --- Advisor notification (log label only) ---
# TripPlanner sends no e-mail: AA-Booking e-mails staff about a new inquiry (bk_notify_email, AA-811).
# Optional label for the hand-off log line in assembly/notify.py; no personal address as a default.
ADVISOR_NOTIFY_EMAIL = _get("ADVISOR_NOTIFY_EMAIL", "")

# --- Master-content tenant (RLS on the shared gold schema) ---
# gold_aa_internal.published_tours enforces row-level security by tenant
# (policy: tenant_id = current_setting('app.tenant_id')). To read AA's
# authored itinerary text the app sets this tenant id (SET LOCAL) before
# querying. All active AA tours belong to this single tenant. Empty ->
# skip setting it (local/tests), in which case RLS yields no rows and
# narration falls back to the LLM.
MASTER_CONTENT_TENANT_ID = _get(
    "MASTER_CONTENT_TENANT_ID", "00000000-0000-0000-0000-000000000001"
)

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

# --- Guest credential (AA-827) ---
# The BFF holds a random token in an HttpOnly cookie and forwards it in this header on every
# mutating call; the session row stores only sha256(token) (migration tp-011). session_id is an
# identifier, never a credential. See assembly/guest_session.py.
GUEST_TOKEN_HEADER = "x-tripplanner-guest"
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

# Feature flag: require registration before send-to-advisor.
REQUIRE_REGISTRATION_BEFORE_HANDOFF = (
    _get("REQUIRE_REGISTRATION_BEFORE_HANDOFF", "true").lower() == "true"
)

# --- AI Phase 0 guards (AA-805) ---
# Daily hard stop on TripPlanner model spend (all tp_* stages together, UTC day), counted in
# tripplanner.llm_spend_daily (migration tp-010). PLACEHOLDER default — the business cap is an
# open question for Nghiệp; override with the env var on both Lambdas. When reached, narration
# falls back to AA text + neutral day lines and free-text search answers 503.
TP_DAILY_SPEND_CAP_USD = float(_get("TP_DAILY_SPEND_CAP_USD", "5.0"))

# How long a narration in tripplanner.narration_cache is reused for the same itinerary.
NARRATION_CACHE_TTL_HOURS = int(_get("NARRATION_CACHE_TTL_HOURS", "168"))
