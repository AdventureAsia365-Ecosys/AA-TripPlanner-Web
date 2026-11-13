"""AA-1038 R5 slice 3 (root ADR 0019 d11): a traveller's trip request to ONE agency tenant, as lead.submitted.v1.

Contract: AA-Ecosys docs/contracts/lead.submitted.v1.openapi.yaml (component LeadSubmitted; the pull API for the CRM).

Where a lead comes from. "Send to advisor" on an agency tenant's planner (/{slug}/plan): the request carries the BFF's
verified tenant assertion, so the connection is bound to that tenant (tenant.current() source "assertion"). The bare
path and the AA demo tenant keep the demo notice (410 b2b_only in handler.py): AA keeps no traveller record of its own.

Where it goes. CIS decides per tenant (catalog.v_tenant_tripplanner_v1, CIS 390): ``crm`` when the tenant holds the CRM
add-on (crm.core), else the site's ``webhook`` or ``email`` when set, else ``none``. TripPlanner refuses BEFORE storing
anything when it could not deliver: no route (409 lead_route_missing), the view not readable yet (503
lead_route_unavailable), a channel this deployment cannot use (503 lead_channel_unavailable: the CRM pull not switched
on with ``config.LEAD_CRM_PULL_ENABLED`` or the CRM has not listed THIS tenant's leads within
``config.LEAD_CRM_PULL_FRESH_S`` (``crm_pull_fresh``: the agency is locked, offboarding or not provisioned yet), no
webhook signing key, no e-mail sender). Before storing, the handler also counts the send per guest session and per
traveller IP (``ratelimit`` scopes send / send_ip, 429 rate_limited) and caps the tenant's leads per 24 hours
(``config.TP_LEAD_TENANT_DAILY_CAP``, 429 lead_cap_reached; counted under a per-tenant transaction advisory lock,
``TENANT_CAP_LOCK_SQL``, so concurrent sends never pass the cap). A name must hold a letter or digit
(``has_name_token``: what the CRM needs to file the traveller). The traveller is a guest: no tripplanner.customers row,
no registration, no session link.

What is kept, and how long. One tripplanner.outbox row (event lead.submitted, tp-026) in the same transaction as the
trip's ``sent`` event (the AA-676 pattern: a double click answers the same hand-off, a send after an edit is a new
lead). The payload holds the traveller's name, e-mail, optional phone, party, trip summary and consent; it is
redacted at delivery (``redacted_payload``: ids only) and at the latest ``config.LEAD_HOLD_DAYS`` (<= 7) after it was
written, undelivered (``tp_lead_deliver`` in the jobs Lambda, daily, with the conservative ``hold_cutoff`` that
survives one failed tick). Never in a lead: the guest session or token, an access token, an IP, a user agent or
any price.

Delivery:
  * crm: the CRM pulls (``list_pending``) and acknowledges (``acknowledge``) over HTTP with an M2M token
    (backend/shared/m2m_auth.py), one tenant per call, under that tenant's RLS context, only for a tenant whose route
    is crm now (a leaked token reaches no tenant that left the CRM add-on); each list call is the tenant's beacon
    (``mark_crm_pull``);
  * webhook: one signed POST right after the commit (``deliver_webhook``, ``config.LEAD_WEBHOOK_TIMEOUT_S``), then the
    daily run retries; ``X-AA-Signature: t=<unix s>,v1=<hex HMAC-SHA256(key, "<t>." + body)>``, key per tenant =
    HMAC-SHA256(master, "aa-tripplanner lead webhook v1:" + tenant_id) as its 64-character lowercase hex text, used as
    ASCII bytes (``webhook_key``; AA hands the tenant its key); only a public address is ever posted to
    (``urllib_post`` refuses a private, loopback or link-local one);
  * email: one SES e-mail right after the commit (``deliver_email``), Reply-To the traveller, the same retries.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from backend import config
from backend.shared import observability, ratelimit, settings

EVENT = "lead.submitted"
CONTRACT = "lead.submitted.v1"
VERSION = "1.0"
SCHEMA_MAJOR = 1
CHANNELS = ("crm", "webhook", "email")
BUDGET_BANDS = ("under_1000", "1000_2000", "2000_3500", "3500_5000", "over_5000", "not_sure")
BUDGET_LABELS = {"under_1000": "under 1,000", "1000_2000": "1,000 to 2,000", "2000_3500": "2,000 to 3,500",
                 "3500_5000": "3,500 to 5,000", "over_5000": "over 5,000", "not_sure": "not sure yet"}
LOCALES = ("en", "vi")
MAX_NAME, MAX_EMAIL, MAX_PHONE, MAX_MESSAGE = 120, 254, 40, 1000
MAX_ADULTS, MAX_CHILDREN = 50, 20
MAX_TOURS, MAX_STOPS, MAX_COUNTRIES, MAX_DAYS = 30, 12, 12, 90
PAGE_MAX = 50
WEBHOOK_KEY_LABEL = b"aa-tripplanner lead webhook v1:"

VIEW = "catalog.v_tenant_tripplanner_v1"
# Column names, never * (the CIS test test_aa1038_migration_390.py runs this exact statement as tripplanner).
ROUTE_SQL = (f"SELECT tenant_id, slug, name, kind, served, in_service, crm_core, lead_channel, lead_webhook_url, "
             f"lead_email FROM {VIEW} WHERE tenant_id = $1")
TOURS_SQL = ("SELECT source_system, source_id, tour_id::text AS tour_id, title, country FROM catalog.v_tour "
             "WHERE source_id = ANY($1::text[]) OR tour_id::text = ANY($1::text[])")
INSERT_SQL = (
    "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, delivery_channel) "
    "VALUES ($1, $2, $3, $4, $5::jsonb, $6) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id")
# The tenant's pending CRM leads; the tp-025 shadow policy filters to the bound tenant as well (defence in depth).
PENDING_SQL = (
    "SELECT id, payload FROM tripplanner.outbox WHERE event = 'lead.submitted' AND delivery_channel = 'crm' "
    "AND delivered_at IS NULL AND redacted_at IS NULL AND tenant_id = $1 ORDER BY id LIMIT $2")
LEAD_ROW_SQL = (
    "SELECT id, payload, delivered_at, redacted_at, delivery_error FROM tripplanner.outbox "
    "WHERE idempotency_key = $1 AND event = 'lead.submitted' AND delivery_channel = 'crm' AND tenant_id = $2 "
    "FOR UPDATE")
DELIVERED_SQL = (
    "UPDATE tripplanner.outbox SET delivered_at = now(), redacted_at = COALESCE(redacted_at, now()), "
    "payload = $2::jsonb, delivery_error = NULL WHERE id = $1")
REJECTED_SQL = (
    "UPDATE tripplanner.outbox SET redacted_at = COALESCE(redacted_at, now()), payload = $2::jsonb, "
    "delivery_error = $3 WHERE id = $1")
FAILED_ATTEMPT_SQL = (
    "UPDATE tripplanner.outbox SET delivery_attempts = delivery_attempts + 1, delivery_error = $2 WHERE id = $1")
# The tenant's leads of the last 24 hours (the daily cap); the connection is bound to the tenant as well. The count
# runs after TENANT_CAP_LOCK_SQL in the send transaction: two concurrent sends of one tenant (different trips, so the
# per-trip lock does not order them) would otherwise both count N-1 and both store, one lead past the cap each time.
TENANT_CAP_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('tp_lead_cap:' || $1::text))"
TENANT_LEADS_24H_SQL = (
    "SELECT count(*)::int FROM tripplanner.outbox WHERE event = 'lead.submitted' AND tenant_id = $1 "
    "AND created_at > now() - interval '24 hours'")

# The expiry schedule (AA-CIS-Infra accounts/aa365/tripplanner_jobs.tf: EventBridge cron(30 19 * * ? *), one tick a
# day) and how late a tick may run (Lambda start, earlier jobs of the same tick). hold_cutoff keeps both inside the
# hold. A change of the schedule in Infra changes EXPIRY_TICK_INTERVAL here first (test_aa1038_lead_deliver.py).
EXPIRY_TICK_INTERVAL = timedelta(days=1)
EXPIRY_TICK_MARGIN = timedelta(hours=1)
# Ticks the hold absorbs on top of the one between two runs: the tick that should end a lead's hold may fail (the
# database unreachable for longer than lead_deliver._tried covers, an RDS reboot or failover, a cold-start pool
# error) or not reach the job (run_due stops when the earlier jobs use up the invocation). The failed job is re-queued,
# and the scheduler wakes the jobs Lambda only at the next daily tick, so that tick must still be inside the hold.
# Two failed ticks in a row is the residual risk (each one already fails loudly: the jobs Lambda error alarm).
EXPIRY_TICKS_ABSORBED = 1

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,63}$")
# E.164 with its country code (contract Traveller.phone pattern): the CRM normalises a phone without a region, so a
# local number ("090 ...") would not survive there. Spaces, brackets and dashes are kept as the traveller typed them.
_PHONE_RE = re.compile(r"^\+[1-9][0-9 ()-]{5,39}$")
_CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class LeadRefused(Exception):
    """The request is refused before anything is stored. ``code`` is the stable error, never a value."""

    def __init__(self, status: int, code: str, fields: Optional[list[str]] = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.fields = fields or []

    def body(self) -> dict:
        out: dict = {"error": self.code}
        if self.fields:
            out["fields"] = self.fields
        return out


@dataclass(frozen=True)
class Route:
    tenant_id: str
    slug: str
    name: str
    channel: str                      # crm | webhook | email
    webhook_url: Optional[str] = None
    email: Optional[str] = None


# --- the traveller's answers ----------------------------------------------------------------------------------------

def _text(value: Any, max_len: int) -> Optional[str]:
    """Control characters out, NFC, trimmed, at most ``max_len`` code points: the CRM's Stage 1 ``clean_text`` rule
    byte for byte (a name NFC lengthens past the limit is refused here, never rejected later by the CRM). Shared test
    vectors: tests/contracts/lead.submitted.v1.traveller-rules.json (root docs/contracts/rules/)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError
    text = unicodedata.normalize("NFC", _CONTROL_RE.sub("", value)).strip()
    if len(text) > max_len:
        raise ValueError
    return text or None


