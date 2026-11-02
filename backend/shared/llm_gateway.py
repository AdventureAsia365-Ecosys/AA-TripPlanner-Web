"""Thin client for the ecosystem Model Gateway (AA-685).

AA-CIS-App owns the gateway tables in the shared RDS:
  shared.llm_role_config   stage -> model, fallback models, preferred account
  shared.llm_model_catalog model -> Bedrock profile per account, API style, price
  shared.llm_call_log      one row per model call (cost reporting, External Spend page)

This module reads the first two for a stage, calls Bedrock itself (no dependency on the CIS API,
which is often scaled to zero), and writes the log row. The `tripplanner` DB role has SELECT on
the two config tables and INSERT on the log (CIS migration 173).

Deployments (AA-1027 E3, root plan S263; gaps PC-2, TP-2, TP-3): each model of the route is called on its
deployments (`shared.llm_model_deployment`: connection, wire id, priority, price overrides) in priority order, as
`vendor_route.deployment_plan` does in CIS, and `deployment_plan` leaves out what TripPlanner may not or cannot call:
a disabled deployment or connection; a connection whose `served_roles` do not include the stage's role (acc2 never
serves a writer, ADR 0009 decision 14); a connection whose `data_scope` the stage's `allowed_scopes` exclude; a
connection TripPlanner has no way to call (only Bedrock: the Lambda role for a `task_role` connection, the satellite
role from the Lambda env for an `assume_role` connection whose account comes from `BEDROCK_ACCT_PRIMARY`; T-11,
TripPlanner stays on AWS); and a connection or deployment whose circuit is open (`llm_breaker`: the CIS breaker
state from the view, plus this container's own failures). The connection facts and circuit state come from the
`deployment_routing` column CIS migration 341 appends to the view. Before 341 is applied (or on the pre-323 table
read, or for a model with no deployment row) the deployments are the catalog's `bedrock_profile_ids`, mapped to the
two connections TripPlanner has always called (`_LEGACY_CONNECTIONS`: acc3 by the satellite role, acc2 by the Lambda
role; `account_route` first); a profile naming any other account (the retired acc1, AA-998) is never called. A model
whose every usable deployment was skipped by an open circuit is not called: it gets one 0 USD 'refused' row with the
class that opened the circuit and no connection (no provider attempt), and when nothing at all was attempted the
request raises `CircuitOpen` (reason 'circuit_open'), so callers degrade at once.
Cost (AA-1027 E3, root ADR 0009 decision 8): the shared core `aa_llm.pricing.quote` prices each billed attempt from
the catalog prices and the deployment's price overrides, and its `pricing_mode` (override / catalog / estimate) is
the ledger's. A model with no catalog price is charged the core's estimate rates (high on purpose, never 0) and
flagged 'estimate'.
Embeddings (AA-862): acc2 first, then satellite rows by route. acc2's Cohere Embed v4 quota is
20 rpm, so the route chains one catalog row per account (`cohere-embed-v4` acc2 ->
`cohere-embed-v4-acc3`), same model and dimension, so vectors stay comparable. Satellite embed clients are cached per container (one AssumeRole until 5 minutes
before the creds expire) and make a single attempt, like the acc2 one.

AA-642 S0 (root ADR 0009 decisions 5 and 6): the gateway fails CLOSED. When the route tables cannot be
read, `load_route` raises `AiPaused` ("route_unreadable") instead of calling hard-coded models; when the
daily spend counter cannot be read (or a counter write failed in the last SPEND_UNREADABLE_HOLD_SECONDS),
`budget_status` reports `exhausted` with reason "spend_unreadable". Callers then degrade: narration
answers the basic / master_basic text with an `ai_paused` flag, search answers the keyword ranking.
BUILTIN_ROUTES only documents the expected routes now (tests, the online-stage list); no call uses it.

AA-805 (fork defects vs. the CIS client):
  - Failed attempts are logged too: `generate_text` / `embed_texts` append one failed `Call` per
    model x account attempt to `failures`, and `record()` writes it with stop_reason 'error'.
  - A missing catalog price is never 0 (since AA-1027 E3: the core's flagged estimate, see above).
  - Shadow config (`shadow_model_id` / `shadow_sample_pct`) is read but NOT run here: a shadow
    would double the spend of a consumer app and needs INSERT on shared.llm_shadow_log, which the
    `tripplanner` role does not have. `load_route` prints a warning when one is configured.
  - Daily spend hard stop: `record()` adds each customer-facing call (ONLINE_STAGES: tp_compose,
    tp_search_embed) to tripplanner.llm_spend_daily (migration tp-010); `budget_status()` compares
    today's total of those stages with TP_DAILY_SPEND_CAP_USD. The offline stages (tp_extract,
    tp_component_embed) are gone: no caller since AA-712, their routes retired by CIS migration 336 (AA-1024 D8).

AA-642 S8 (root ADR 0009 decision 13): the shared core `backend/aa_llm` (a byte copy of AA-CIS-App
`shared/aa_llm`, pinned by `backend/aa_llm.lock.json`; never edit it here) gives the ledger row, the error
classes and the spend decision. The route is read from the CIS contract view `catalog.v_llm_gateway_config_v1`
and today's spend from `catalog.v_llm_app_spend_today_v1` (CIS migration 323). Until that migration is applied
(the view is missing or not granted: SQLSTATE 42P01 / 42501), each read falls back to the old source
(`shared.llm_role_config` + `shared.llm_model_catalog`, `tripplanner.llm_spend_daily`) and does not try the
view again for VIEW_RETRY_SECONDS. `record()` writes the ledger v2 row (`aa_llm.ledger.LedgerRow`: app,
status, error_class, latency_ms, attempt_no, connection, cache tokens, pricing_mode), or the pre-319 row when
the v2 columns are missing (SQLSTATE 42703). A failed ledger write of an online stage pauses AI like a failed
counter write: the spend view sums the ledger, so a lost row undercounts.

AA-642 S263 E1 (root ADR 0009 decision 6, plan 1.E): `generate_text` treats a reply cut at its token limit
(stop_reason max_tokens / length, `ledger.TRUNCATED_STOP_REASONS`) as a failed attempt: it is billed, so it is logged
with its tokens and cost and status / error_class 'truncated', and the route moves on to the next account or model;
truncated text is never returned. Each request also has a route deadline (`route_deadline_s`: the route's
`route_deadline_s` when the config view carries it, else config.LLM_ROUTE_DEADLINE_DEFAULT_S, capped at
LLM_ROUTE_DEADLINE_MAX_S under the HTTP API's 30 s): every attempt's client gets the time left as its timeout, and
when too little is left the next attempt is not started: one 0 USD row with status 'error', error_class 'deadline'
names it and `RouteDeadlineExceeded` is raised. Callers degrade like any route failure (basic narration).

AA-642 S263 E2: every attempt row also carries `request_id` (the provider's request id: Bedrock ResponseMetadata
RequestId of the reply, or of the error when the provider answered one) and, for text generation, `prompt_hash`
(sha256 of the system prompt sent, as the CIS gateway writes it) and `prompt_version` (the stage's version in the
prompt manifest `backend/shared/prompt_versions.json` when the deployed source still matches it, see
`prompt_registry`; a CI gate fails when a prompt changes without a version bump).

AA-856: `EmbeddingCache` / `query_embed_cache` keep query embeddings in the Lambda container
(bounded LRU + TTL, keyed by stage, route models, input type and the normalised text). A cache hit
is not a model call: no llm_call_log row, no spend.
"""
from __future__ import annotations

import functools
import json
import re
import time
import unicodedata
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from backend import config
from backend.aa_llm import budget as core_budget
from backend.aa_llm import contract, ledger, pricing
from backend.aa_llm import errors as core_errors
from backend.shared import bedrock_satellite, llm_breaker, observability, prompt_registry
from backend.shared.bedrock_satellite import BedrockError, ClientFactory

