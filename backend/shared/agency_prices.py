"""AA-1152 (S285 decision B2 option C; contract tripplanner.agency_prices.v1, tp-036): an agency's own "from" prices on
its planner pages.

The agency's CRM (AA-Partner) computes every number (AA's B2B price x the agency's own markup, else AA's RRP, rounded
up to the agency's step) and PUTs the whole list (``PUT /trip/m2m/v1/tenants/{id}/planner/prices``, scope
``tripplanner.leads/manage``: :func:`store`). TripPlanner keeps it in ``tripplanner.agency_prices`` (one row per tenant,
scope ``tenant:<uuid>``) and only shows it (:func:`read`): never a B2B price, a markup or a supplier, nothing computed
here. With the agency's switch off (``show_prices`` false) no price shows. AA's own planner (config.is_aa_hosted) never
shows an agency price (root ADR 0019 b5 as amended by AA-1152: only the agency's own price, on its own pages).

Fail soft: the table absent or not granted (42P01 / 42501, before tp-036) = no price, one log line.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from backend import config
from backend.shared import observability

ABSENT_SQLSTATES = ("42P01", "42501")
TOURS_MAX = 2000  # technical-constant: the contract's maxItems (tripplanner.agency_prices.v1 AgencyPrices.tours)
ADDONS_MAX = 1000  # technical-constant: the contract's maxItems (AgencyPrices.addons)
_AMOUNT = re.compile(r"^\d{1,10}(\.\d{1,2})?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SV = re.compile(r"^sv:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SOURCE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")

READ_SQL = "SELECT show_prices, currency, tours, addons FROM tripplanner.agency_prices WHERE scope = $1"
UPSERT_SQL = (
    "INSERT INTO tripplanner.agency_prices AS p (scope, show_prices, currency, tours, addons, computed_at) "
    "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::timestamptz) "
    "ON CONFLICT (scope) DO UPDATE SET show_prices = EXCLUDED.show_prices, currency = EXCLUDED.currency, "
    "tours = EXCLUDED.tours, addons = EXCLUDED.addons, computed_at = EXCLUDED.computed_at, received_at = now() "
    "WHERE p.computed_at <= EXCLUDED.computed_at RETURNING computed_at"
)


class Invalid(ValueError):
    """A body outside the contract (422 invalid); the message names the field, never a value."""


@dataclass(frozen=True)
class AgencyPrices:
    """The prices an agency's pages show (only built when its switch is on)."""

    currency: str
    tours: dict[str, str] = field(default_factory=dict)
    addons: dict[str, str] = field(default_factory=dict)

    def tour(self, source_id: str) -> Optional[dict]:
        amount = self.tours.get(source_id)
        return {"from_per_person": amount, "currency": self.currency} if amount else None

    def addon(self, component_id: str) -> Optional[dict]:
        amount = self.addons.get(component_id)
        return {"from_per_person": amount, "currency": self.currency} if amount else None


def scope_of(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"


def _object(value: Any) -> dict:
    data = json.loads(value) if isinstance(value, str) else value
    return {str(k): str(v) for k, v in dict(data or {}).items()}


async def read(db: Any, tenant_id: Optional[str]) -> Optional[AgencyPrices]:
    """The agency's prices, or None (AA's own planner, no list, switch off, table absent). ``db`` is a pool or a
    connection (a connection reads in a savepoint, so a failure never aborts the caller's transaction)."""
    if tenant_id is None or config.is_aa_hosted(tenant_id):
        return None
    try:
        if hasattr(db, "transaction") and not hasattr(db, "acquire"):
            async with db.transaction():
                row = await db.fetchrow(READ_SQL, scope_of(tenant_id))
        else:
            row = await db.fetchrow(READ_SQL, scope_of(tenant_id))
    except Exception as exc:  # noqa: BLE001 — only the type and SQLSTATE are logged
        if getattr(exc, "sqlstate", None) not in ABSENT_SQLSTATES:
            raise
        observability.emit("agency_prices_unreadable", error=type(exc).__name__)
        return None
    if row is None or not row["show_prices"]:
        return None
    return AgencyPrices(str(row["currency"]), _object(row["tours"]), _object(row["addons"]))


def _amount(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _AMOUNT.match(value):
        raise Invalid(f"{name} is not a decimal string")
    try:
        Decimal(value)
    except InvalidOperation:  # pragma: no cover — the pattern already guarantees a decimal
        raise Invalid(f"{name} is not a decimal string") from None
    return value


def _list(body: dict, key: str, id_key: str, pattern: re.Pattern, bound: int) -> dict[str, str]:
    items = body.get(key)
    if not isinstance(items, list) or len(items) > bound:
        raise Invalid(f"{key} is not a list of at most {bound}")
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {id_key, "from_per_person"}:
            raise Invalid(f"{key} holds an item that is not {{{id_key}, from_per_person}}")
        ident = item[id_key]
        if not isinstance(ident, str) or not pattern.match(ident):
            raise Invalid(f"{key}.{id_key} is malformed")
        out[ident] = _amount(item["from_per_person"], f"{key}.from_per_person")
    return out


def parse(body: Any) -> tuple[bool, str, dict[str, str], dict[str, str], datetime]:
    """The contract ``AgencyPrices`` body, checked field by field (an unknown field is refused, like the schema)."""
    if not isinstance(body, dict) or not set(body) <= {"show_prices", "currency", "computed_at", "tours", "addons"}:
        raise Invalid("body is not an AgencyPrices object")
    show, currency = body.get("show_prices"), body.get("currency")
    if not isinstance(show, bool):
        raise Invalid("show_prices is not a boolean")
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise Invalid("currency is malformed")
    try:
        computed_at = datetime.fromisoformat(str(body.get("computed_at")).replace("Z", "+00:00"))
    except ValueError:
        raise Invalid("computed_at is not a date-time") from None
    if computed_at.tzinfo is None:
        raise Invalid("computed_at has no offset")
    tours = _list(body, "tours", "source_id", _SOURCE, TOURS_MAX)
    addons = _list(body, "addons", "component_id", _SV, ADDONS_MAX)
    return show, currency, tours, addons, computed_at


async def store(conn: Any, tenant_id: str, body: Any) -> dict:
    """Replace the agency's list (the newest computed_at wins). Raises :class:`Invalid` (422) or ``LookupError``
    (409 stale: an older list than the one kept)."""
    show, currency, tours, addons, computed_at = parse(body)
    kept = await conn.fetchval(UPSERT_SQL, scope_of(tenant_id), show, currency, json.dumps(tours), json.dumps(addons),
                               computed_at)
    if kept is None:
        raise LookupError("stale")
    observability.emit("agency_prices_stored", tenant_id=tenant_id, show_prices=show, tours=len(tours),
                       addons=len(addons))
    return {"tenant_id": tenant_id, "show_prices": show, "currency": currency, "computed_at": computed_at.isoformat(),
            "tour_count": len(tours), "addon_count": len(addons)}