def has_name_token(text: str) -> bool:
    """The contract's name token rule: after NFKD, at least one letter or digit (Unicode category L or N; Python's
    ``str.isalnum`` is exactly that set). It is what the agency's CRM needs to file the traveller (AA-Partner
    ``blind_index.name_tokens``, which splits the NFKD text on every non-word character and on ``_``): a name of
    punctuation, symbols or emoji only ("-", "...", an em dash) is refused here (422 invalid_lead, fields [name]) before
    anything is stored, never delivered and then lost at the agency. Shared test vectors: name cases of
    tests/contracts/lead.submitted.v1.traveller-rules.json."""
    return any(c.isalnum() for c in unicodedata.normalize("NFKD", text))


def _int(value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError
    n = int(value)
    if not lo <= n <= hi:
        raise ValueError
    return n


def validate(raw: Any, *, today: Optional[date] = None) -> dict:
    """The normalised answers of the lead form, or LeadRefused 422 (consent_required / invalid_lead + field codes)."""
    if not isinstance(raw, dict):
        raise LeadRefused(422, "invalid_lead", ["lead"])
    consent = raw.get("consent") if isinstance(raw.get("consent"), dict) else {}
    if consent.get("contact") is not True:
        raise LeadRefused(422, "consent_required")
    traveller = raw.get("traveller") if isinstance(raw.get("traveller"), dict) else {}
    party = raw.get("party") if isinstance(raw.get("party"), dict) else {}
    trip = raw.get("trip") if isinstance(raw.get("trip"), dict) else {}
    bad: list[str] = []
    out: dict = {}

    def take(name: str, fn: Callable[[], Any]) -> None:
        try:
            out[name] = fn()
        except (ValueError, TypeError):
            bad.append(name)

    def name() -> str:
        v = _text(traveller.get("name"), MAX_NAME)
        if not v or not has_name_token(v):
            raise ValueError
        return v

    def email() -> str:
        # Lower-case FIRST, then the length and the pattern on the value we send: str.lower() turns U+0130 (capital I
        # with dot above) into two code points, so checking before lower-casing let a 64-code-point local part become
        # 65 and the CRM rejected the lead after it was stored (S272 review 4). Shared vectors: dotted_capital_i_*.
        v = _text(traveller.get("email"), MAX_EMAIL)
        v = v.lower() if v else v
        if not v or len(v) > MAX_EMAIL or not _EMAIL_RE.match(v):
            raise ValueError
        return v

    def phone() -> Optional[str]:
        v = _text(traveller.get("phone"), MAX_PHONE)
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError
        return v

    def child_ages() -> list[int]:
        v = party.get("child_ages") or []
        if not isinstance(v, list) or len(v) > MAX_CHILDREN:
            raise ValueError
        return [_int(x, 0, 17) for x in v]

    def start_date() -> Optional[str]:
        v = trip.get("start_date")
        if v in (None, ""):
            return None
        d = date.fromisoformat(str(v))
        if d < (today or datetime.now(timezone.utc).date()):
            raise ValueError
        return d.isoformat()

    def budget() -> Optional[str]:
        v = trip.get("budget_band")
        if v in (None, ""):
            return None
        if v not in BUDGET_BANDS:
            raise ValueError
        return v

    def marketing() -> bool:
        v = consent.get("marketing", False)
        if not isinstance(v, bool):
            raise ValueError
        return v

    take("name", name)
    take("email", email)
    take("phone", phone)
    take("locale", lambda: traveller.get("locale") if traveller.get("locale") in LOCALES else "en")
    take("adults", lambda: _int(party.get("adults", 1), 1, MAX_ADULTS))
    take("child_ages", child_ages)
    take("start_date", start_date)
    take("budget_band", budget)
    take("message", lambda: _text(trip.get("message"), MAX_MESSAGE))
    take("marketing", marketing)
    if bad:
        raise LeadRefused(422, "invalid_lead", bad)
    return out


# --- the tenant's route ---------------------------------------------------------------------------------------------

def webhook_master_key() -> Optional[bytes]:
    """The webhook master secret, or None when this deployment has none (the webhook channel is then unavailable)."""
    if config.TP_LEAD_WEBHOOK_KEY:
        return config.TP_LEAD_WEBHOOK_KEY.strip().encode()
    return _secret_key()


_secret_cache: dict[str, Any] = {"value": None, "until": 0.0}


def _secret_key() -> Optional[bytes]:
    """The Secrets Manager master key on Lambda (cached 10 minutes; a failed read is cached 60 s as None)."""
    import os

    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or not (config.ENVIRONMENT or "").strip():
        return None
    now = time.monotonic()
    if now < _secret_cache["until"]:
        return _secret_cache["value"]
    from backend.shared import auth

    value: Optional[bytes] = None
    try:
        raw = auth._fetch_secret(config.LEAD_WEBHOOK_KEY_SECRET_TEMPLATE.format(  # noqa: SLF001
            environment=config.ENVIRONMENT.strip()))
        if raw and raw.strip() and raw.strip() != "REPLACE_ME":
            value = raw.strip().encode()
    except Exception as e:  # noqa: BLE001 — never the value
        observability.emit("lead_webhook_key_unavailable", error=type(e).__name__)
    _secret_cache.update(value=value, until=now + (600.0 if value else 60.0))
    return value


def webhook_key(master: bytes, tenant_id: str) -> bytes:
    """The tenant's webhook signing key (hex of HMAC-SHA256(master, label + tenant id), as bytes)."""
    return hmac.new(master, WEBHOOK_KEY_LABEL + tenant_id.encode(), hashlib.sha256).hexdigest().encode()


def channel_ready(route: Route, *, resolve: Optional[Resolver] = None) -> bool:
    """True only when this deployment can hand the lead over on the route's channel (else 503, nothing stored). For
    crm this is the deployment switch only; the per-tenant part is :func:`crm_pull_fresh`. For webhook the target
    must also be an https URL whose host is a public address now (:func:`webhook_target_problem`): a route aimed at a
    private, loopback or link-local address (the CIS 390 CHECK accepts ``https://127.0.0.1/hook``) could never be
    delivered, so the traveller's details are never stored for it."""
    if route.channel == "crm":
        return config.LEAD_CRM_PULL_ENABLED
    if route.channel == "webhook":
        if not route.webhook_url or webhook_master_key() is None:
            return False
        problem = webhook_target_problem(route.webhook_url, resolve=resolve)
        if problem is not None:
            observability.emit("lead_webhook_target_refused", tenant_id=route.tenant_id, error=problem)
            return False
        return True
    if route.channel == "email":
        return bool(route.email) and bool(config.TP_LEAD_EMAIL_FROM)
    return False


async def crm_pull_fresh(conn: Any, tenant_id: str) -> bool:
    """True when the CRM listed this tenant's leads within ``config.LEAD_CRM_PULL_FRESH_S`` (the beacon of
    :func:`mark_crm_pull`). A read error is False: the crm channel then refuses (nothing stored), never stores blind."""
    try:
        return await ratelimit.seen_within(conn, ratelimit.SCOPE_CRM_PULL, ratelimit.KIND_TENANT, tenant_id,
                                           config.LEAD_CRM_PULL_FRESH_S)
    except Exception as e:  # noqa: BLE001
        observability.emit("lead_crm_beacon_unreadable", error=type(e).__name__)
        return False


async def mark_crm_pull(conn: Any, tenant_id: str) -> None:
    """The CRM listed this tenant's leads (serve_m2m): the crm channel may take this tenant's next leads."""
    await ratelimit.touch(conn, ratelimit.SCOPE_CRM_PULL, ratelimit.KIND_TENANT, tenant_id)


async def tenant_leads_24h(conn: Any, tenant_id: str) -> int:
    """The tenant's leads of the last 24 hours, counted under the tenant's cap lock (``TENANT_CAP_LOCK_SQL``, held to
    the end of the transaction). Call it only inside the send transaction, after the per-trip lock (always that order,
    so two sends never wait on each other crosswise): the lock serialises the count-then-store of one tenant, so the
    cap is exact under concurrency."""
    await conn.execute(TENANT_CAP_LOCK_SQL, tenant_id)
    return int(await conn.fetchval(TENANT_LEADS_24H_SQL, tenant_id) or 0)


async def load_route(conn: Any, tenant_id: str) -> Route:
    """The tenant's lead route from CIS, or LeadRefused (nothing stored)."""
    try:
        row = await conn.fetchrow(ROUTE_SQL, tenant_id)
    except Exception as e:  # noqa: BLE001 — 42P01 / 42501 / 42703 before CIS 390, or a read error
        observability.emit("lead_route_unreadable", error=type(e).__name__, sqlstate=getattr(e, "sqlstate", None))
        raise LeadRefused(503, "lead_route_unavailable") from None
    if row is None or not row["served"] or not row["in_service"]:
        raise LeadRefused(404, "unknown_tenant")
    channel = row["lead_channel"]
    if channel not in CHANNELS:
        raise LeadRefused(409, "lead_route_missing")
    return Route(str(row["tenant_id"]), row["slug"], row["name"], channel,
                 row["lead_webhook_url"] if channel == "webhook" else None,
                 row["lead_email"] if channel == "email" else None)


# --- the lead ---------------------------------------------------------------------------------------------------------

async def trip_summary(conn: Any, itinerary: list[dict], *, extra_days: int = 0) -> dict:
    """Days, countries, AA tour keys and titles, and the stops per day of the served itinerary. The catalog read is
    best effort (a savepoint when the connection has transactions): an unreadable catalog leaves tours and countries
    empty, it never refuses the request."""
    keys = sorted({str(e["source_tour_id"]) for e in itinerary if e.get("source_tour_id")})
    tours: list[dict] = []
    countries: list[str] = []
    if keys:
        try:
            if hasattr(conn, "transaction"):
                async with conn.transaction():
                    rows = await conn.fetch(TOURS_SQL, keys)
            else:
                rows = await conn.fetch(TOURS_SQL, keys)
        except Exception as e:  # noqa: BLE001
            observability.emit("lead_tours_unreadable", error=type(e).__name__)
            rows = []
        seen: set = set()
        for r in rows or []:
            k = (r["source_system"], r["source_id"])
            if k not in seen and len(tours) < MAX_TOURS:
                seen.add(k)
                tours.append({"source_system": str(r["source_system"])[:40], "source_id": str(r["source_id"])[:120],
                              "title": str(r["title"] or r["source_id"])[:300]})
            c = (r["country"] or "").strip()
            if c and c not in countries and len(countries) < MAX_COUNTRIES:
                countries.append(c[:80])
    by_day: dict[int, list[str]] = {}
    for e in sorted(itinerary, key=lambda x: int(x["day"])):
        stop = (e.get("destination_name") or e.get("name") or "").strip()[:120]
        stops = by_day.setdefault(int(e["day"]), [])
        if stop and stop not in stops and len(stops) < MAX_STOPS:
            stops.append(stop)
    day_plan = [{"day": d, "stops": s} for d, s in sorted(by_day.items()) if 1 <= d <= MAX_DAYS][:MAX_DAYS]
    days = min(MAX_DAYS, max([d for d in by_day if 1 <= d <= MAX_DAYS], default=0) + max(0, int(extra_days)) or 1)
    return {"days": days, "countries": countries or ["Not specified"], "tours": tours, "day_plan": day_plan}


def build_lead(*, route: Route, trip_id: str, answers: dict, summary: dict, lead_id: Optional[str] = None,
               now: Optional[datetime] = None) -> dict:
    """The LeadSubmitted object (contract lead.submitted.v1)."""
    lid = lead_id or str(uuid.uuid4())
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "contract": CONTRACT,
        "version": VERSION,
        "lead_id": lid,
        "idempotency_key": f"tp-lead:{lid}",
        "submitted_at": stamp,
        "tenant": {"tenant_id": route.tenant_id, "slug": route.slug},
        "source": {"app": "tripplanner", "trip_ref": str(trip_id)},
        "traveller": {"name": answers["name"], "email": answers["email"], "phone": answers["phone"],
                      "locale": answers["locale"]},
        "party": {"adults": answers["adults"], "child_ages": answers["child_ages"]},
        "trip": {"days": summary["days"], "countries": summary["countries"], "start_date": answers["start_date"],
                 "budget_band": answers["budget_band"], "message": answers["message"], "tours": summary["tours"],
                 "day_plan": summary["day_plan"]},
        "consent": {"contact": True, "marketing": answers["marketing"], "given_at": stamp},
    }