APP = "tripplanner"
_CACHE_TTL_SECONDS = 60.0

# AA-1027 E3: the connection facts TripPlanner relies on.
KIND_BEDROCK = "bedrock"
AUTH_TASK_ROLE = "task_role"        # the Lambda's own role (acc2)
AUTH_ASSUME_ROLE = "assume_role"    # the satellite role (acc3), its target from the Lambda env
SATELLITE_ACCOUNT_ENV = "BEDROCK_ACCT_PRIMARY"
AWS_ONLY = "aws_only"
_PRICE_OVERRIDES = ("price_in_override", "price_out_override", "price_cache_read_override",
                    "price_cache_write_override", "price_cache_write_1h_override")


@dataclass(frozen=True)
class Deployment:
    """Where one catalog model is called (AA-1027 E3): a `shared.llm_model_deployment` row and the non-secret facts
    of its connection and circuits (CIS 341 `deployment_routing`). `facts` is False when the config source carried no
    connection facts and the connection is not one of `_LEGACY_CONNECTIONS` (nothing is known: never called).
    Circuit times are epoch seconds."""
    connection_key: str
    wire_id: Optional[str]
    priority: int = 0
    enabled: bool = True
    price_overrides: tuple = ()          # ((override field, $/MTok), ...) of the non-NULL overrides
    facts: bool = True
    kind: Optional[str] = None
    auth_mode: Optional[str] = None
    account_id_env: Optional[str] = None
    region: Optional[str] = None
    data_scope: Optional[str] = None
    served_roles: Optional[tuple] = None  # None = serves every role (CIS: NULL served_roles)
    connection_enabled: bool = True
    circuit_open_until: Optional[float] = None
    probe_until: Optional[float] = None
    last_error_class: Optional[str] = None
    connection_circuit_class: Optional[str] = None


# The two connections TripPlanner called before deployments were readable (CIS 304 seeds, AA-998): used only when the
# config source carries no connection facts (CIS 341 not applied, the pre-323 tables, a model with no deployment row).
_LEGACY_CONNECTIONS: dict[str, dict] = {
    "acc3": {"kind": KIND_BEDROCK, "auth_mode": AUTH_ASSUME_ROLE, "account_id_env": SATELLITE_ACCOUNT_ENV,
             "data_scope": AWS_ONLY},
    "acc2": {"kind": KIND_BEDROCK, "auth_mode": AUTH_TASK_ROLE, "data_scope": AWS_ONLY},
}
_LEGACY_ORDER = ("acc3", "acc2")


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
    # AA-1027 E3: the deployments from the config view, by priority; empty = derive them from `profiles`.
    deployments: tuple = ()


@dataclass(frozen=True)
class Route:
    stage: str
    role: str
    models: tuple
    account_route: Optional[str] = None
    shadow_model: Optional[str] = None
    # AA-642 S263 E1: llm_role_config.route_deadline_s (CIS 320) when the config read carries it, else None.
    deadline_s: Optional[int] = None
    # AA-1027 E3: llm_role_config.allowed_scopes (CIS 311); None = no stage policy.
    allowed_scopes: Optional[tuple] = None


@dataclass
class Call:
    """One model call attempt, ready for `record()`. `failed` attempts carry the error in
    `extra["error"]` and its ledger outcome in `status` / `error_class`; `cost_usd` is None when the
    catalog has no price for the model. `attempt_no` counts the attempts of one request from 1,
    `latency_ms` is the provider call's wall time, `wire_id` the profile it called."""
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
    latency_ms: Optional[int] = None
    attempt_no: Optional[int] = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    wire_id: Optional[str] = None
    status: Optional[str] = None
    error_class: Optional[str] = None
    # AA-642 S263 E2: the provider's request id, and the prompt sent (hash of the system prompt, manifest version).
    request_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    prompt_version: Optional[str] = None
    # AA-1027 E3: how the core priced a billed attempt (aa_llm.pricing.quote), and the connection's auth mode.
    pricing_mode: Optional[str] = None
    auth_mode: Optional[str] = None

    @property
    def provider(self) -> Optional[str]:
        """The ledger provider (the values the CIS gateway writes for a Bedrock connection). None for a row with no
        connection (a model not called because of an open circuit)."""
        if self.auth_mode == AUTH_TASK_ROLE:
            return "bedrock-native"
        if self.auth_mode == AUTH_ASSUME_ROLE:
            return "bedrock-satellite"
        if self.account is None:
            return None
        return "bedrock-native" if self.account == "acc2" else "bedrock-satellite"


_SONNET = Model("sonnet", "anthropic", "anthropic_native",
                {"acc3": "global.anthropic.claude-sonnet-4-6"}, 3.0, 15.0)
_COHERE = Model("cohere-embed-v4", "cohere", "embed", {"acc2": "us.cohere.embed-v4:0"}, 0.12, 0.0)

# The two live stages (tp_extract / tp_component_embed retired: no caller since AA-712, CIS 336, AA-1024 D8).
BUILTIN_ROUTES = {
    "tp_compose": Route("tp_compose", "writer", (_SONNET,), "acc3"),
    "tp_search_embed": Route("tp_search_embed", "embed", (_COHERE,)),
}

_cache: dict[str, tuple[Route, float]] = {}


class RouteDeadlineExceeded(BedrockError):
    """AA-642 S263 E1: the route deadline passed before the next model / account could be tried. No provider call
    was made for that attempt; its 0 USD 'deadline' row is in `failures`. `reason` is the degrade code."""

    reason = "route_deadline"


class CircuitOpen(BedrockError):
    """AA-1027 E3: no model of the route was called, because every deployment that could serve it is behind an open
    circuit (`llm_breaker`). The 0 USD 'refused' row of each such model is in `failures`. `reason` is the degrade
    code, `error_class` the class that opened the (first) circuit."""

    reason = "circuit_open"

    def __init__(self, message: str, error_class: str) -> None:
        super().__init__(message)
        self.error_class = error_class


