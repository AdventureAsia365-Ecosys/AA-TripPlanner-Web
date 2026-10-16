"""Thin client for the ecosystem Model Gateway (AA-685).

AA-CIS-App owns the gateway tables in the shared RDS:
  shared.llm_role_config   stage -> model, fallback models, preferred account
  shared.llm_model_catalog model -> Bedrock profile per account, API style, price
  shared.llm_call_log      one row per model call (cost reporting, External Spend page)

This module reads the first two for a stage, calls Bedrock itself (no dependency on the CIS API,
which is often scaled to zero), and writes the log row. The `tripplanner` DB role has SELECT on
the two config tables and INSERT on the log (CIS migration 173).

Accounts: acc2 is where the Lambdas run (direct call, used for Cohere embeddings); acc3 and acc1
are reached through the satellite roles in `bedrock_satellite`. Anthropic models are not
available on acc2 (channel-program account), so an acc2 attempt for them simply fails over.

If the tables cannot be read, BUILTIN_ROUTES keeps the models this app used before AA-685, so a
DB problem never stops narration or search.

AA-805 (fork defects vs. the CIS client):
  - Failed attempts are logged too: `generate_text` / `embed_texts` append one failed `Call` per
    model x account attempt to `failures`, and `record()` writes it with stop_reason 'error'.
  - A missing catalog price is NULL, never 0: the call logs cost_usd NULL and prints a warning.
  - Shadow config (`shadow_model_id` / `shadow_sample_pct`) is read but NOT run here: a shadow
    would double the spend of a consumer app and needs INSERT on shared.llm_shadow_log, which the
    `tripplanner` role does not have. `load_route` prints a warning when one is configured.
  - Daily spend hard stop: `record()` adds each customer-facing call (ONLINE_STAGES: tp_compose,
    tp_search_embed) to tripplanner.llm_spend_daily (migration tp-010); `budget_status()` compares
    today's total of those stages with TP_DAILY_SPEND_CAP_USD. Offline stages (tp_extract,
    tp_component_embed — extraction and embedding backfills) are logged in shared.llm_call_log but
    neither counted nor capped here, so a backfill can never take customer search/narration down.

AA-856: `EmbeddingCache` / `query_embed_cache` keep query embeddings in the Lambda container
(bounded LRU + TTL, keyed by stage, route models, input type and the normalised text). A cache hit
is not a model call: no llm_call_log row, no spend.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend import config
from backend.shared import bedrock_satellite
from backend.shared.bedrock_satellite import BedrockError, ClientFactory

APP = "tripplanner"
_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class Model:
    key: str
    vendor: str
    api_style: str
    profiles: dict
    price_in: Optional[float] = None
    price_out: Optional[float] = None
    price_cache_read: Optional[float] = None
    price_cache_write: Optional[float] = None
    enabled: bool = True


@dataclass(frozen=True)
class Route:
    stage: str
    role: str
    models: tuple
    account_route: Optional[str] = None
    shadow_model: Optional[str] = None


@dataclass
class Call:
    """One model call attempt, ready for `record()`. `failed` attempts carry the error in
    `extra["error"]`; `cost_usd` is None when the catalog has no price for the model."""
    stage: str
    role: str
    model: str
    account: str
    tokens_in: int
    tokens_out: int
    cost_usd: Optional[float]
    fallback_used: bool
    stop_reason: Optional[str] = None
    extra: dict = field(default_factory=dict)
    failed: bool = False

    @property
    def provider(self) -> str:
        return "bedrock-native" if self.account == "acc2" else "bedrock-satellite"


_SONNET = Model("sonnet", "anthropic", "anthropic_native",
                {"acc3": "global.anthropic.claude-sonnet-4-6",
                 "acc1": "global.anthropic.claude-sonnet-4-6"}, 3.0, 15.0)
_COHERE = Model("cohere-embed-v4", "cohere", "embed", {"acc2": "us.cohere.embed-v4:0"}, 0.12, 0.0)

BUILTIN_ROUTES = {
    "tp_compose": Route("tp_compose", "writer", (_SONNET,), "acc3"),
    "tp_extract": Route("tp_extract", "writer", (_SONNET,), "acc3"),
    "tp_search_embed": Route("tp_search_embed", "embed", (_COHERE,)),
    "tp_component_embed": Route("tp_component_embed", "embed", (_COHERE,)),
}

_cache: dict[str, tuple[Route, float]] = {}


def builtin_route(stage: str) -> Route:
    try:
        return BUILTIN_ROUTES[stage]
    except KeyError:
        raise BedrockError(f"Unknown stage {stage!r}") from None


def _num(v: Any) -> Optional[float]:
    return float(v) if v is not None else None


async def load_route(db: Any, stage: str) -> Route:
    """`db` is an asyncpg pool or connection. Never raises for a known stage."""
    cached = _cache.get(stage)
    now = time.monotonic()
    if cached and now - cached[1] < _CACHE_TTL_SECONDS:
        return cached[0]
    try:
        rows = await db.fetch(
            "SELECT role, model_id, fallback_model_ids, account_route, shadow_model_id, "
            "shadow_sample_pct FROM shared.llm_role_config WHERE stage = $1 AND is_active", stage)
        if not rows:
            raise LookupError(f"no active llm_role_config row for {stage!r}")
        cfg = rows[0]
        keys = [cfg["model_id"], *(cfg["fallback_model_ids"] or [])]
        cat = await db.fetch(
            "SELECT model_key, vendor, api_style, bedrock_profile_ids, price_in_per_mtok, "
            "price_out_per_mtok, price_cache_read_per_mtok, price_cache_write_per_mtok, enabled "
            "FROM shared.llm_model_catalog WHERE model_key = ANY($1::text[])", keys)
        by_key = {}
        for r in cat:
            profiles = r["bedrock_profile_ids"]
            if isinstance(profiles, str):
                profiles = json.loads(profiles)
            by_key[r["model_key"]] = Model(
                r["model_key"], r["vendor"], r["api_style"], dict(profiles or {}),
                _num(r["price_in_per_mtok"]), _num(r["price_out_per_mtok"]),
                _num(r["price_cache_read_per_mtok"]), _num(r["price_cache_write_per_mtok"]),
                bool(r["enabled"]))
        models = tuple(by_key[k] for k in keys if k in by_key)
        if not models:
            raise LookupError(f"none of {keys} is in llm_model_catalog")
        shadow = cfg.get("shadow_model_id") if (cfg.get("shadow_sample_pct") or 0) > 0 else None
        if shadow:
            print(f"[llm_gateway] {stage}: shadow {shadow!r} is configured but TripPlanner does not "
                  "run shadows (AA-805); only the primary route is called")
        route = Route(stage, cfg["role"], models, cfg["account_route"], shadow)
    except Exception as e:  # noqa: BLE001 — a DB problem must not stop the call
        print(f"[llm_gateway] route for {stage} unreadable ({e}); using builtin")
        route = builtin_route(stage)
    _cache[stage] = (route, now)
    return route


def _accounts(model: Model, preferred: Optional[str]) -> list[str]:
    order = [a for a in (preferred, "acc3", "acc1", "acc2") if a]
    return [a for a in dict.fromkeys(order) if a in model.profiles]


def _runtime(account: str, client_factory: ClientFactory) -> Any:
    if account == "acc2":
        return client_factory("bedrock-runtime", region=config.BEDROCK_REGION)
    if account == "acc3":
        target = (config.BEDROCK_ACCT_PRIMARY, config.BEDROCK_ROLE_NAME_PRIMARY,
                  config.BEDROCK_EXTERNAL_ID_PRIMARY)
    elif account == "acc1":
        target = (config.BEDROCK_ACCT_FALLBACK, config.BEDROCK_ROLE_NAME_FALLBACK,
                  config.BEDROCK_EXTERNAL_ID_FALLBACK)
    else:
        raise BedrockError(f"Unknown account {account!r}")
    return bedrock_satellite.bedrock_for_account(*target, client_factory)


def _cost(model: Model, tin: int, tout: int, cache_read: int = 0,
          cache_write: int = 0) -> Optional[float]:
    """USD for one call, or None when a price this call needs is missing from the catalog.
    A price is needed only when its token count is non-zero (an embedding has no output price)."""
    price_in, price_out = model.price_in, model.price_out
    if price_in is None and (tin or cache_read or cache_write):
        print(f"[llm_gateway] {model.key}: no input price in llm_model_catalog; cost logged as NULL")
        return None
    if price_out is None and tout:
        print(f"[llm_gateway] {model.key}: no output price in llm_model_catalog; cost logged as NULL")
        return None
    price_in, price_out = price_in or 0.0, price_out or 0.0
    cr = model.price_cache_read if model.price_cache_read is not None else price_in * 0.1
    cw = model.price_cache_write if model.price_cache_write is not None else price_in * 1.25
    total = tin * price_in + tout * price_out + cache_read * cr + cache_write * cw
    return round(total / 1_000_000, 6)


def _failed(route: Route, model: Model, account: str, position: int, error: str) -> Call:
    """A failed attempt: nothing billed, the error kept for the log row."""
    return Call(route.stage, route.role, model.key, account, 0, 0, 0.0, fallback_used=position > 0,
                stop_reason="error", extra={"error": error[:500]}, failed=True)


def _read_body(resp: dict) -> dict:
    raw = resp["body"].read() if hasattr(resp["body"], "read") else resp["body"]
    return json.loads(raw)


def _text_call(model: Model, account: str, system: str, user: str, max_tokens: int,
               client_factory: ClientFactory) -> tuple[str, dict, Optional[str]]:
    rt = _runtime(account, client_factory)
    profile = model.profiles[account]
    if model.api_style == "anthropic_native":
        body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": max_tokens,
                "system": system, "messages": [{"role": "user", "content": user}]}
        payload = _read_body(rt.invoke_model(modelId=profile, body=json.dumps(body)))
        text = "".join(p.get("text", "") for p in payload.get("content", [])
                       if isinstance(p, dict) and p.get("type") == "text")
        u = payload.get("usage", {}) or {}
        usage = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                 "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                 "cache_write": u.get("cache_creation_input_tokens", 0) or 0}
        return text, usage, payload.get("stop_reason")
    if model.api_style == "converse":
        resp = rt.converse(modelId=profile, system=[{"text": system}],
                           messages=[{"role": "user", "content": [{"text": user}]}],
                           inferenceConfig={"maxTokens": max_tokens})
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        u = resp.get("usage", {}) or {}
        usage = {"in": u.get("inputTokens", 0), "out": u.get("outputTokens", 0),
                 "cache_read": u.get("cacheReadInputTokens", 0) or 0,
                 "cache_write": u.get("cacheWriteInputTokens", 0) or 0}
        return text, usage, resp.get("stopReason")
    raise BedrockError(f"{model.key}: api_style {model.api_style!r} is not supported here")


def generate_text(route: Route, system: str, user: str, max_tokens: int, *,
                  client_factory: ClientFactory = bedrock_satellite.default_client_factory,
                  failures: Optional[list] = None,
                  ) -> tuple[str, Call]:
    """Runs the route: each enabled model in order, each of its accounts in order.
    Every failed attempt is appended to `failures` (when given) so the caller logs it too."""
    errors = []
    for position, model in enumerate(route.models):
        if not model.enabled or model.api_style == "embed":
            errors.append(f"{model.key}: skipped")
            continue
        for account in _accounts(model, route.account_route):
            try:
                text, usage, stop = _text_call(model, account, system, user, max_tokens,
                                               client_factory)
            except Exception as e:  # noqa: BLE001 — fail over to the next account/model
                errors.append(f"{model.key}@{account}: {str(e)[:200]}")
                if failures is not None:
                    failures.append(_failed(route, model, account, position, str(e)))
                continue
            if not text:
                errors.append(f"{model.key}@{account}: empty response")
                if failures is not None:
                    failures.append(_failed(route, model, account, position, "empty response"))
                continue
            return text, Call(
                route.stage, route.role, model.key, account, usage["in"], usage["out"],
                _cost(model, usage["in"], usage["out"], usage["cache_read"], usage["cache_write"]),
                fallback_used=position > 0, stop_reason=stop)
    raise BedrockError(f"All models failed for stage {route.stage!r}: " + "; ".join(errors))


_acc2_embed: Any = None


def _acc2_embed_client() -> Any:
    """AA-856: one acc2 bedrock-runtime client per container for embeddings, with NO retry
    (max_attempts=1, as CIS AA-610): a throttle retried at once spends the same minute's quota
    again and holds the search request for seconds; search falls back to keyword ranking instead."""
    global _acc2_embed
    if _acc2_embed is None:
        import boto3  # lazily, so tests never need boto3
        from botocore.config import Config

        _acc2_embed = boto3.client(
            "bedrock-runtime", region_name=config.BEDROCK_REGION,
            config=Config(connect_timeout=5, read_timeout=10,
                          retries={"max_attempts": 1, "mode": "standard"}))
    return _acc2_embed


def embed_texts(route: Route, texts: list[str], *, input_type: str = "search_document",
                client_factory: ClientFactory = bedrock_satellite.default_client_factory,
                failures: Optional[list] = None,
                ) -> tuple[list[list[float]], Call]:
    """Cohere Embed v4 request shape (the only embedding vendor in the catalog)."""
    errors = []
    for position, model in enumerate(route.models):
        if not model.enabled or model.api_style != "embed" or model.vendor != "cohere":
            errors.append(f"{model.key}: skipped")
            continue
        for account in _accounts(model, route.account_route):
            try:
                rt = (_acc2_embed_client() if account == "acc2"
                      and client_factory is bedrock_satellite.default_client_factory
                      else _runtime(account, client_factory))
                resp = rt.invoke_model(modelId=model.profiles[account], body=json.dumps(
                    {"texts": texts, "input_type": input_type, "embedding_types": ["float"],
                     "output_dimension": config.EMBED_DIM}))
                emb = _read_body(resp).get("embeddings")
                vectors = emb.get("float") if isinstance(emb, dict) else emb
                if (not isinstance(vectors, list) or len(vectors) != len(texts)
                        or any(len(v) != config.EMBED_DIM for v in vectors)):
                    raise BedrockError("malformed embedding response")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{model.key}@{account}: {str(e)[:200]}")
                if failures is not None:
                    failures.append(_failed(route, model, account, position, str(e)))
                continue
            header = (resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                      .get("x-amzn-bedrock-input-token-count"))
            tokens = int(header) if header is not None else sum(max(1, len(t) // 4) for t in texts)
            return [[float(x) for x in v] for v in vectors], Call(
                route.stage, route.role, model.key, account, tokens, 0, _cost(model, tokens, 0),
                fallback_used=position > 0, extra={"tokens_estimated": header is None})
    raise BedrockError(f"All models failed for stage {route.stage!r}: " + "; ".join(errors))


# --- Query embedding cache (AA-856) -------------------------------------------------------------
#
# Cohere Embed V4 on acc2 allows 20 requests per minute (Service Quotas, S239), so every repeated
# search query must not call Bedrock again. The cache lives in the Lambda container (one instance
# serves one invocation at a time, so no locking): bounded LRU with a TTL. A hit is not a model
# call: no llm_call_log row, nothing added to the daily spend.

EMBED_CACHE_MAX_ENTRIES = 1024      # ~12 KB per 1536-d vector stored as array('d') -> ~12 MB max
EMBED_CACHE_TTL_SECONDS = 6 * 3600  # the key names the model; the TTL only bounds staleness

_WS_RE = re.compile(r"\s+")


def normalise_query(text: str) -> str:
    """Cache key text and the text that is embedded: NFKC, case-folded, whitespace collapsed."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text or "")).strip().casefold()