REDACT_KEEP = ("contract", "version", "lead_id", "idempotency_key", "submitted_at", "tenant")


def redacted_payload(payload: Any) -> dict:
    """What stays after delivery or the hold window: ids and the tenant, no traveller, party, trip or consent."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    payload = payload if isinstance(payload, dict) else {}
    out = {k: payload[k] for k in REDACT_KEEP if k in payload}
    out["redacted"] = True
    return out


async def write_lead(conn: Any, *, trip_id: str, payload: dict, channel: str) -> Optional[int]:
    """The outbox row (same transaction as the `sent` event). The tenant comes from the bound context (column default
    of tp-025), never from the payload."""
    return await conn.fetchval(INSERT_SQL, EVENT, SCHEMA_MAJOR, payload["idempotency_key"], trip_id,
                               json.dumps(payload), channel)


# --- the CRM pull (M2M) -----------------------------------------------------------------------------------------------

def _payload(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


async def list_pending(conn: Any, tenant_id: str, limit: int) -> dict:
    """LeadPage of the tenant's pending CRM leads (oldest first). The connection is bound to the tenant."""
    rows = await conn.fetch(PENDING_SQL, tenant_id, limit + 1)
    leads = [_payload(r["payload"]) for r in rows[:limit]]
    # Defence in depth: a row of another tenant never leaves, whatever the database returned.
    leads = [p for p in leads if (p.get("tenant") or {}).get("tenant_id") == tenant_id]
    return {"leads": leads, "more": len(rows) > limit}