class AiPaused(BedrockError):
    """AI is paused for this request (AA-642 S0): the route config or the spend counter cannot be
    read, so no model is called. `reason` is a short code for the log and the response."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"AI paused ({reason}){': ' + detail if detail else ''}")
        self.reason = reason


def builtin_route(stage: str) -> Route:
    try:
        return BUILTIN_ROUTES[stage]
    except KeyError:
        raise BedrockError(f"Unknown stage {stage!r}") from None


def _num(v: Any) -> Optional[float]:
    return float(v) if v is not None else None


# AA-642 S8: a contract view (CIS 323) that is missing or not granted yet: SQLSTATE undefined_table /
# insufficient_privilege. The read falls back to the old source and skips the view for VIEW_RETRY_SECONDS.
_MISSING_VIEW_STATES = ("42P01", "42501")
VIEW_RETRY_SECONDS = 300.0
_view_missing_until: dict[str, float] = {}


def _is_missing_object(e: BaseException) -> bool:
    return getattr(e, "sqlstate", None) in _MISSING_VIEW_STATES


def _view_usable(view: str) -> bool:
    return time.monotonic() >= _view_missing_until.get(view, 0.0)


def _mark_view_missing(view: str, e: BaseException) -> None:
    _view_missing_until[view] = time.monotonic() + VIEW_RETRY_SECONDS
    observability.emit("llm_gateway_note", view=view, error=type(e).__name__,
                       detail=f"{view} is not readable yet (CIS migration 323); reading the old source")


def _profiles(raw: Any) -> dict:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw or {})


def _model(r: Any, enabled_key: str) -> Model:
    return Model(r["model_key"], r["vendor"], r["api_style"], _profiles(r["bedrock_profile_ids"]),
                 _num(r["price_in_per_mtok"]), _num(r["price_out_per_mtok"]),
                 _num(r["price_cache_read_per_mtok"]), _num(r["price_cache_write_per_mtok"]),
                 bool(r[enabled_key]), _deployments(r))


def _get(r: Any, key: str) -> Any:
    """A column of a config row that an older view / table read does not carry (None then)."""
    try:
        return r.get(key) if hasattr(r, "get") else r[key]
    except (KeyError, IndexError):
        return None


def _json_list(raw: Any) -> list:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return list(raw) if isinstance(raw, list) else []


def _epoch(raw: Any) -> Optional[float]:
    """A timestamptz from the view's JSON (ISO text) as epoch seconds; None when absent or unreadable."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.timestamp()
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _deployments(r: Any) -> tuple:
    """AA-1027 E3: the model's deployments (323 `deployments`) with their connection facts and circuits (341
    `deployment_routing`, same order). Without 341 the facts are the legacy mirror's (acc2 / acc3) or unknown."""
    routing = {d.get("connection_key"): d for d in _json_list(_get(r, "deployment_routing")) if isinstance(d, dict)}
    has_routing = _get(r, "deployment_routing") is not None
    out = []
    for d in _json_list(_get(r, "deployments")):
        if not isinstance(d, dict) or not d.get("connection_key") or not d.get("wire_id"):
            continue
        key = str(d["connection_key"])
        overrides = tuple((f, float(d[f])) for f in _PRICE_OVERRIDES if d.get(f) is not None)
        base = dict(connection_key=key, wire_id=str(d["wire_id"]), priority=int(d.get("priority") or 0),
                    enabled=bool(d.get("enabled", True)), price_overrides=overrides)
        g = routing.get(key)
        if has_routing and g is not None:
            roles = g.get("served_roles")
            out.append(Deployment(
                **base, facts=bool(g.get("in_registry")), kind=g.get("kind"), auth_mode=g.get("auth_mode"),
                account_id_env=g.get("account_id_env"), region=g.get("region"), data_scope=g.get("data_scope"),
                served_roles=tuple(roles) if isinstance(roles, list) else None,
                connection_enabled=bool(g.get("connection_enabled")),
                circuit_open_until=_epoch(g.get("circuit_open_until")), probe_until=_epoch(g.get("probe_until")),
                last_error_class=g.get("last_error_class"),
                connection_circuit_class=g.get("connection_circuit_class")))
        else:
            out.append(_legacy_deployment(**base))
    return tuple(out)


def _legacy_deployment(**base: Any) -> Deployment:
    facts = _LEGACY_CONNECTIONS.get(base["connection_key"])
    return Deployment(**base, **facts) if facts else Deployment(**base, facts=False)


# AA-1027 E3: the 341 columns, appended to the 323 contract SELECT. Missing (SQLSTATE 42703 before 341 is applied):
# the 323 columns only, and the next VIEW_RETRY_SECONDS skip the wider read.
ROUTING_COLUMNS = ("route_deadline_s", "deployment_routing")
_ROUTING_VIEW = f"{contract.GATEWAY_CONFIG_VIEW}#routing"
GATEWAY_ROUTING_SQL = (
    f"SELECT {', '.join(contract.GATEWAY_CONFIG_COLUMNS + ROUTING_COLUMNS)} "
    f"FROM {contract.GATEWAY_CONFIG_VIEW} WHERE stage = $1 AND is_active ORDER BY position")


async def _view_rows(db: Any, stage: str) -> list:
    if _view_usable(_ROUTING_VIEW):
        try:
            return await db.fetch(GATEWAY_ROUTING_SQL, stage)
        except Exception as e:  # noqa: BLE001 — only the missing 341 columns fall back to the 323 columns
            if getattr(e, "sqlstate", None) != _UNDEFINED_COLUMN:
                raise
            _view_missing_until[_ROUTING_VIEW] = time.monotonic() + VIEW_RETRY_SECONDS
            observability.emit("llm_gateway_note", view=contract.GATEWAY_CONFIG_VIEW,
                               detail="deployment_routing is not readable yet (CIS migration 341); "
                                      "the deployments run on the legacy connection facts")
    return await db.fetch(contract.GATEWAY_CONFIG_SQL, stage)


async def _route_from_view(db: Any, stage: str) -> tuple[Any, tuple]:
    """(route config row, models in chain order) from catalog.v_llm_gateway_config_v1. A chain key that is
    not in the catalog (in_catalog false) is skipped, as before."""
    rows = await _view_rows(db, stage)
    if not rows:
        raise LookupError(f"no active route for {stage!r} in {contract.GATEWAY_CONFIG_VIEW}")
    models = tuple(_model(r, "model_enabled") for r in sorted(rows, key=lambda r: r["position"])
                   if r["in_catalog"])
    if not models:
        raise LookupError(f"none of the {stage!r} route models is in the catalog")
    return rows[0], models


async def _route_from_tables(db: Any, stage: str) -> tuple[Any, tuple]:
    """The pre-323 read: shared.llm_role_config + shared.llm_model_catalog (grandfathered SELECT, CIS 173)."""
    rows = await db.fetch(
        "SELECT role, model_id, fallback_model_ids, account_route, shadow_model_id, "
        "shadow_sample_pct, route_deadline_s FROM shared.llm_role_config WHERE stage = $1 AND is_active", stage)
    if not rows:
        raise LookupError(f"no active llm_role_config row for {stage!r}")
    cfg = rows[0]
    keys = [cfg["model_id"], *(cfg["fallback_model_ids"] or [])]
    cat = await db.fetch(
        "SELECT model_key, vendor, api_style, bedrock_profile_ids, price_in_per_mtok, "
        "price_out_per_mtok, price_cache_read_per_mtok, price_cache_write_per_mtok, enabled "
        "FROM shared.llm_model_catalog WHERE model_key = ANY($1::text[])", keys)
    by_key = {r["model_key"]: _model(r, "enabled") for r in cat}
    models = tuple(by_key[k] for k in keys if k in by_key)
    if not models:
        raise LookupError(f"none of {keys} is in llm_model_catalog")
    return cfg, models


async def _read_route(db: Any, stage: str) -> tuple[Any, tuple]:
    view = contract.GATEWAY_CONFIG_VIEW
    if _view_usable(view):
        try:
            return await _route_from_view(db, stage)
        except Exception as e:  # noqa: BLE001 — only a missing / ungranted view falls back
            if not _is_missing_object(e):
                raise
            _mark_view_missing(view, e)
    return await _route_from_tables(db, stage)


async def load_route(db: Any, stage: str) -> Route:
    """`db` is an asyncpg pool or connection. Raises AiPaused("route_unreadable") when the route
    cannot be read (AA-642 S0: fail closed, never the hard-coded BUILTIN_ROUTES models); the
    failure is not cached, so the next request reads again."""
    cached = _cache.get(stage)
    now = time.monotonic()
    if cached and now - cached[1] < _CACHE_TTL_SECONDS:
        return cached[0]
    try:
        cfg, models = await _read_route(db, stage)
        shadow = cfg.get("shadow_model_id") if (cfg.get("shadow_sample_pct") or 0) > 0 else None
        if shadow:
            observability.emit("llm_gateway_note", stage=stage,
                               detail=f"shadow {shadow!r} is configured but TripPlanner does not "
                                      "run shadows (AA-805); only the primary route is called")
        route = Route(stage, cfg["role"], models, cfg["account_route"], shadow,
                      _deadline_value(_get(cfg, "route_deadline_s")), _scopes(_get(cfg, "allowed_scopes")))
    except Exception as e:  # noqa: BLE001 — AA-642 S0: an unreadable route pauses AI (fail closed)
        _error("route_unreadable", stage, e)
        raise AiPaused("route_unreadable", str(e)[:200]) from None
    _cache[stage] = (route, now)
    return route