def embed_cache_key(route: Route, input_type: str, text: str) -> tuple:
    """(stage, model keys of the route, input_type, dimension, normalised text). A model change on
    the admin Settings page gives new keys, so a cached vector never crosses models."""
    return (route.stage, tuple(m.key for m in route.models), input_type, config.EMBED_DIM,
            normalise_query(text))


class EmbeddingCache:
    """Bounded LRU of embedding vectors with a per-entry TTL. Counts hits and misses for the
    per-search log line (cache hit rate in CloudWatch)."""

    def __init__(self, max_entries: int = EMBED_CACHE_MAX_ENTRIES,
                 ttl_seconds: float = EMBED_CACHE_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._data: "OrderedDict[tuple, tuple[array, float]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> Optional[list[float]]:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        vec, stored_at = entry
        if self._clock() - stored_at >= self.ttl_seconds:
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return list(vec)

    def put(self, key: tuple, vec: list[float]) -> None:
        self._data[key] = (array("d", vec), self._clock())
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._data)

    def stats(self) -> dict:
        return {"cache_hits": self.hits, "cache_misses": self.misses, "cache_size": len(self._data)}


query_embed_cache = EmbeddingCache()


def is_throttle(error: str) -> bool:
    """True for a Bedrock throttle (ThrottlingException / 'Too many requests') in an error text."""
    low = (error or "").lower()
    return "throttl" in low or "too many requests" in low