async def acknowledge(conn: Any, tenant_id: str, lead_id: str, outcome: str, reason: Optional[str]) -> Optional[dict]:
    """LeadAck, or None when the tenant has no such CRM lead (404). Idempotent; the payload is redacted either way.

    A lead in a final state never changes again: delivered (any later ack answers ``already``), rejected (any later
    ack, ``stored`` included, answers ``already``), expired then acked ``rejected`` (answers ``expired``, the expiry
    record is kept). Only a pending lead, or an expired one the CRM did store, changes state."""
    async with conn.transaction():
        row = await conn.fetchrow(LEAD_ROW_SQL, f"tp-lead:{lead_id}", tenant_id)
        if row is None:
            return None
        payload = _payload(row["payload"])
        error = str(row["delivery_error"] or "")
        redacted = row["redacted_at"] is not None
        if row["delivered_at"] is not None:
            return {"lead_id": lead_id, "state": "delivered", "already": True}
        if redacted and error.startswith("rejected"):
            return {"lead_id": lead_id, "state": "rejected", "already": True}
        expired = redacted and error == "expired"
        if outcome == "stored":
            # A pending lead, or one the hold ended while the CRM was storing it: the agency has it, say so.
            await conn.execute(DELIVERED_SQL, row["id"], json.dumps(redacted_payload(payload)))
            observability.emit("lead_delivered", channel="crm", tenant_id=tenant_id, after_expiry=expired)
            return {"lead_id": lead_id, "state": "delivered", "already": False}
        if expired:
            return {"lead_id": lead_id, "state": "expired", "already": True}
        await conn.execute(REJECTED_SQL, row["id"], json.dumps(redacted_payload(payload)),
                           f"rejected:{reason}" if reason else "rejected")
        # A rejected lead is a traveller's request the agency will never see: an ERROR line now (ids and the reason
        # code only), and tp_lead_deliver's next run counts it as attention (the daily tick fails loudly).
        observability.emit("lead_rejected", level="error", channel="crm", tenant_id=tenant_id, reason=reason)
        return {"lead_id": lead_id, "state": "rejected", "already": False}