def _deadline_value(raw: Any) -> Optional[int]:
    """A route_deadline_s the migration 320 CHECK allows (1..3600 s), else None (the default applies)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 3600:
        return raw
    return None


def route_deadline_s(route: Route) -> float:
    """The wall-time budget of one generate_text request on `route` (module docstring, AA-642 S263 E1)."""
    seconds = float(route.deadline_s) if route.deadline_s else float(config.LLM_ROUTE_DEADLINE_DEFAULT_S)
    return min(seconds, float(config.LLM_ROUTE_DEADLINE_MAX_S))


# --- Deployment plan (AA-1027 E3) ---------------------------------------------------------------


def model_deployments(model: Model, account_route: Optional[str] = None) -> tuple:
    """The model's deployments: the config view's, else (no deployment row, CIS 341 / 323 not applied) one per
    catalog profile, `account_route` first, then acc3, acc2, then any other connection (facts unknown: never
    called)."""
    if model.deployments:
        return model.deployments
    keys = list(dict.fromkeys(k for k in (account_route, *_LEGACY_ORDER) if k and k in model.profiles))
    keys += sorted(k for k in model.profiles if k not in keys)
    return tuple(_legacy_deployment(connection_key=k, wire_id=str(model.profiles[k]), priority=i)
                 for i, k in enumerate(keys))


def _uncallable(model: Model, d: Deployment) -> Optional[str]:
    """None when TripPlanner can call `model` on `d` (T-11: Bedrock only, a role it holds), else why not."""
    if not d.facts:
        return "connection not described (no TripPlanner credentials for it)"
    if d.kind != KIND_BEDROCK:
        return f"a {d.kind} connection (TripPlanner calls Bedrock only)"
    if d.auth_mode == AUTH_TASK_ROLE:
        return None
    if d.auth_mode == AUTH_ASSUME_ROLE and d.account_id_env == SATELLITE_ACCOUNT_ENV:
        return None
    return "no TripPlanner credentials for this connection"


@dataclass
class Plan:
    """The deployments a model is tried on, in order, and why the others were left out: `circuit_skips` =
    (connection_key, class that opened it) of each deployment skipped only because of a circuit, `notes` = every
    other reason (for the error message)."""
    items: list = field(default_factory=list)
    circuit_skips: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _scopes(raw: Any) -> Optional[tuple]:
    """allowed_scopes as a tuple, None for "no stage policy" (NULL, not a list)."""
    return tuple(str(s) for s in raw) if isinstance(raw, (list, tuple)) else None


def deployment_plan(route: Route, model: Model, *, now: Optional[float] = None) -> Plan:
    """AA-1027 E3 — where `model` is called for `route`, in order (CIS vendor_route.deployment_plan for what
    TripPlanner can call): enabled deployment on an enabled connection TripPlanner can call (`_uncallable`); a
    connection that serves the route's role (`served_roles`, None = every role); a data scope the stage allows (with
    no stage policy a vendor_direct connection only as the first candidate, CIS slice S5's rule); then no open circuit
    (`llm_breaker`: the CIS state from the view, then this container's). Never raises."""
    now = _wall_clock() if now is None else now
    plan = Plan()
    candidates: list[Deployment] = []
    for d in model_deployments(model, route.account_route):
        where = f"{model.key}@{d.connection_key}"
        if not d.enabled or not d.connection_enabled:
            plan.notes.append(f"{where}: disabled")
            continue
        why = _uncallable(model, d)
        if why:
            plan.notes.append(f"{where}: {why}")
            continue
        if d.served_roles is not None and route.role not in d.served_roles:
            plan.notes.append(f"{where}: the connection does not serve the role {route.role}")
            continue
        scope = d.data_scope or AWS_ONLY
        if route.allowed_scopes is not None:
            if scope not in route.allowed_scopes:
                plan.notes.append(f"{where}: data scope {scope} is not allowed for the stage")
                continue
        elif candidates and scope != AWS_ONLY:
            plan.notes.append(f"{where}: {scope} is never a fallback (no stage data scope)")
            continue
        candidates.append(d)
    for d in candidates:
        cls = llm_breaker.shared_skip_class(
            connection_circuit_class=d.connection_circuit_class, circuit_open_until=d.circuit_open_until,
            probe_until=d.probe_until, last_error_class=d.last_error_class, now=now)
        source = "gateway"
        if cls is None:
            cls, source = llm_breaker.local.open_class(d.connection_key, model.key), "container"
        if cls:
            plan.circuit_skips.append((d.connection_key, cls))
            plan.notes.append(f"{model.key}@{d.connection_key}: circuit open ({cls})")
            observability.emit("llm_deployment_skip", stage=route.stage, model=model.key,
                               connection=d.connection_key, reason="circuit_open", error_class=cls, source=source)
            continue
        plan.items.append(d)
    return plan


def as_deployment(model: Model, d: Any) -> Deployment:
    """`d` as a Deployment of `model`: a Deployment as is, a connection key (callers and tests that name the
    connection only) as the model's deployment on it, else the legacy one."""
    if isinstance(d, Deployment):
        return d
    found = next((x for x in model_deployments(model) if x.connection_key == d), None)
    return found or _legacy_deployment(connection_key=str(d), wire_id=model.profiles.get(d))


def _satellite_target(d: Deployment) -> tuple[str, str, str]:
    """(account id, invoker role name, ExternalId) of an assume_role connection whose account comes from the Lambda
    env (`BEDROCK_ACCT_PRIMARY`, acc3: the only satellite since AA-998).

    The account ID has no default (AA-902, public repo): an unset one raises here, before any
    STS call, so the failover loop records a clear error and moves on."""
    if d.auth_mode != AUTH_ASSUME_ROLE or d.account_id_env != SATELLITE_ACCOUNT_ENV:
        raise BedrockError(f"{d.connection_key}: no TripPlanner credentials for this connection")
    target = (config.BEDROCK_ACCT_PRIMARY, config.BEDROCK_ROLE_NAME_PRIMARY,
              config.BEDROCK_EXTERNAL_ID_PRIMARY)
    if not target[0]:
        raise BedrockError(f"{d.connection_key}: account id not configured (BEDROCK_ACCT_PRIMARY is unset)")
    return target


def _runtime(d: Deployment, client_factory: ClientFactory, timeout_s: Optional[float] = None) -> Any:
    """The deployment's bedrock-runtime client: the Lambda role (task_role, in the connection's region) or the
    satellite role. `timeout_s` (the time left of the route deadline) bounds the production clients, the satellite's
    STS call included; an injected factory (tests, scripts) is used as is."""
    if timeout_s is not None and client_factory is bedrock_satellite.default_client_factory:
        client_factory = functools.partial(bedrock_satellite.default_client_factory, timeout_s=timeout_s)
    if d.auth_mode == AUTH_TASK_ROLE:
        return client_factory("bedrock-runtime", region=d.region or config.BEDROCK_REGION)
    return bedrock_satellite.bedrock_for_account(*_satellite_target(d), client_factory)


def _quote(model: Model, d: Deployment, tin: int, tout: int, cache_read: int = 0,
           cache_write: int = 0) -> tuple[float, str]:
    """(USD, pricing_mode) of one billed attempt from the shared core (`aa_llm.pricing.quote`): the catalog prices,
    the deployment's price overrides on top. A model nobody can price gets the core's flagged estimate."""
    catalog = pricing.catalog_rates(model.price_in, model.price_out, model.price_cache_read, model.price_cache_write)
    usage = pricing.Usage(input_tokens=int(tin or 0), output_tokens=int(tout or 0),
                          cache_read_tokens=int(cache_read or 0), cache_write_tokens=int(cache_write or 0))

    def _estimated(key: str) -> None:
        observability.emit("llm_gateway_note", level="warning", model=key,
                           detail="no catalog price: charged the core's estimate rates (pricing_mode estimate)")

    q = pricing.quote(model.key, usage, dict(d.price_overrides) or None, catalog=catalog, on_estimate=_estimated)
    return q.cost_usd, q.pricing_mode


def _failed(route: Route, model: Model, d: Any, position: int, error: str, *,
            latency_ms: Optional[int] = None, attempt_no: Optional[int] = None,
            request_id: Optional[str] = None) -> Call:
    """A failed attempt: nothing billed, the error kept for the log row, its ledger outcome
    ('refused' for an access / credit / rate-limit refusal, else 'error') from the shared core.
    AA-1027 E3: the container's breaker counts it."""
    d = as_deployment(model, d)
    status, error_class = ledger.failed_outcome(error)
    llm_breaker.local.record_failure(d.connection_key, model.key, error_class, error)
    return Call(route.stage, route.role, model.key, d.connection_key, 0, 0, 0.0, fallback_used=position > 0,
                stop_reason="error", extra={"error": error[:500]}, failed=True,
                latency_ms=latency_ms, attempt_no=attempt_no, wire_id=d.wire_id,
                status=status, error_class=error_class, request_id=request_id, auth_mode=d.auth_mode)


def _not_called(route: Route, model: Model, position: int, skips: list) -> Call:
    """AA-1027 E3: the 0 USD 'refused' row of a model none of whose deployments was called because of an open
    circuit. No connection, no attempt: the class is the one that opened the (first) circuit."""
    cls = skips[0][1] if skips[0][1] in ledger.ERROR_CLASSES else core_errors.OTHER
    names = ", ".join(f"{k} ({c})" for k, c in skips)
    return Call(route.stage, route.role, model.key, None, 0, 0, 0.0, fallback_used=position > 0,
                stop_reason="error", extra={"error": f"not called: circuit open on {names}",
                                            "not_called": "circuit_open",
                                            "circuit_skips": [k for k, _ in skips]},
                failed=True, latency_ms=0, status=ledger.STATUS_REFUSED, error_class=cls)


def _request_id(source: Any) -> Optional[str]:
    """AA-642 S263 E2: the Bedrock RequestId of a reply dict, or of a botocore ClientError (its `response`)."""
    meta = source.get("ResponseMetadata") if isinstance(source, dict) else None
    if meta is None:
        resp = getattr(source, "response", None)
        meta = resp.get("ResponseMetadata") if isinstance(resp, dict) else None
    rid = meta.get("RequestId") if isinstance(meta, dict) else None
    return rid[:200] if isinstance(rid, str) and rid else None


def _with_prompt(call: Call, prompt: tuple[Optional[str], Optional[str]]) -> Call:
    """The attempt stamped with (prompt_hash, prompt_version) of the request (AA-642 S263 E2)."""
    call.prompt_hash, call.prompt_version = prompt
    return call


def _truncated(route: Route, model: Model, d: Any, position: int, usage: dict, stop: str, *,
               latency_ms: int, attempt_no: int) -> Call:
    """AA-642 S263 E1: a reply cut at its token limit. Billed (tokens and cost kept, so it counts against the spend
    cap) but unusable: a failed attempt with status / error_class 'truncated'. The provider answered, so the
    container's breaker counts it as a success (AA-1027 E3)."""
    d = as_deployment(model, d)
    llm_breaker.local.record_success(d.connection_key, model.key)
    cost, mode = _quote(model, d, usage["in"], usage["out"], usage["cache_read"], usage["cache_write"])
    return Call(route.stage, route.role, model.key, d.connection_key, usage["in"], usage["out"], cost,
                fallback_used=position > 0, stop_reason=stop,
                extra={"error": f"reply cut at its token limit ({stop})"}, failed=True,
                latency_ms=latency_ms, attempt_no=attempt_no,
                cache_read_tokens=int(usage["cache_read"] or 0), cache_write_tokens=int(usage["cache_write"] or 0),
                wire_id=d.wire_id, status=ledger.STATUS_TRUNCATED,
                error_class=core_errors.TRUNCATED, request_id=usage.get("request_id"), pricing_mode=mode,
                auth_mode=d.auth_mode)


def _deadline_passed(route: Route, model: Model, d: Any, position: int, seconds: float, *,
                     attempt_no: int) -> Call:
    """AA-642 S263 E1: the attempt the route deadline stopped before it started: 0 USD, no provider call."""
    d = as_deployment(model, d)
    return Call(route.stage, route.role, model.key, d.connection_key, 0, 0, 0.0, fallback_used=position > 0,
                stop_reason="error", extra={"error": f"route deadline of {seconds:g} s passed before this attempt"},
                failed=True, latency_ms=0, attempt_no=attempt_no, wire_id=d.wire_id,
                status=ledger.STATUS_ERROR, error_class=core_errors.DEADLINE, auth_mode=d.auth_mode)


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _read_body(resp: dict) -> dict:
    raw = resp["body"].read() if hasattr(resp["body"], "read") else resp["body"]
    return json.loads(raw)


def _text_call(model: Model, d: Deployment, system: str, user: str, max_tokens: int,
               client_factory: ClientFactory, *, timeout_s: Optional[float] = None,
               ) -> tuple[str, dict, Optional[str]]:
    rt = _runtime(d, client_factory, timeout_s)
    if model.api_style == "anthropic_native":
        body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": max_tokens,
                "system": system, "messages": [{"role": "user", "content": user}]}
        resp = rt.invoke_model(modelId=d.wire_id, body=json.dumps(body))
        payload = _read_body(resp)
        text = "".join(p.get("text", "") for p in payload.get("content", [])
                       if isinstance(p, dict) and p.get("type") == "text")
        u = payload.get("usage", {}) or {}
        usage = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0),
                 "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                 "cache_write": u.get("cache_creation_input_tokens", 0) or 0,
                 "request_id": _request_id(resp)}
        return text, usage, payload.get("stop_reason")
    if model.api_style == "converse":
        resp = rt.converse(modelId=d.wire_id, system=[{"text": system}],
                           messages=[{"role": "user", "content": [{"text": user}]}],
                           inferenceConfig={"maxTokens": max_tokens})
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        u = resp.get("usage", {}) or {}
        usage = {"in": u.get("inputTokens", 0), "out": u.get("outputTokens", 0),
                 "cache_read": u.get("cacheReadInputTokens", 0) or 0,
                 "cache_write": u.get("cacheWriteInputTokens", 0) or 0,
                 "request_id": _request_id(resp)}
        return text, usage, resp.get("stopReason")
    raise BedrockError(f"{model.key}: api_style {model.api_style!r} is not supported here")