async def record(db: Any, call: Call, quality_signal: dict, *,
                 tenant_id: Optional[str] = None) -> None:
    """Writes the shared.llm_call_log row (and, for ONLINE_STAGES, today's spend counter).
    Never raises: a lost log row must not fail a request."""
    signal = {"app": APP, **call.extra, **quality_signal}
    if call.failed:
        signal["failed"] = True
    try:
        await db.execute(
            "INSERT INTO shared.llm_call_log (tenant_id, stage, role, model, tokens_in, tokens_out, "
            "cost_usd, quality_signal, stop_reason, account, fallback_used, provider) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)",
            tenant_id, call.stage, call.role, call.model, call.tokens_in, call.tokens_out,
            call.cost_usd, json.dumps(signal), call.stop_reason, call.account,
            call.fallback_used, call.provider)
    except Exception as e:  # noqa: BLE001
        print(f"[llm_gateway] llm_call_log write failed for {call.stage}: {e}")
    if call.stage in ONLINE_STAGES:
        await _add_spend(db, call)


# --- Daily spend hard stop (AA-805) -------------------------------------------------------------

# Customer-facing stages: the only ones counted against, and stopped by, the daily cap.
ONLINE_STAGES: tuple[str, ...] = ("tp_compose", "tp_search_embed")