def valid_ack(body: Any) -> tuple[str, Optional[str]]:
    """(outcome, reason) of a LeadAckIn body, or ValueError."""
    if not isinstance(body, dict) or body.get("outcome") not in ("stored", "rejected"):
        raise ValueError
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or not _CODE_RE.match(reason)):
        raise ValueError
    return body["outcome"], reason


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


# --- webhook and e-mail delivery ----------------------------------------------------------------------------------------

def signature(key: bytes, body: bytes, timestamp: int) -> str:
    digest = hmac.new(key, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


Poster = Callable[[str, bytes, dict, float], int]


class UrlNotPublic(Exception):
    """The webhook host is not a public address (private, loopback, link-local, reserved): a permanent route defect
    (error code ``webhook_urlnotpublic``) until AA staff change the URL."""


class UrlUnresolvable(UrlNotPublic):
    """The webhook host did not resolve (in time): possibly transient (error code ``webhook_urlunresolvable``)."""


# Error codes of deliver_webhook for the two cases (``webhook_`` + the exception class name, lower case).
WEBHOOK_URL_NOT_PUBLIC = "webhook_urlnotpublic"
WEBHOOK_URL_UNRESOLVABLE = "webhook_urlunresolvable"

Resolver = Callable[[str], list[str]]
_resolver_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="lead-dns")


