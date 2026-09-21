"""Central configuration for the AA-TripPlanner backend.

All environment-driven values live here so nothing is hardcoded inline
across modules. In production the DB connection string comes from AWS
Secrets Manager (acc2); locally it comes from the environment / .env.
"""
from __future__ import annotations

import os


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


# --- Database (shared RDS instance, acc2, us-west-1) ---
# Full DSN, e.g. postgresql://user:pass@host:5432/dbname
DATABASE_URL = _get("TRIPPLANNER_DATABASE_URL")

# Schemas this service touches. tripplanner is owned; shared is the one
# deliberate cross-program exception (shared.destinations only).
SCHEMA_TRIPPLANNER = "tripplanner"
SCHEMA_SHARED = "shared"

# Source schemas the extraction pipeline READS (never writes).
SCHEMA_GOLD = "gold_aa_internal"
SCHEMA_ACP = "acp_contract"

# --- Mapbox ---
MAPBOX_GEOCODING_TOKEN = _get("MAPBOX_GEOCODING_TOKEN")
MAPBOX_GEOCODING_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places"

# --- Bedrock ---
# Region for all Bedrock calls. Verified live: acc2/acc3/acc1 all us-west-1.
BEDROCK_REGION = _get("BEDROCK_REGION", "us-west-1")

# LLM (compose/renarrate) — Claude via the satellite pattern. acc2
# (005097885195) is a channel-program account and CANNOT invoke Claude
# ("Access to this model is not available for channel program accounts"),
# so the app assumes a cross-account role: acc3 primary, acc1 fallback —
# same as AA-CIS-App/ACPv2. Verified live on acc1: us.anthropic.claude-
# sonnet-4-6 invokes OK. Models MUST be called by inference-profile id
# (the bare foundation-model id fails with ValidationException).
BEDROCK_ACCT_PRIMARY = _get("BEDROCK_ACCT_PRIMARY", "786888028788")   # acc3
BEDROCK_ACCT_FALLBACK = _get("BEDROCK_ACCT_FALLBACK", "867490540162")  # acc1
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
# The satellite invoker roles (acc3 AA3-Bedrock-Invoker / acc1
# AA-Bedrock-Invoker) grant InvokeModel on the GLOBAL cross-region profile
# `global.anthropic.claude-sonnet-4-6` — NOT the `us.` profile. Verified
# live against AA-Bedrock-Invoker's InvokeApprovedClaudeModelsOnly policy
# (AA-CIS-Infra accounts/acc1-bedrock/bedrock_invoker_import.tf). Using the
# `us.` id fails with AccessDenied on the inference-profile ARN.
BEDROCK_MODEL_COMPOSE = _get("BEDROCK_MODEL_COMPOSE", "global.anthropic.claude-sonnet-4-6")

# Embedding — Cohere Embed v4 via a DIRECT Bedrock call on acc2 (no
# satellite: acc2 CAN invoke Cohere embeddings). Verified live:
# us.cohere.embed-v4:0 returns 1536-dim vectors (matches VECTOR(1536)).
# Called by inference-profile id (bare id fails on-demand).
BEDROCK_MODEL_EMBED = _get("BEDROCK_MODEL_EMBED", "us.cohere.embed-v4:0")
EMBED_DIM = 1536

# --- Advisor notification (single config value, never inline) ---
ADVISOR_NOTIFY_EMAIL = _get("ADVISOR_NOTIFY_EMAIL", "pqnghiep1354@gmail.com")

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
# A shared secret the BFF must send in the X-TripPlanner-Key header. When
# unset/empty (local dev, unit tests) the check is DISABLED — so nothing
# breaks without it. In production the Lambda env sets it from Secrets
# Manager and the BFF sends the same value. See shared/auth.py.
TRIPPLANNER_API_KEY = _get("TRIPPLANNER_API_KEY", "")
API_KEY_HEADER = "x-tripplanner-key"

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
