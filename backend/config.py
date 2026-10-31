"""Central configuration for the AA-TripPlanner backend.

All environment-driven values live here so nothing is hardcoded inline
across modules. In production the DB connection string comes from AWS
Secrets Manager (acc2); locally it comes from the environment / .env.
"""
from __future__ import annotations

import os
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

# Schemas this service touches. tripplanner is owned (user state written here). In shared it reads
# the gateway config and writes llm_call_log only. CIS content is read only through the catalog views
# (ADR 0001); since AA-712 X4 nothing else of CIS (gold_aa_internal, acp_contract, shared.destinations)
# and no frozen tripplanner place table is read, and the PLACES_SOURCE switch is gone.
SCHEMA_TRIPPLANNER = "tripplanner"
SCHEMA_SHARED = "shared"

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
# Gateway tables in the shared RDS — stages tp_compose / tp_extract (Claude via the satellite
# roles) and tp_search_embed / tp_component_embed (Cohere Embed v4: acc2 first, then satellite
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

# Feature flag: require registration before send-to-advisor.
REQUIRE_REGISTRATION_BEFORE_HANDOFF = (
    _get("REQUIRE_REGISTRATION_BEFORE_HANDOFF", "true").lower() == "true"
)

# --- AI Phase 0 guards (AA-805) ---
# Daily hard stop on TripPlanner model spend (the customer-facing stages together, UTC day), counted in
# tripplanner.llm_spend_daily (migration tp-010). Infra sets it explicitly on both Lambdas (AA-1015); the code
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

# --- AI route limits (AA-1015, migration tp-023) ---
# Fixed windows counted in tripplanner.ai_rate_counter (backend/shared/ratelimit.py). Capacity plan S261 §2.5:
# 10 narrations per hour per session, 30 searches per minute per traveller IP, a signed-in traveller gets
# TP_RATE_SIGNED_IN_FACTOR times that. Only requests that would call a model are counted (a cached narration, an
# all-AA-text narration or an empty query is not).
TP_RATE_NARRATE_PER_HOUR = int(_get("TP_RATE_NARRATE_PER_HOUR", "10"))
TP_RATE_SEARCH_PER_MINUTE = int(_get("TP_RATE_SEARCH_PER_MINUTE", "30"))
TP_RATE_SIGNED_IN_FACTOR = int(_get("TP_RATE_SIGNED_IN_FACTOR", "2"))
# Sent by the BFF (Vercel) with the traveller's address and whether a traveller session is signed in. Trusted ONLY
# when the request also carries the right x-tripplanner-key (only the BFF has it); otherwise ignored.
CLIENT_IP_HEADER = "x-tp-client-ip"
SIGNED_IN_HEADER = "x-tp-signed-in"

# How long a narration in tripplanner.narration_cache is reused for the same itinerary.
NARRATION_CACHE_TTL_HOURS = int(_get("NARRATION_CACHE_TTL_HOURS", "168"))
