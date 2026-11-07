"""AA-1038 R5 slice 3 (root ADR 0019 d11): a traveller's trip request to ONE agency tenant, as lead.submitted.v1.

Contract: AA-Ecosys docs/contracts/lead.submitted.v1.openapi.yaml (component LeadSubmitted; the pull API for the CRM).

Where a lead comes from. "Send to advisor" on an agency tenant's planner (/{slug}/plan): the request carries the BFF's
verified tenant assertion, so the connection is bound to that tenant (tenant.current() source "assertion"). The bare
path and the AA demo tenant keep the demo notice (410 b2b_only in handler.py): AA keeps no traveller record of its own.

Where it goes. CIS decides per tenant (catalog.v_tenant_tripplanner_v1, CIS 390): ``crm`` when the tenant holds the CRM
add-on (crm.core), else the site's ``webhook`` or ``email`` when set, else ``none``. TripPlanner refuses BEFORE storing
anything when it could not deliver: no route (409 lead_route_missing), the view not readable yet (503
lead_route_unavailable), a webhook / e-mail channel this deployment cannot use (503 lead_channel_unavailable: no
signing key, no sender). The traveller is a guest: no tripplanner.customers row, no registration, no session link.

What is kept, and how long. One tripplanner.outbox row (event lead.submitted, tp-026) in the same transaction as the
trip's ``sent`` event (the AA-676 pattern: a double click answers the same hand-off, a send after an edit is a new
lead). The payload holds the traveller's name, e-mail, optional phone, party, trip summary and consent; it is
redacted at delivery (``redacted_payload``: ids only) and at the latest after ``config.LEAD_HOLD_DAYS`` undelivered
(``tp_lead_deliver`` in the jobs Lambda). Never in a lead: the guest session or token, an access token, an IP, a user
agent or any price.

Delivery:
  * crm: the CRM pulls (``list_pending``) and acknowledges (``acknowledge``) over HTTP with an M2M token
    (backend/shared/m2m_auth.py), one tenant per call, under that tenant's RLS context;
  * webhook: one signed POST right after the commit (``deliver_webhook``, ``config.LEAD_WEBHOOK_TIMEOUT_S``), then the
    daily run retries; ``X-AA-Signature: t=<unix s>,v1=<hex HMAC-SHA256(key, "<t>." + body)>``, key per tenant =
    HMAC-SHA256(master, "aa-tripplanner lead webhook v1:" + tenant_id) (``webhook_key``; AA hands the tenant its key);
  * email: one SES e-mail right after the commit (``deliver_email``), Reply-To the traveller, the same retries.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from backend import config
from backend.shared import observability

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

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,63}$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()./-]{6,40}$")
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
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError
    text = _CONTROL_RE.sub("", value).strip()
    if len(text) > max_len:
        raise ValueError
    return text or None


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
        if not v:
            raise ValueError
        return v

    def email() -> str:
        v = _text(traveller.get("email"), MAX_EMAIL)
        if not v or not _EMAIL_RE.match(v):
            raise ValueError
        return v.lower()

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


def channel_ready(route: Route) -> bool:
    if route.channel == "crm":
        return True
    if route.channel == "webhook":
        return bool(route.webhook_url) and webhook_master_key() is not None
    if route.channel == "email":
        return bool(route.email) and bool(config.TP_LEAD_EMAIL_FROM)
    return False


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
    """LeadAck, or None when the tenant has no such CRM lead (404). Idempotent; the payload is redacted either way."""
    async with conn.transaction():
        row = await conn.fetchrow(LEAD_ROW_SQL, f"tp-lead:{lead_id}", tenant_id)
        if row is None:
            return None
        payload = _payload(row["payload"])
        if row["delivered_at"] is not None:
            return {"lead_id": lead_id, "state": "delivered", "already": True}
        rejected_before = row["redacted_at"] is not None and str(row["delivery_error"] or "").startswith("rejected")
        if outcome == "stored":
            await conn.execute(DELIVERED_SQL, row["id"], json.dumps(redacted_payload(payload)))
            observability.emit("lead_delivered", channel="crm", tenant_id=tenant_id)
            return {"lead_id": lead_id, "state": "delivered", "already": False}
        if rejected_before:
            return {"lead_id": lead_id, "state": "rejected", "already": True}
        await conn.execute(REJECTED_SQL, row["id"], json.dumps(redacted_payload(payload)),
                           f"rejected:{reason}" if reason else "rejected")
        observability.emit("lead_rejected", channel="crm", tenant_id=tenant_id, reason=reason)
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


def urllib_post(url: str, body: bytes, headers: dict, timeout: float) -> int:
    """POST and return the HTTP status (redirects are not followed: a 3xx is a failure)."""
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


def ses_mailer(message: dict) -> None:
    import boto3  # lazy: tests and local runs never reach SES

    boto3.client("sesv2", region_name=config.BEDROCK_REGION).send_email(**message)


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


def hold_cutoff(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=config.LEAD_HOLD_DAYS)