def _no_usable(route: Route, model: Model, position: int, plan: Plan, errors: list,
               failures: Optional[list], prompt: Optional[tuple] = None) -> list:
    """AA-1027 E3: the model has no deployment to try. Its reasons go to `errors`; when circuits emptied the plan,
    its 0 USD 'refused' row goes to `failures` and the circuit skips are returned."""
    errors.extend(plan.notes or [f"{model.key}: no deployment"])
    if not plan.circuit_skips:
        return []
    if failures is not None:
        row = _not_called(route, model, position, plan.circuit_skips)
        failures.append(_with_prompt(row, prompt) if prompt else row)
    return list(plan.circuit_skips)


def _all_failed(route: Route, errors: list, attempts: int, skips: list) -> BedrockError:
    """The error of a route that answered nothing: CircuitOpen when no provider attempt was made and circuits were
    the reason (callers degrade with 'circuit_open'), else the generic failure."""
    detail = "; ".join(errors)
    if attempts == 0 and skips:
        return CircuitOpen(f"No model called for stage {route.stage!r}: {detail}"[:1000], skips[0][1])
    return BedrockError(f"All models failed for stage {route.stage!r}: " + detail)


def generate_text(route: Route, system: str, user: str, max_tokens: int, *,
                  client_factory: ClientFactory = bedrock_satellite.default_client_factory,
                  failures: Optional[list] = None,
                  ) -> tuple[str, Call]:
    """Runs the route: each enabled model in order, each of its deployments in order (`deployment_plan`).
    Every failed attempt is appended to `failures` (when given) so the caller logs it too.

    AA-642 S263 E1: a reply cut at its token limit is a failed (billed) attempt, never returned; the route deadline
    (`route_deadline_s`) bounds every attempt's client and stops the loop with RouteDeadlineExceeded.
    AA-1027 E3: a deployment behind an open circuit is not called (a model with nothing left gets one 'refused' row);
    nothing attempted because of circuits raises CircuitOpen."""
    attempt_errors: list = []
    skips: list = []
    attempt = 0
    prompt = (prompt_registry.system_prompt_hash(system), prompt_registry.version_for(route.stage))
    deadline = route_deadline_s(route)
    ends = time.monotonic() + deadline
    for position, model in enumerate(route.models):
        if not model.enabled or model.api_style == "embed":
            attempt_errors.append(f"{model.key}: skipped")
            continue
        plan = deployment_plan(route, model)
        if not plan.items:
            skips += _no_usable(route, model, position, plan, attempt_errors, failures, prompt)
            continue
        for d in plan.items:
            attempt += 1
            left = ends - time.monotonic()
            if left < config.LLM_ROUTE_DEADLINE_MIN_ATTEMPT_S:
                if failures is not None:
                    failures.append(_with_prompt(
                        _deadline_passed(route, model, d, position, deadline, attempt_no=attempt), prompt))
                observability.emit("llm_route_deadline_passed", level="warning", stage=route.stage,
                                   model=model.key, account=d.connection_key, deadline_s=deadline)
                tried = "; ".join(attempt_errors)[:600]
                raise RouteDeadlineExceeded(
                    f"Route deadline of {deadline:g} s for stage {route.stage!r} passed before "
                    f"{model.key}@{d.connection_key}" + (f"; tried: {tried}" if tried else ""))
            started = time.monotonic()
            try:
                text, usage, stop = _text_call(model, d, system, user, max_tokens,
                                               client_factory, timeout_s=left)
            except Exception as e:  # noqa: BLE001 — fail over to the next deployment/model
                attempt_errors.append(f"{model.key}@{d.connection_key}: {str(e)[:200]}")
                failed = _failed(route, model, d, position, str(e), latency_ms=_ms(started), attempt_no=attempt,
                                 request_id=_request_id(e))
                if failures is not None:
                    failures.append(_with_prompt(failed, prompt))
                continue
            if stop in ledger.TRUNCATED_STOP_REASONS:
                # AA-642 S263 E1: billed but cut short; logged as 'truncated' and never returned.
                attempt_errors.append(f"{model.key}@{d.connection_key}: reply cut at its token limit ({stop})")
                cut = _truncated(route, model, d, position, usage, stop, latency_ms=_ms(started),
                                 attempt_no=attempt)
                if failures is not None:
                    failures.append(_with_prompt(cut, prompt))
                continue
            if not text:
                attempt_errors.append(f"{model.key}@{d.connection_key}: empty response")
                empty = _failed(route, model, d, position, "empty response", latency_ms=_ms(started),
                                attempt_no=attempt, request_id=usage.get("request_id"))
                if failures is not None:
                    failures.append(_with_prompt(empty, prompt))
                continue
            llm_breaker.local.record_success(d.connection_key, model.key)
            cost, mode = _quote(model, d, usage["in"], usage["out"], usage["cache_read"], usage["cache_write"])
            return text, _with_prompt(Call(
                route.stage, route.role, model.key, d.connection_key, usage["in"], usage["out"], cost,
                fallback_used=position > 0, stop_reason=stop, latency_ms=_ms(started),
                attempt_no=attempt, cache_read_tokens=int(usage["cache_read"] or 0),
                cache_write_tokens=int(usage["cache_write"] or 0), wire_id=d.wire_id,
                request_id=usage.get("request_id"), pricing_mode=mode, auth_mode=d.auth_mode), prompt)
    raise _all_failed(route, attempt_errors, attempt, skips)