def resolve_host(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def resolve_host_bounded(host: str) -> list[str]:
    """:func:`resolve_host` within ``config.LEAD_WEBHOOK_RESOLVE_TIMEOUT_S`` (getaddrinfo has no timeout of its own):
    the send path checks the webhook target before storing, and a slow resolver must not hold the request."""
    future = _resolver_pool.submit(resolve_host, host)
    try:
        return future.result(timeout=config.LEAD_WEBHOOK_RESOLVE_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        raise OSError("resolve timeout") from None


def webhook_target_problem(url: str, *, resolve: Optional[Resolver] = None) -> Optional[str]:
    """None when ``url`` is https and its host resolves to public addresses only, else the error code
    (``webhook_url_not_https`` / ``webhook_urlnotpublic`` / ``webhook_urlunresolvable``). An IP literal is never
    looked up; a host name is resolved within the bound (:func:`resolve_host_bounded`)."""
    if not url.startswith("https://"):
        return "webhook_url_not_https"
    try:
        public_address_check(url, resolve=resolve or resolve_host_bounded)
    except UrlUnresolvable:
        return WEBHOOK_URL_UNRESOLVABLE
    except UrlNotPublic:
        return WEBHOOK_URL_NOT_PUBLIC
    return None


def public_address_check(url: str, *, resolve: Resolver = resolve_host) -> None:
    """Raise UrlNotPublic unless EVERY address of the URL's host is a global unicast address (no 10/8, 172.16/12,
    192.168/16, 127/8, 169.254/16, ::1, fc00::/7, fe80::/10, 100.64/10, the VPC resolver ...). The webhook URL is set
    by AA staff today (CIS Tenant 360, CHECK ^https://), but a lead must never be posted into AA's own network."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host:
        raise UrlNotPublic("no host")
    try:
        addresses = [host] if _is_ip(host) else resolve(host)
    except OSError:
        raise UrlUnresolvable("unresolvable") from None
    if not addresses:
        raise UrlUnresolvable("unresolvable")
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise UrlNotPublic("not public")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def urllib_post(url: str, body: bytes, headers: dict, timeout: float, *, resolve: Resolver = resolve_host) -> int:
    """POST and return the HTTP status (redirects are not followed: a 3xx is a failure). The host must resolve to
    public addresses only (UrlNotPublic otherwise: error code webhook_urlnotpublic). The check and the connection
    resolve separately; a DNS answer that flips in between is the residual risk, bounded by the https-only target."""
    public_address_check(url, resolve=resolve)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — https only (CIS 390 CHECK)
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def deliver_webhook(url: str, payload: dict, tenant_id: str, *, post: Poster = urllib_post,
                    now: Optional[int] = None, timeout: Optional[float] = None) -> Optional[str]:
    """None when the tenant answered 2xx, else an error code (never the URL, the body or an exception text)."""
    master = webhook_master_key()
    if master is None:
        return "webhook_key_unavailable"
    if not url.startswith("https://"):
        return "webhook_url_not_https"
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ts = int(now if now is not None else time.time())
    headers = {"Content-Type": "application/json", "X-AA-Contract": CONTRACT,
               "Idempotency-Key": payload["idempotency_key"],
               "X-AA-Signature": signature(webhook_key(master, tenant_id), body, ts)}
    try:
        status = post(url, body, headers, timeout if timeout is not None else config.LEAD_WEBHOOK_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        return f"webhook_{type(e).__name__.lower()}"[:64]
    return None if 200 <= status < 300 else f"webhook_http_{status}"


def email_text(payload: dict, tenant_name: str) -> tuple[str, str]:
    """(subject, plain text) of the e-mail channel. The traveller's own words are quoted as they are."""
    t, trip, party = payload["traveller"], payload["trip"], payload["party"]
    lines = [f"New trip request from your {tenant_name} trip planner.", "",
             f"Name: {t['name']}", f"E-mail: {t['email']}"]
    if t.get("phone"):
        lines.append(f"Phone: {t['phone']}")
    kids = f", children aged {', '.join(str(a) for a in party['child_ages'])}" if party["child_ages"] else ""
    lines += [f"Travellers: {party['adults']} adult(s){kids}",
              f"Trip: {trip['days']} day(s) in {', '.join(trip['countries'])}"]
    if trip.get("start_date"):
        lines.append(f"Start: {trip['start_date']}")
    if trip.get("budget_band"):
        lines.append(f"Budget per person (USD, excluding flights): {BUDGET_LABELS[trip['budget_band']]}")
    for tour in trip["tours"]:
        lines.append(f"Tour: {tour['title']} ({tour['source_id']})")
    for day in trip["day_plan"]:
        lines.append(f"Day {day['day']}: {', '.join(day['stops'])}")
    if trip.get("message"):
        lines += ["", "Message:", trip["message"]]
    lines += ["", f"The traveller agreed to be contacted about this trip. Lead {payload['lead_id']}.",
              "Adventure Asia keeps no copy of this request once it is delivered."]
    return f"Trip request: {trip['days']} day(s) in {', '.join(trip['countries'])}", "\n".join(lines)


Mailer = Callable[[dict], None]


_ses_client: dict[str, Any] = {}


def ses_mailer(message: dict) -> None:
    """One SES send with short timeouts and no SDK retry: it runs in the request path after the commit (API Gateway
    answers at about 30 s); a failure is retried by the daily run. The client is cached per container."""
    client = _ses_client.get("sesv2")
    if client is None:
        import boto3  # lazy: tests and local runs never reach SES
        from botocore.config import Config

        client = boto3.client("sesv2", region_name=config.BEDROCK_REGION,
                              config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}))
        _ses_client["sesv2"] = client
    client.send_email(**message)


