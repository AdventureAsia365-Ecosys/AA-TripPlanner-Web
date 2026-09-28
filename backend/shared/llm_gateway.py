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
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

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
    price_in: float = 0.0
    price_out: float = 0.0
    price_cache_read: Optional[float] = None
    price_cache_write: Optional[float] = None
    enabled: bool = True


@dataclass(frozen=True)
class Route:
    stage: str
    role: str
    models: tuple
    account_route: Optional[str] = None


@dataclass
class Call:
    """One successful model call, ready for `record()`."""
    stage: str
    role: str
    model: str
    account: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    fallback_used: bool
    stop_reason: Optional[str] = None
    extra: dict = field(default_factory=dict)

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
            "SELECT role, model_id, fallback_model_ids, account_route "
            "FROM shared.llm_role_config WHERE stage = $1 AND is_active", stage)
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
                _num(r["price_in_per_mtok"]) or 0.0, _num(r["price_out_per_mtok"]) or 0.0,
                _num(r["price_cache_read_per_mtok"]), _num(r["price_cache_write_per_mtok"]),
                bool(r["enabled"]))
        models = tuple(by_key[k] for k in keys if k in by_key)
        if not models:
            raise LookupError(f"none of {keys} is in llm_model_catalog")
        route = Route(stage, cfg["role"], models, cfg["account_route"])
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


def _cost(model: Model, tin: int, tout: int, cache_read: int = 0, cache_write: int = 0) -> float:
    cr = model.price_cache_read if model.price_cache_read is not None else model.price_in * 0.1
    cw = model.price_cache_write if model.price_cache_write is not None else model.price_in * 1.25
    total = tin * model.price_in + tout * model.price_out + cache_read * cr + cache_write * cw
    return round(total / 1_000_000, 6)


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
                  ) -> tuple[str, Call]:
    """Runs the route: each enabled model in order, each of its accounts in order."""
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
                continue
            if not text:
                errors.append(f"{model.key}@{account}: empty response")
                continue
            return text, Call(
                route.stage, route.role, model.key, account, usage["in"], usage["out"],
                _cost(model, usage["in"], usage["out"], usage["cache_read"], usage["cache_write"]),
                fallback_used=position > 0, stop_reason=stop)
    raise BedrockError(f"All models failed for stage {route.stage!r}: " + "; ".join(errors))


def embed_texts(route: Route, texts: list[str], *, input_type: str = "search_document",
                client_factory: ClientFactory = bedrock_satellite.default_client_factory,
                ) -> tuple[list[list[float]], Call]:
    """Cohere Embed v4 request shape (the only embedding vendor in the catalog)."""
    errors = []
    for position, model in enumerate(route.models):
        if not model.enabled or model.api_style != "embed" or model.vendor != "cohere":
            errors.append(f"{model.key}: skipped")
            continue
        for account in _accounts(model, route.account_route):
            try:
                rt = _runtime(account, client_factory)
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
                continue
            header = (resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                      .get("x-amzn-bedrock-input-token-count"))
            tokens = int(header) if header is not None else sum(max(1, len(t) // 4) for t in texts)
            return [[float(x) for x in v] for v in vectors], Call(
                route.stage, route.role, model.key, account, tokens, 0, _cost(model, tokens, 0),
                fallback_used=position > 0, extra={"tokens_estimated": header is None})
    raise BedrockError(f"All models failed for stage {route.stage!r}: " + "; ".join(errors))


async def record(db: Any, call: Call, quality_signal: dict, *,
                 tenant_id: Optional[str] = None) -> None:
    """Writes the shared.llm_call_log row. Never raises: a lost log row must not fail a request."""
    signal = {"app": APP, **call.extra, **quality_signal}
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