_task_embed: dict[str, Any] = {}


def _new_embed_client(creds: Optional[bedrock_satellite.Credentials] = None, region: Optional[str] = None) -> Any:
    """A bedrock-runtime client for embeddings with NO retry: a throttle retried at once spends the
    same minute's quota again and holds the search request for seconds; the route moves on to the
    next account instead (AA-862), and search falls back to keyword ranking when every account
    failed (AA-856). `creds` = satellite temp creds; None = the
    Lambda role (acc2). AA-862: `total_max_attempts=1`, because botocore reads the legacy
    `max_attempts` as the number of RETRIES (`max_attempts=1` = 2 attempts, as AA-856 shipped)."""
    import boto3  # lazily, so tests never need boto3
    from botocore.config import Config

    kwargs: dict[str, Any] = {"region_name": region or config.BEDROCK_REGION}
    if creds is not None:
        kwargs.update(aws_access_key_id=creds.access_key_id,
                      aws_secret_access_key=creds.secret_access_key,
                      aws_session_token=creds.session_token)
    return boto3.client("bedrock-runtime", config=Config(
        connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1, "mode": "standard"}),
        **kwargs)


def _task_embed_client(region: Optional[str] = None) -> Any:
    """AA-856: one Lambda-role (task_role) bedrock-runtime client per region per container for embeddings, no
    retry (AA-1027 E3: per region, a task_role connection may name another region)."""
    key = region or config.BEDROCK_REGION
    client = _task_embed.get(key)
    if client is None:
        client = _task_embed[key] = _new_embed_client(region=key)
    return client


# AA-862: satellite (acc3) embed clients, one per connection per container, with the wall-clock
# time (epoch seconds) after which the client is rebuilt. Wall clock, not monotonic, because STS
# `Expiration` is an absolute time and a frozen Lambda container must not outlive its creds.
_sat_embed: dict[str, tuple[Any, float]] = {}
SAT_CREDS_REFRESH_MARGIN_SECONDS = 300.0
# AssumeRole's shortest session is 900 s; used when STS gives no Expiration (never in production).
SAT_CREDS_ASSUMED_LIFETIME_SECONDS = 900.0
_wall_clock: Callable[[], float] = time.time
# Errors after which a cached satellite client is dropped, so the next call assumes the role again.
_SAT_STALE_MARKERS = ("expiredtoken", "accessdenied", "unrecognizedclient")


def _embed_sts_factory(service: str, **_: Any) -> Any:
    """STS client for the satellite AssumeRole on the search path, with short timeouts and at most
    one retry. boto3's defaults (60 s connect, legacy retries) could hold a search past the browse
    Lambda's 30 s timeout when STS hangs behind the NAT instance, turning a keyword-ranked answer
    into a 500 (AA-862 review)."""
    import boto3  # lazily, so tests never need boto3
    from botocore.config import Config

    return boto3.client(service, region_name=config.BEDROCK_REGION, config=Config(
        connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 2, "mode": "standard"}))


