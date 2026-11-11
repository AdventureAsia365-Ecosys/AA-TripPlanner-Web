"""AA-1027 E3 (root plan S263; gaps PC-2, TP-2, TP-3): the gateway dispatches by deployment, honours served roles and
data scopes, skips connections and deployments behind an open circuit (the CIS breaker state from the contract view,
CIS 341, and the container's own breaker), and prices through the shared core. Fully stubbed: no AWS, no DB.
Invented model, connection and wire names only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import config
from backend.aa_llm import contract, ledger
from backend.shared import bedrock_satellite as bs
from backend.shared import llm_breaker
from backend.shared import llm_gateway as gw

NOW = 2_000_000_000.0     # the wall clock the tests freeze (epoch seconds)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "ZzInvokerRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    monkeypatch.setattr(gw, "_wall_clock", lambda: NOW)
    gw._cache.clear()
    gw._view_missing_until.clear()
    yield
    gw._view_missing_until.clear()


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw


class Stub:
    """bedrock-runtime stub: raises the queued errors first, then answers (text or vectors)."""

    def __init__(self, *errors: str):
        self.errors = list(errors)
        self.calls: list = []

    def invoke_model(self, *, modelId, body, **kwargs):
        payload = json.loads(body)
        self.calls.append(modelId)
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        if "texts" in payload:
            return {"body": _Body({"embeddings": {"float": [[0.1, 0.2, 0.3] for _ in payload["texts"]]}}),
                    "ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "1000"}}}
        return {"body": _Body({"content": [{"type": "text", "text": "Day 1: a quiet lane."}],
                               "stop_reason": "end_turn", "usage": {"input_tokens": 1000, "output_tokens": 100}})}


class StubSts:
    def __init__(self, seen: list):
        self.seen = seen

    def assume_role(self, **kwargs):
        self.seen.append(kwargs["RoleArn"])
        return {"Credentials": {"AccessKeyId": "AKIA-sat", "SecretAccessKey": "s", "SessionToken": "t"}}


def factory(task: Stub, sat: Stub, seen: list | None = None, regions: list | None = None):
    def make(service, *, creds=None, region=None):
        if service == "sts":
            return StubSts(seen if seen is not None else [])
        if creds is None:
            if regions is not None:
                regions.append(region)
            return task
        return sat

    return make


DENIED = "An error occurred (AccessDeniedException) when calling the InvokeModel operation: Error 002"
THROTTLE = "An error occurred (ThrottlingException): Too many requests"


def dep(key, wire, prio, **kw) -> gw.Deployment:
    facts = {"zz-sat": dict(kind="bedrock", auth_mode="assume_role", account_id_env="BEDROCK_ACCT_PRIMARY",
                            data_scope="aws_only"),
             "zz-task": dict(kind="bedrock", auth_mode="task_role", data_scope="aws_only"),
             "zz-task-west": dict(kind="bedrock", auth_mode="task_role", region="us-west-2", data_scope="aws_only"),
             "zz-vendor": dict(kind="anthropic", auth_mode="api_key", data_scope="vendor_direct")}.get(key, {})
    return gw.Deployment(key, wire, prio, **{**facts, **kw})


def writer(key="zz-writer", *deps, price=(3.0, 15.0)) -> gw.Model:
    return gw.Model(key, "anthropic", "anthropic_native", {"zz-sat": f"catalog.{key}"}, *price, deployments=deps)


def route(*models, role="writer", scopes=None, stage="tp_compose") -> gw.Route:
    return gw.Route(stage, role, tuple(models), None, allowed_scopes=scopes)


def iso(offset_s: float) -> str:
    return datetime.fromtimestamp(NOW + offset_s, tz=timezone.utc).isoformat()


# --- dispatch by deployment ---------------------------------------------------------------------------------------

def test_the_deployment_wire_id_is_called_in_priority_order_and_priced_by_the_core():
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10), dep("zz-task", "wire.task", 20))
    sat, task, seen = Stub(DENIED), Stub(), []
    failures: list = []
    text, call = gw.generate_text(route(model, role="judge"), "s", "u", 100,
                                  client_factory=factory(task, sat, seen), failures=failures)
    assert text == "Day 1: a quiet lane."
    assert sat.calls == ["wire.sat"] and task.calls == ["wire.task"]       # the wire ids, not the catalog profile
    assert seen == ["arn:aws:iam::111111111111:role/ZzInvokerRole"]
    assert (call.account, call.provider, call.wire_id, call.attempt_no) == ("zz-task", "bedrock-native", "wire.task", 2)
    assert (call.cost_usd, call.pricing_mode) == (pytest.approx(0.0045), "catalog")   # 1000 x $3/M + 100 x $15/M
    assert [(f.account, f.provider, f.status, f.error_class) for f in failures] == [
        ("zz-sat", "bedrock-satellite", "refused", "access_denied")]
    row = gw.ledger_row(call, {})
    assert (row.connection_key, row.deployment_wire_id, row.pricing_mode) == ("zz-task", "wire.task", "catalog")


def test_a_price_override_on_the_deployment_wins_and_says_so():
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10, price_overrides=(("price_in_override", 1.0),)))
    _, call = gw.generate_text(route(model), "s", "u", 100, client_factory=factory(Stub(), Stub()))
    assert (call.cost_usd, call.pricing_mode) == (pytest.approx(0.0025), "override")  # 1000 x $1/M + 100 x $15/M
    assert gw.ledger_row(call, {}).pricing_mode == "override"


def test_a_task_role_connection_in_another_region_gets_its_region():
    model = writer("zz-writer", dep("zz-task-west", "wire.west", 10))
    regions: list = []
    _, call = gw.generate_text(route(model, role="judge"), "s", "u", 50,
                               client_factory=factory(Stub(), Stub(), regions=regions))
    assert regions == ["us-west-2"] and call.account == "zz-task-west"


def test_what_tripplanner_cannot_or_may_not_call_is_left_out():
    model = writer("zz-writer",
                   dep("zz-vendor", "wire.vendor", 1),                                # an API connection: AWS only
                   dep("zz-task", "wire.task", 2, served_roles=("embed", "judge")),     # acc2 never serves a writer
                   dep("zz-off", "wire.off", 3, enabled=False),
                   dep("zz-unknown", "wire.unknown", 4, facts=False),
                   dep("zz-sat", "wire.sat", 5, connection_enabled=False))
    plan = gw.deployment_plan(route(model), model)
    assert plan.items == [] and plan.circuit_skips == []
    notes = " | ".join(plan.notes)
    assert "zz-writer@zz-vendor: a anthropic connection (TripPlanner calls Bedrock only)" in notes
    assert "zz-writer@zz-task: the connection does not serve the role writer" in notes
    assert "zz-writer@zz-off: disabled" in notes and "zz-writer@zz-sat: disabled" in notes
    assert "zz-writer@zz-unknown: connection not described" in notes
    task, sat = Stub(), Stub()
    with pytest.raises(bs.BedrockError, match="All models failed") as e:
        gw.generate_text(route(model), "s", "u", 50, client_factory=factory(task, sat))
    assert not isinstance(e.value, gw.CircuitOpen)
    assert task.calls == [] and sat.calls == []


def test_a_stage_data_scope_leaves_out_a_connection_it_does_not_allow():
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 1, data_scope="vendor_direct"), dep("zz-task", "wire.t", 2))
    plan = gw.deployment_plan(route(model, role="judge", scopes=("aws_only",)), model)
    assert [d.connection_key for d in plan.items] == ["zz-task"]
    assert "data scope vendor_direct is not allowed" in " ".join(plan.notes)
    # no stage policy: a vendor_direct connection only as the first candidate, never as a fallback
    later = writer("zz-writer", dep("zz-task", "wire.t", 1), dep("zz-sat", "wire.sat", 2, data_scope="vendor_direct"))
    assert [d.connection_key for d in gw.deployment_plan(route(later, role="judge"), later).items] == ["zz-task"]


# --- the CIS breaker state from the view --------------------------------------------------------------------------

def test_an_open_connection_circuit_skips_acc3_at_once_and_writes_one_refused_row_without_a_connection():
    """Dev today: the LLM account refuses every call (Error 002), CIS tripped the connection circuit. TripPlanner
    makes no attempt on it, degrades with reason 'circuit_open', and its only row has no connection."""
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10, connection_circuit_class="access_denied"))
    sat, seen, failures = Stub(), [], []
    with pytest.raises(gw.CircuitOpen) as e:
        gw.generate_text(route(model), "s", "u", 50, client_factory=factory(Stub(), sat, seen), failures=failures)
    assert (e.value.reason, e.value.error_class) == ("circuit_open", "access_denied")
    assert sat.calls == [] and seen == []                                   # no AssumeRole, no provider call
    [refused] = failures
    row = gw.ledger_row(refused, {"app": "tripplanner"})
    assert (row.account, row.connection_key, row.provider, row.deployment_wire_id) == (None, None, None, None)
    assert (row.status, row.error_class, row.cost_usd, row.attempt_no) == ("refused", "access_denied", 0.0, None)
    assert refused.extra["not_called"] == "circuit_open" and refused.extra["circuit_skips"] == ["zz-sat"]
    assert refused.prompt_hash is not None                                   # the request's prompt, as every row


def test_a_half_open_connection_circuit_still_skips():
    """TripPlanner takes no probe lease: a tripped connection is skipped open or half-open (the view only names it
    while nothing answered on the connection since the refusals)."""
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10, connection_circuit_class="quota_exhausted",
                                    circuit_open_until=NOW - 60))
    plan = gw.deployment_plan(route(model), model)
    assert plan.items == [] and plan.circuit_skips == [("zz-sat", "quota_exhausted")]


@pytest.mark.parametrize("open_until, probe_until, called", [
    (NOW + 120, None, False),         # open
    (NOW - 5, NOW + 60, False),       # half-open, another caller holds the probe
    (NOW - 5, None, True),            # half-open, no probe running: this call may try
    (None, None, True),               # closed
])
def test_the_deployment_circuit_of_the_view(open_until, probe_until, called):
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10, circuit_open_until=open_until, probe_until=probe_until,
                                    last_error_class="throttled"))
    plan = gw.deployment_plan(route(model), model)
    assert bool(plan.items) is called
    if not called:
        assert plan.circuit_skips == [("zz-sat", "throttled")]


def test_one_model_skipped_by_a_circuit_and_the_next_one_answers():
    first = writer("zz-writer-a", dep("zz-sat", "wire.a", 10, connection_circuit_class="access_denied"))
    second = gw.Model("zz-writer-b", "anthropic", "anthropic_native", {}, 1.0, 5.0,
                      deployments=(dep("zz-task", "wire.b", 10),))
    failures: list = []
    _, call = gw.generate_text(route(first, second, role="judge"), "s", "u", 50,
                               client_factory=factory(Stub(), Stub()), failures=failures)
    assert (call.model, call.fallback_used, call.attempt_no) == ("zz-writer-b", True, 1)
    assert [(f.model, f.status, f.account) for f in failures] == [("zz-writer-a", "refused", None)]


# --- the container's own breaker ----------------------------------------------------------------------------------

def test_after_one_account_refusal_the_container_skips_the_deployment():
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10))
    sat = Stub(DENIED, DENIED)
    with pytest.raises(bs.BedrockError, match="All models failed"):
        gw.generate_text(route(model), "s", "u", 50, client_factory=factory(Stub(), sat))
    with pytest.raises(gw.CircuitOpen):
        gw.generate_text(route(model), "s", "u", 50, client_factory=factory(Stub(), sat))
    assert sat.calls == ["wire.sat"]                                         # no attempt past the threshold


def test_throttling_opens_the_container_circuit_after_three_in_a_row():
    model = gw.Model("zz-embed", "cohere", "embed", {}, 0.12, 0.0, deployments=(dep("zz-task", "wire.e", 10),))
    r = route(model, role="embed", stage="tp_search_embed")
    task = Stub(THROTTLE, THROTTLE, THROTTLE)
    for _ in range(3):
        with pytest.raises(bs.BedrockError) as e:
            gw.embed_texts(r, ["x"], client_factory=factory(task, Stub()))
        assert not isinstance(e.value, gw.CircuitOpen)
    with pytest.raises(gw.CircuitOpen):
        gw.embed_texts(r, ["x"], client_factory=factory(task, Stub()))
    assert len(task.calls) == 3


def test_a_success_closes_the_container_circuit_after_the_open_period(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(llm_breaker.local, "_clock", lambda: clock[0])
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10))
    sat = Stub(DENIED)
    with pytest.raises(bs.BedrockError):
        gw.generate_text(route(model), "s", "u", 50, client_factory=factory(Stub(), sat))
    assert gw.deployment_plan(route(model), model).circuit_skips == [("zz-sat", "access_denied")]
    clock[0] += llm_breaker.RULES["access_denied"].open_s                    # half-open: the next call is the probe
    _, call = gw.generate_text(route(model), "s", "u", 50, client_factory=factory(Stub(), sat))
    assert call.account == "zz-sat" and llm_breaker.local.open_class("zz-sat", "zz-writer") is None


def test_a_request_content_refusal_is_not_counted():
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 10))
    sat = Stub(*["ValidationException: Input is too long for requested model"] * 6)
    for _ in range(6):
        with pytest.raises(bs.BedrockError) as e:
            gw.generate_text(route(model), "s", "u", 50, client_factory=factory(Stub(), sat))
        assert not isinstance(e.value, gw.CircuitOpen)
    assert len(sat.calls) == 6


def test_the_container_connection_circuit_needs_two_models_refused():
    a, b, c = (writer(k, dep("zz-sat", f"wire.{k}", 10)) for k in ("zz-a", "zz-b", "zz-c"))
    sat = Stub(DENIED, DENIED)
    for m in (a,):
        with pytest.raises(bs.BedrockError):
            gw.generate_text(route(m), "s", "u", 50, client_factory=factory(Stub(), sat))
    assert gw.deployment_plan(route(c), c).items                             # one model refused: not the account
    with pytest.raises(bs.BedrockError):
        gw.generate_text(route(b), "s", "u", 50, client_factory=factory(Stub(), sat))
    assert gw.deployment_plan(route(c), c).circuit_skips == [("zz-sat", "access_denied")]


def test_local_breaker_state_machine_matches_the_cis_rules():
    h = llm_breaker.Health()
    h = llm_breaker.after_failure(h, "throttled", 0.0)
    h = llm_breaker.after_failure(h, "throttled", 1.0)
    assert llm_breaker.state_of(h, 1.0) == "closed"
    h = llm_breaker.after_failure(h, "throttled", 2.0)
    assert llm_breaker.state_of(h, 2.0) == "open" and h.open_until == 32.0
    h = llm_breaker.after_failure(h, "throttled", 40.0)                     # failed probe: twice as long
    assert h.open_until == 100.0 and h.open_streak == 2
    assert llm_breaker.open_seconds("access_denied", 10) == 1800
    assert llm_breaker.after_success(h, 50.0).open_until is None


# --- reading the view (CIS 341 columns, and before them) ----------------------------------------------------------

def _view_row(position, key, *, deployments, routing=None, deadline=None, scopes=None):
    row = {"stage": "tp_compose", "role": "writer", "is_active": True, "account_route": "acc3",
           "allowed_scopes": scopes, "shadow_model_id": None, "shadow_sample_pct": 0, "position": position,
           "model_key": key, "in_catalog": True, "vendor": "anthropic", "provider": "bedrock",
           "api_style": "anthropic_native", "bedrock_profile_ids": {"acc3": f"catalog.{key}"}, "wire_model": None,
           "max_output_tokens": 4096, "model_enabled": True, "price_in_per_mtok": 3, "price_out_per_mtok": 15,
           "price_cache_read_per_mtok": None, "price_cache_write_per_mtok": None,
           "deployments": json.dumps(deployments), "route_updated_at": None}
    if routing is not None:
        row.update(route_deadline_s=deadline, deployment_routing=json.dumps(routing))
    return row


class PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__(f"pg {sqlstate}")
        self.sqlstate = sqlstate


class ViewDb:
    def __init__(self, rows, *, routing_columns=True):
        self.rows = rows
        self.routing_columns = routing_columns
        self.sql: list = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        if "deployment_routing" in sql and not self.routing_columns:
            raise PgError("42703")
        return self.rows


DEPLOYMENTS = [
    {"connection_key": "acc3", "wire_id": "wire.acc3", "priority": 10, "enabled": True, "price_in_override": None,
     "price_out_override": 12.5, "price_cache_read_override": None, "price_cache_write_override": None,
     "price_cache_write_1h_override": None},
    {"connection_key": "acc2", "wire_id": "wire.acc2", "priority": 20, "enabled": False},
    {"connection_key": "zz-vendor", "wire_id": "wire.vendor", "priority": 30, "enabled": True},
]
ROUTING = [
    {"connection_key": "acc3", "in_registry": True, "kind": "bedrock", "auth_mode": "assume_role",
     "account_id_env": "BEDROCK_ACCT_PRIMARY", "region": "us-west-1", "data_scope": "aws_only",
     "served_roles": ["writer", "embed"], "connection_enabled": True, "access_state": "access_denied",
     "circuit_open_until": iso(240), "probe_until": None, "last_error_class": "access_denied",
     "consecutive_failures": 1, "connection_circuit_class": "access_denied",
     "connection_circuit_open_until": iso(900)},
    {"connection_key": "acc2", "in_registry": True, "kind": "bedrock", "auth_mode": "task_role",
     "account_id_env": None, "region": "us-west-1", "data_scope": "aws_only", "served_roles": ["embed", "judge"],
     "connection_enabled": True, "access_state": "ok", "circuit_open_until": None, "probe_until": None,
     "last_error_class": None, "consecutive_failures": None, "connection_circuit_class": None,
     "connection_circuit_open_until": None},
    {"connection_key": "zz-vendor", "in_registry": True, "kind": "anthropic", "auth_mode": "api_key",
     "account_id_env": None, "region": None, "data_scope": "vendor_direct", "served_roles": None,
     "connection_enabled": False, "access_state": "unknown", "circuit_open_until": None, "probe_until": None,
     "last_error_class": None, "consecutive_failures": None, "connection_circuit_class": None,
     "connection_circuit_open_until": None},
]


async def test_the_341_columns_give_deployments_facts_circuits_deadline_and_scopes():
    db = ViewDb([_view_row(0, "zz-writer", deployments=DEPLOYMENTS, routing=ROUTING, deadline=17,
                           scopes=["aws_only"])])
    r = await gw.load_route(db, "tp_compose")
    assert "deployment_routing" in db.sql[0] and contract.GATEWAY_CONFIG_VIEW in db.sql[0]
    assert (r.deadline_s, r.allowed_scopes) == (17, ("aws_only",))
    acc3, acc2, vendor = r.models[0].deployments
    assert (acc3.wire_id, acc3.auth_mode, acc3.account_id_env, acc3.served_roles) == (
        "wire.acc3", "assume_role", "BEDROCK_ACCT_PRIMARY", ("writer", "embed"))
    assert acc3.price_overrides == (("price_out_override", 12.5),)
    assert acc3.circuit_open_until == pytest.approx(NOW + 240) and acc3.connection_circuit_class == "access_denied"
    assert (acc2.enabled, acc2.auth_mode) == (False, "task_role")
    assert (vendor.kind, vendor.connection_enabled, vendor.data_scope) == ("anthropic", False, "vendor_direct")
    plan = gw.deployment_plan(r, r.models[0])
    assert plan.items == [] and plan.circuit_skips == [("acc3", "access_denied")]


async def test_before_341_the_323_columns_run_on_the_legacy_connection_facts(monkeypatch, capsys):
    now = [10.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: now[0])
    db = ViewDb([_view_row(0, "zz-writer", deployments=DEPLOYMENTS)], routing_columns=False)
    r = await gw.load_route(db, "tp_compose")
    # the 350 set, then the 341 set (both 42703), then the 323 contract columns (AA-1027 E4 column sets)
    assert db.sql == [gw.GATEWAY_PARAMS_SQL, gw.GATEWAY_ROUTING_SQL, contract.GATEWAY_CONFIG_SQL]
    assert "CIS migration 341" in capsys.readouterr().out
    acc3, acc2, vendor = r.models[0].deployments
    assert (acc3.wire_id, acc3.auth_mode, acc3.circuit_open_until) == ("wire.acc3", "assume_role", None)
    assert (acc2.auth_mode, vendor.facts) == ("task_role", False)
    assert [d.connection_key for d in gw.deployment_plan(r, r.models[0]).items] == ["acc3"]
    gw._cache.clear()
    await gw.load_route(db, "tp_compose")                                    # within VIEW_RETRY_SECONDS
    assert db.sql[3:] == [contract.GATEWAY_CONFIG_SQL]


async def test_a_model_with_no_deployment_row_runs_on_its_catalog_profiles():
    db = ViewDb([_view_row(0, "zz-writer", deployments=[], routing=[])])
    r = await gw.load_route(db, "tp_compose")
    [only] = gw.model_deployments(r.models[0], r.account_route)
    assert (only.connection_key, only.wire_id, only.auth_mode) == ("acc3", "catalog.zz-writer", "assume_role")


def test_the_route_table_has_only_the_live_stages():
    # AA-1041 slice 2: + tp_plan_chat (on the tp_compose route's models until CIS seeds its own row).
    assert set(gw.BUILTIN_ROUTES) == {"tp_compose", "tp_search_embed", "tp_plan_chat"}
    assert gw.ONLINE_STAGES == ("tp_compose", "tp_search_embed", "tp_plan_chat")


def test_no_supported_account_list_is_left():
    assert not hasattr(gw, "SUPPORTED_ACCOUNTS") and not hasattr(gw, "_accounts")


def test_the_not_called_row_keeps_a_ledger_class():
    model = writer("zz-writer")
    call = gw._not_called(route(model), model, 0, [("zz-sat", "not-a-class")])
    assert call.error_class == "other" and call.status == ledger.STATUS_REFUSED


def test_epoch_reads_iso_and_ignores_garbage():
    assert gw._epoch(iso(5)) == pytest.approx(NOW + 5)
    assert gw._epoch((datetime.fromtimestamp(NOW, tz=timezone.utc) + timedelta(seconds=1))) == pytest.approx(NOW + 1)
    assert gw._epoch("not a time") is None and gw._epoch(None) is None