def deliver_email(to: str, payload: dict, tenant_name: str, *, send: Mailer = ses_mailer) -> Optional[str]:
    """None when SES accepted the e-mail, else an error code."""
    if not config.TP_LEAD_EMAIL_FROM:
        return "email_sender_unavailable"
    subject, text = email_text(payload, tenant_name)
    message = {"FromEmailAddress": config.TP_LEAD_EMAIL_FROM, "Destination": {"ToAddresses": [to]},
               "ReplyToAddresses": [payload["traveller"]["email"]],
               "Content": {"Simple": {"Subject": {"Data": subject[:200]}, "Body": {"Text": {"Data": text}}}}}
    try:
        send(message)
    except Exception as e:  # noqa: BLE001
        return f"email_{type(e).__name__.lower()}"[:64]
    return None


async def deliver_now(conn: Any, row_id: int, route: Route, payload: dict, **kw: Any) -> Optional[str]:
    """One delivery attempt of a webhook / e-mail lead after its commit: delivered = redacted at once; a failure
    counts the attempt and leaves the row for the daily run. Returns the error code or None."""
    if route.channel == "webhook":
        error = deliver_webhook(route.webhook_url or "", payload, route.tenant_id, post=kw.get("post", urllib_post))
    elif route.channel == "email":
        error = deliver_email(route.email or "", payload, route.name, send=kw.get("send", ses_mailer))
    else:
        return None
    if error is None:
        await conn.execute(DELIVERED_SQL, row_id, json.dumps(redacted_payload(payload)))
        observability.emit("lead_delivered", channel=route.channel, tenant_id=route.tenant_id)
    else:
        await conn.execute(FAILED_ATTEMPT_SQL, row_id, error)
        observability.emit("lead_delivery_failed", channel=route.channel, tenant_id=route.tenant_id, error=error)
    return error