def _satellite_embed_client(d: Deployment) -> Any:
    """AA-862: cached no-retry embed client for a satellite connection. One AssumeRole per container
    until SAT_CREDS_REFRESH_MARGIN_SECONDS before the creds expire, instead of one per call."""
    now = _wall_clock()
    cached = _sat_embed.get(d.connection_key)
    if cached is not None and now < cached[1]:
        return cached[0]
    creds = bedrock_satellite.assume_credentials(*_satellite_target(d), _embed_sts_factory)
    if creds.expiration is not None:
        expires_at = creds.expiration.timestamp()
    else:
        expires_at = now + SAT_CREDS_ASSUMED_LIFETIME_SECONDS
    client = _new_embed_client(creds)
    _sat_embed[d.connection_key] = (client, expires_at - SAT_CREDS_REFRESH_MARGIN_SECONDS)
    return client


def _embed_runtime(d: Deployment, client_factory: ClientFactory) -> Any:
    """Production (default factory): the cached no-retry clients. An injected factory (tests,
    scripts) keeps the plain per-call `_runtime` path."""
    if client_factory is not bedrock_satellite.default_client_factory:
        return _runtime(d, client_factory)
    if d.auth_mode == AUTH_TASK_ROLE:
        return _task_embed_client(d.region)
    return _satellite_embed_client(d)


def _drop_stale_satellite_client(d: Deployment, error: str) -> None:
    low = (error or "").lower()
    if d.auth_mode == AUTH_ASSUME_ROLE and any(m in low for m in _SAT_STALE_MARKERS):
        _sat_embed.pop(d.connection_key, None)