_SPEND_UPSERT_SQL = """
    INSERT INTO tripplanner.llm_spend_daily (day, stage, cost_usd, calls, unpriced_calls)
    VALUES ((now() AT TIME ZONE 'UTC')::date, $1, $2, 1, $3)
    ON CONFLICT (day, stage) DO UPDATE
       SET cost_usd = tripplanner.llm_spend_daily.cost_usd + EXCLUDED.cost_usd,
           calls = tripplanner.llm_spend_daily.calls + 1,
           unpriced_calls = tripplanner.llm_spend_daily.unpriced_calls + EXCLUDED.unpriced_calls,
           updated_at = now()
"""

_SPEND_TODAY_SQL = """
    SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM tripplanner.llm_spend_daily
    WHERE day = (now() AT TIME ZONE 'UTC')::date AND stage = ANY($1::text[])
"""


async def _add_spend(db: Any, call: Call) -> None:
    unpriced = 1 if call.cost_usd is None else 0
    try:
        await db.execute(_SPEND_UPSERT_SQL, call.stage, call.cost_usd or 0.0, unpriced)
    except Exception as e:  # noqa: BLE001 — e.g. tp-010 not applied yet
        print(f"[llm_gateway] llm_spend_daily update failed for {call.stage}: {e}")


async def budget_status(db: Any) -> dict:
    """Today's (UTC) customer-facing (ONLINE_STAGES) model spend against TP_DAILY_SPEND_CAP_USD.

    Returns {"spent_usd", "cap_usd", "exhausted"}. Fails OPEN when the counter cannot be read
    (tp-010 not applied, permission): `exhausted` False and `spent_usd` None, with a warning —
    the cap must never take narration down by itself. Calls with no catalog price add nothing."""
    cap = float(config.TP_DAILY_SPEND_CAP_USD)
    try:
        row = await db.fetchrow(_SPEND_TODAY_SQL, list(ONLINE_STAGES))
        spent = float(row["spent"]) if row and row["spent"] is not None else 0.0
    except Exception as e:  # noqa: BLE001
        print(f"[llm_gateway] daily spend unreadable ({e}); TripPlanner spend cap NOT enforced")
        return {"spent_usd": None, "cap_usd": cap, "exhausted": False}
    return {"spent_usd": round(spent, 6), "cap_usd": cap, "exhausted": spent >= cap}