def hold_days() -> int:
    """The lead hold in days (S279 L5: the setting tripplanner.leads.hold_days, 3..7; ``config.LEAD_HOLD_DAYS`` is its
    fallback), clamped again to LEAD_HOLD_DAYS_MIN..MAX so no value can stretch the DM-7.1 promise."""
    return config.lead_hold_days(str(settings.get(settings.LEAD_HOLD_DAYS)))


def effective_hold() -> timedelta:
    """How old a pending lead may be at an expiry tick: the hold minus one tick interval, the ticks it absorbs
    (:data:`EXPIRY_TICKS_ABSORBED`) and the lateness margin, so a lead written just after a tick is still redacted
    before it turns ``config.LEAD_HOLD_DAYS`` old even when the tick that should end its hold fails (the next one
    does). With the 7-day hold a lead is expired once it is 4 d 23 h old."""
    return (timedelta(days=hold_days()) - (1 + EXPIRY_TICKS_ABSORBED) * EXPIRY_TICK_INTERVAL
            - EXPIRY_TICK_MARGIN)


def hold_cutoff(now: Optional[datetime] = None) -> datetime:
    """Leads written before this are expired by the run at ``now``. Conservative for the daily schedule: a lead written
    at t is picked by the first tick after t + effective_hold; if that tick fails, the next one (one interval later)
    still ends it before t + LEAD_HOLD_DAYS - margin."""
    return (now or datetime.now(timezone.utc)) - effective_hold()