def embed_texts(route: Route, texts: list[str], *, input_type: str = "search_document",
                client_factory: ClientFactory = bedrock_satellite.default_client_factory,
                failures: Optional[list] = None,
                ) -> tuple[list[list[float]], Call]:
    """Cohere Embed v4 request shape (the only embedding vendor in the catalog). Each model on its deployments in
    order (AA-862: acc2 first, then the satellite row by route; AA-1027 E3: `deployment_plan`, circuits included)."""
    errors: list = []
    skips: list = []
    attempt = 0
    for position, model in enumerate(route.models):
        if not model.enabled or model.api_style != "embed" or model.vendor != "cohere":
            errors.append(f"{model.key}: skipped")
            continue
        plan = deployment_plan(route, model)
        if not plan.items:
            skips += _no_usable(route, model, position, plan, errors, failures)
            continue
        for d in plan.items:
            attempt += 1
            started = time.monotonic()
            try:
                rt = _embed_runtime(d, client_factory)
                resp = rt.invoke_model(modelId=d.wire_id, body=json.dumps(
                    {"texts": texts, "input_type": input_type, "embedding_types": ["float"],
                     "output_dimension": config.EMBED_DIM}))
                emb = _read_body(resp).get("embeddings")
                vectors = emb.get("float") if isinstance(emb, dict) else emb
                if (not isinstance(vectors, list) or len(vectors) != len(texts)
                        or any(len(v) != config.EMBED_DIM for v in vectors)):
                    raise BedrockError("malformed embedding response")
            except Exception as e:  # noqa: BLE001
                if client_factory is bedrock_satellite.default_client_factory:
                    _drop_stale_satellite_client(d, str(e))
                errors.append(f"{model.key}@{d.connection_key}: {str(e)[:200]}")
                failed = _failed(route, model, d, position, str(e), latency_ms=_ms(started), attempt_no=attempt,
                                 request_id=_request_id(e))
                if failures is not None:
                    failures.append(failed)
                continue
            llm_breaker.local.record_success(d.connection_key, model.key)
            header = (resp.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                      .get("x-amzn-bedrock-input-token-count"))
            tokens = int(header) if header is not None else sum(max(1, len(t) // 4) for t in texts)
            cost, mode = _quote(model, d, tokens, 0)
            return [[float(x) for x in v] for v in vectors], Call(
                route.stage, route.role, model.key, d.connection_key, tokens, 0, cost,
                fallback_used=position > 0, extra={"tokens_estimated": header is None},
                latency_ms=_ms(started), attempt_no=attempt, wire_id=d.wire_id,
                request_id=_request_id(resp), pricing_mode=mode, auth_mode=d.auth_mode)
    raise _all_failed(route, errors, attempt, skips)


# --- Query embedding cache (AA-856) -------------------------------------------------------------
#
# Cohere Embed V4 on acc2 allows 20 requests per minute (Service Quotas, S239), so every repeated
# search query must not call Bedrock again (misses then fall through to acc3, AA-862). The
# cache lives in the Lambda container (one instance serves one invocation at a time, so no
# locking): bounded LRU with a TTL. A hit is not a model
# call: no llm_call_log row, nothing added to the daily spend.

EMBED_CACHE_MAX_ENTRIES = 1024      # ~12 KB per 1536-d vector stored as array('d') -> ~12 MB max
EMBED_CACHE_TTL_SECONDS = 6 * 3600  # the key names the model; the TTL only bounds staleness

_WS_RE = re.compile(r"\s+")


def normalise_query(text: str) -> str:
    """Cache key text and the text that is embedded: NFKC, case-folded, whitespace collapsed."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text or "")).strip().casefold()


# Geographic / cross-region inference-profile prefixes: `global.cohere.embed-v4:0` and
# `us.cohere.embed-v4:0` are the same model and give the same vector.
_PROFILE_GEO_PREFIXES = ("global.", "us-gov.", "us.", "eu.", "apac.", "jp.", "au.", "ca.")


def _base_model_id(profile: str) -> str:
    for prefix in _PROFILE_GEO_PREFIXES:
        if profile.startswith(prefix):
            return profile[len(prefix):]
    return profile


def embed_cache_key(route: Route, input_type: str, text: str) -> tuple:
    """(stage, base model ids the route can call, input_type, dimension, normalised text).

    Keyed on the MODEL (the Bedrock model id behind each row's profiles, geo prefix stripped), not
    on the route's catalog keys: AA-862 rows `cohere-embed-v4` / `-acc3` are one model on
    two accounts, so adding, enabling or disabling an account row must not empty the cache (a
    cold cache sends every query straight at acc2's 20 rpm). A different model on the admin
    Settings page gives a new key, so a cached vector never crosses models. AA-1027 E3: the wire ids of the
    deployments count too (the ids actually called), with the catalog profiles."""
    ids = sorted({_base_model_id(str(p)) for m in route.models
                  for p in [*(m.profiles or {}).values(), *(d.wire_id for d in m.deployments)]})
    model_part = tuple(ids) if ids else tuple(m.key for m in route.models)
    return (route.stage, model_part, input_type, config.EMBED_DIM, normalise_query(text))


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


# The pre-319 row (no ledger v2 columns): written only when the v2 INSERT finds a column missing.
_LEGACY_INSERT_SQL = (
    "INSERT INTO shared.llm_call_log (tenant_id, stage, role, model, tokens_in, tokens_out, "
    "cost_usd, quality_signal, stop_reason, account, fallback_used, provider) "
    "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)")
_UNDEFINED_COLUMN = "42703"


def _billed(call: Call) -> bool:
    """A success, or a failed attempt the provider still billed (a reply cut at its token limit, AA-642 S263 E1)."""
    return not call.failed or call.status == ledger.STATUS_TRUNCATED


def ledger_row(call: Call, signal: dict, tenant_id: Optional[str] = None) -> ledger.LedgerRow:
    """The ledger v2 row of one attempt (AA-642 S8). A billed attempt says how the core priced it (AA-1027 E3:
    `aa_llm.pricing.quote`'s mode: override / catalog / estimate); a failed attempt carries its status and error
    class."""
    return ledger.LedgerRow(
        app=APP, stage=call.stage, role=call.role, model=call.model, tokens_in=call.tokens_in,
        tokens_out=call.tokens_out, cost_usd=call.cost_usd, quality_signal=signal, tenant_id=tenant_id,
        stop_reason=call.stop_reason, account=call.account, fallback_used=call.fallback_used,
        provider=call.provider, cache_read_tokens=call.cache_read_tokens or None,
        cache_write_tokens=call.cache_write_tokens or None,
        pricing_mode=call.pricing_mode if call.cost_usd is not None and _billed(call) else None,
        status=call.status if call.failed else None, error_class=call.error_class if call.failed else None,
        latency_ms=call.latency_ms, connection_key=call.account, deployment_wire_id=call.wire_id,
        attempt_no=call.attempt_no, request_id=call.request_id, prompt_hash=call.prompt_hash,
        prompt_version=call.prompt_version)


async def record(db: Any, call: Call, quality_signal: dict, *,
                 tenant_id: Optional[str] = None) -> None:
    """Writes the shared.llm_call_log row (ledger v2; the pre-319 row when its columns are missing)
    and, for ONLINE_STAGES, today's spend counter. Never raises: a lost log row must not fail a
    request, but it is an ERROR line and, for an online stage, pauses AI for the hold window (the
    spend view sums the ledger, so the day would undercount)."""
    global _spend_unreadable_until
    signal = {"app": APP, **call.extra, **quality_signal}
    if call.failed:
        signal["failed"] = True
    row = ledger_row(call, signal, tenant_id)
    try:
        try:
            await db.execute(ledger.insert_sql(), *row.args())
        except Exception as e:  # noqa: BLE001
            if getattr(e, "sqlstate", None) != _UNDEFINED_COLUMN:
                raise
            await db.execute(_LEGACY_INSERT_SQL, *row.base_values()[:8], call.stop_reason, call.account,
                             call.fallback_used, call.provider)
    except Exception as e:  # noqa: BLE001 — AA-642 S0: logged as an ERROR (unlogged spend)
        _error("call_log_write_failed", call.stage, e)
        if call.stage in ONLINE_STAGES:
            _spend_unreadable_until = time.monotonic() + SPEND_UNREADABLE_HOLD_SECONDS
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


# AA-642 S0: after a failed counter write the counter undercounts, so the cap could never be
# reached. For this long (per container) budget_status reports "spend_unreadable" and AI pauses.
SPEND_UNREADABLE_HOLD_SECONDS = 60.0
_spend_unreadable_until = 0.0

# budget_status reasons when `exhausted` is True.
REASON_CAP = core_budget.REASON_CAP
REASON_SPEND_UNREADABLE = core_budget.REASON_SPEND_UNREADABLE


def _error(what: str, stage: Optional[str], e: BaseException) -> None:
    """One ERROR line for a gateway failure that changes what customers get (AA-642 S0)."""
    observability.emit("llm_gateway_error", level="error", what=what, stage=stage,
                       error=type(e).__name__, detail=str(e)[:200])


def spend_unreadable_flag_active() -> bool:
    return time.monotonic() < _spend_unreadable_until


async def _add_spend(db: Any, call: Call) -> None:
    global _spend_unreadable_until
    unpriced = 1 if call.cost_usd is None else 0
    try:
        await db.execute(_SPEND_UPSERT_SQL, call.stage, call.cost_usd or 0.0, unpriced)
    except Exception as e:  # noqa: BLE001 — e.g. tp-010 not applied, permission, DB down
        _error("spend_write_failed", call.stage, e)
        _spend_unreadable_until = time.monotonic() + SPEND_UNREADABLE_HOLD_SECONDS


# AA-1015: the stage caps (config, unset = none) and the once-per-day alert lines.
def _stage_cap(stage: Optional[str]) -> Optional[float]:
    caps = {"tp_compose": config.TP_DAILY_SPEND_CAP_COMPOSE_USD,
            "tp_search_embed": config.TP_DAILY_SPEND_CAP_SEARCH_USD}
    cap = caps.get(stage) if stage else None
    return float(cap) if cap is not None else None


# (UTC day, scope, threshold) already announced by this container, so a busy day prints each alert once.
_alerted: set[tuple[str, str, str]] = set()


def _alert(scope: str, spent: float, cap: float) -> None:
    """One `llm_spend_alert` line per container, UTC day, scope and threshold: 'near' at
    SPEND_NEAR_CAP_RATIO of the cap (level warning), 'cap' when reached (level error)."""
    threshold = core_budget.threshold(spent, cap, config.SPEND_NEAR_CAP_RATIO)
    if threshold is None:
        return
    level = "error" if threshold == core_budget.THRESHOLD_CAP else "warning"
    key = (time.strftime("%Y-%m-%d", time.gmtime()), scope, threshold)
    if key in _alerted:
        return
    _alerted.add(key)
    observability.emit("llm_spend_alert", level=level, threshold=threshold, scope=scope,
                       spent_usd=round(spent, 6), cap_usd=cap, ratio=config.SPEND_NEAR_CAP_RATIO)


async def _spent(db: Any, stages: list) -> float:
    """Today's (UTC) spend of `stages`: the CIS contract view over the ledger (CIS 323), else the
    tp-010 counter while the view is missing or not granted. Any other error raises (fail closed)."""
    row = None
    view = contract.APP_SPEND_VIEW
    if _view_usable(view):
        try:
            row = await db.fetchrow(contract.APP_SPEND_SQL, stages)
        except Exception as e:  # noqa: BLE001 — only a missing / ungranted view falls back
            if not _is_missing_object(e):
                raise
            _mark_view_missing(view, e)
            row = await db.fetchrow(_SPEND_TODAY_SQL, stages)
    else:
        row = await db.fetchrow(_SPEND_TODAY_SQL, stages)
    return float(row["spent"]) if row and row["spent"] is not None else 0.0


async def budget_status(db: Any, stage: Optional[str] = None) -> dict:
    """Today's (UTC) customer-facing (ONLINE_STAGES) model spend against TP_DAILY_SPEND_CAP_USD and, when
    `stage` has a stage cap (AA-1015: TP_DAILY_SPEND_CAP_COMPOSE_USD / _SEARCH_USD), that stage's spend
    against it. Both caps apply; with no stage cap set only the combined cap does (behaviour unchanged).

    Returns {"spent_usd", "cap_usd", "exhausted", "reason", "near_cap"} (+ "stage", "stage_spent_usd",
    "stage_cap_usd" when a stage cap applies). `near_cap` is True from SPEND_NEAR_CAP_RATIO (80 %) of
    either cap, and the first time a container sees 80 % or 100 % of a cap on a UTC day it prints one
    `llm_spend_alert` line. Fails CLOSED (AA-642 S0, root ADR 0009 decision 6): when the counter cannot
    be read, or a counter write failed in the last SPEND_UNREADABLE_HOLD_SECONDS, `exhausted` is True
    with reason "spend_unreadable" and `spent_usd` None, so no model is called and callers degrade
    (basic narration, keyword search). Over a cap the reason is "cap_reached"; under it, None.
    Calls with no catalog price add nothing."""
    cap = float(config.TP_DAILY_SPEND_CAP_USD)
    stage_cap = _stage_cap(stage)
    if spend_unreadable_flag_active():
        return core_budget.unreadable(cap).as_dict()
    try:
        spent = await _spent(db, list(ONLINE_STAGES))
        stage_spent = await _spent(db, [stage]) if stage_cap is not None else None
    except Exception as e:  # noqa: BLE001
        _error("spend_unreadable", stage, e)
        return core_budget.unreadable(cap).as_dict()
    _alert("total", spent, cap)
    if stage_cap is not None and stage_spent is not None:
        _alert(str(stage), stage_spent, stage_cap)
    decision = core_budget.decide(spent, cap, near_ratio=config.SPEND_NEAR_CAP_RATIO, stage=stage,
                                  stage_spent=stage_spent, stage_cap=stage_cap)
    return decision.as_dict()
