"""AA-1038 R5 (root ADR 0019 d10): the tenant context of a database connection.

tp-025 gave tripplanner.sessions, trip_drafts, trip_events and outbox a ``tenant_id`` and shadow row-level security.
tp-028 (the FORCE slice) makes it enforcing, FORCE ROW LEVEL SECURITY on the four tables:

* ``<table>_tenant`` (runtime role ``tripplanner``): only the rows of the bound tenant (GUC ``app.tenant_id``), for
  reads and writes. No tenant context (unset or ``''``) = no row, and an INSERT without one fails (the column default
  is the bound tenant, with no demo fallback any more, and ``tenant_id`` is NOT NULL).
* ``<table>_system`` (``tripplanner`` and the owner ``tp_owner``): every row while the GUC ``app.tenant_scope`` is
  ``'system'``. Only the jobs Lambda opens it (:func:`system_scope`, the retention and lead-delivery kinds that work
  across tenants); ``tests/test_aa1038_force_rls.py`` fails when any other module names it.
* ``<table>_owner_read`` (``tp_owner``, SELECT): the owner-owned views read by CIS (``v_ops_*``) and the migration
  guards keep every row when the session binds no tenant, and only the bound tenant's rows when it does.

The assembly Lambda binds the tenant right after it acquires its pooled connection and unbinds it in a ``finally``
(:func:`bound`). The setting is session level (``set_config(..., false)``), not transaction level, because the
handler mostly runs in autocommit on one pooled connection (``backend/shared/db.py``, max_size=1): a
transaction-local setting would be gone after the first statement. asyncpg's ``RESET ALL`` when the pool takes the
connection back is a second safety, not the main one.

A request without a tenant assertion (the bare path) is the demo tenant (``config.DEFAULT_TENANT_ID``). Since AA-1038
slice 2 a /{slug} request carries the BFF's signed tenant assertion (backend/shared/tenant_assertion.py); the assembly
Lambda binds its tenant only after the signature and the expiry check out.

Log lines carry the tenant id and where it came from, never a request value.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

from backend.shared import observability

GUC = "app.tenant_id"
SOURCE_DEFAULT = "default"  # config.DEFAULT_TENANT_ID (every assembly request in this slice)
SOURCE_SYSTEM = "system"    # no tenant context on purpose: the jobs Lambda, under the system scope (tp-028)
SOURCE_ASSERTION = "assertion"  # AA-1038 slice 2: a /{slug} request, the BFF's verified tenant assertion
SOURCE_M2M = "m2m"              # AA-1038 slice 3: the CRM's lead pull, the tenant of the path under a verified M2M token

BIND_SQL = "SELECT set_config('app.tenant_id', $1, false)"
UNBIND_SQL = "RESET app.tenant_id"

# tp-028 (AA-1038 FORCE slice): the jobs Lambda's cross-tenant path, policy <table>_system. Session level for the same
# reason as BIND_SQL; reset in a finally (asyncpg's RESET ALL on release is the second safety).
SCOPE_GUC = "app.tenant_scope"
SCOPE_SYSTEM = "system"
SYSTEM_SCOPE_SQL = "SELECT set_config('app.tenant_scope', 'system', false)"
UNSCOPE_SQL = "RESET app.tenant_scope"


@dataclass(frozen=True)
class Current:
    """The tenant this request runs as and where it came from (AA-1038 slice 3): read by the lead route, the per-tenant
    AI budget and the ledger rows, so none of them takes a tenant from the request body."""

    tenant_id: str
    source: str

    @property
    def from_tenant_path(self) -> bool:
        """A /{slug} request (verified BFF assertion) or the CRM's pull of one tenant: never the bare demo path."""
        return self.source in (SOURCE_ASSERTION, SOURCE_M2M)


_CURRENT: ContextVar[Optional[Current]] = ContextVar("tp_current_tenant", default=None)


def current() -> Optional[Current]:
    """The tenant of the running request, or None outside one (the jobs Lambda, a test that binds nothing)."""
    return _CURRENT.get()


def current_id() -> Optional[str]:
    cur = _CURRENT.get()
    return cur.tenant_id if cur else None


@contextmanager
def using(tenant_id: Any, *, source: str) -> Iterator[str]:
    """The tenant of a request that binds no database context (the browse Lambda reads no tenant table): it names the
    tenant for the AI budget and the ledger only. Raises InvalidTenant (no value echoed) for a non-uuid."""
    tid = canonical(tenant_id)
    token = _CURRENT.set(Current(tid, source))
    try:
        yield tid
    finally:
        _CURRENT.reset(token)


class InvalidTenant(ValueError):
    """The tenant id is not a uuid. Raised before any SQL; the message never carries the value."""


def canonical(tenant_id: Any) -> str:
    """The canonical text form of a tenant uuid, or InvalidTenant. A non-uuid is never sent to the database."""
    if isinstance(tenant_id, uuid.UUID):
        return str(tenant_id)
    if not isinstance(tenant_id, str):
        raise InvalidTenant("tenant id must be a uuid")
    try:
        return str(uuid.UUID(tenant_id.strip()))
    except ValueError:
        raise InvalidTenant("tenant id must be a uuid") from None


async def bind(conn: Any, tenant_id: Any, *, source: str = SOURCE_DEFAULT) -> str:
    """Set the session-level tenant context of `conn` (the first statement of a request). Returns the bound id."""
    tid = canonical(tenant_id)
    await conn.execute(BIND_SQL, tid)
    observability.emit("tenant_context", tenant_id=tid, source=source)
    return tid


async def unbind(conn: Any) -> None:
    """Clear the tenant context of `conn` (``RESET`` leaves ``''``, which every policy reads as no context)."""
    await conn.execute(UNBIND_SQL)


def _discard(conn: Any) -> None:
    """Close a connection whose context could not be cleared, so it is never handed out again with a tenant bound
    (the pool opens a new one on the next acquire)."""
    terminate = getattr(conn, "terminate", None)
    if callable(terminate):
        # best effort: if even this fails, the pool's RESET ALL on release (or its reconnect) still applies
        with suppress(Exception):
            terminate()


@asynccontextmanager
async def bound(conn: Any, tenant_id: Any, *, source: str = SOURCE_DEFAULT) -> AsyncIterator[str]:
    """``async with bound(conn, tid):`` binds before the body and always unbinds after it, also when the body raises.

    An unbind that fails (the connection broke mid-request) never hides the body's own exception or answer: the line
    ``tenant_unbind_failed`` is logged with the error type only and the connection is terminated, so a connection
    whose context is unknown never goes back into use."""
    tid = await bind(conn, tenant_id, source=source)
    token = _CURRENT.set(Current(tid, source))
    try:
        yield tid
    finally:
        _CURRENT.reset(token)
        try:
            await unbind(conn)
        except Exception as e:  # noqa: BLE001
            observability.emit("tenant_unbind_failed", error=type(e).__name__)
            _discard(conn)


def system_context(op: str | None = None) -> None:
    """Log that this run binds no tenant on purpose (the jobs Lambda). Since tp-028 a connection without a tenant
    context sees no row of the four tables: the kinds that work across tenants open :func:`system_scope`."""
    fields: dict[str, Any] = {"tenant_id": None, "source": SOURCE_SYSTEM}
    if op:
        fields["op"] = op
    observability.emit("tenant_context", **fields)


@asynccontextmanager
async def system_scope(conn: Any, *, op: str) -> AsyncIterator[None]:
    """``async with system_scope(conn, op=KIND):`` the jobs Lambda's cross-tenant path (tp-028 policy
    ``<table>_system``): every tenant's rows of the four tables for the body, then the scope is reset, also when the
    body raises. A reset that fails terminates the connection (log ``tenant_scope_reset_failed``), as :func:`bound`
    does, so a connection that may still be system-scoped is never used again.

    Only ``backend/jobs/`` may open it (``tests/test_aa1038_force_rls.py``): a request path always binds a tenant."""
    await conn.execute(SYSTEM_SCOPE_SQL)
    observability.emit("tenant_context", tenant_id=None, source=SOURCE_SYSTEM, scope=SCOPE_SYSTEM, op=op)
    try:
        yield
    finally:
        try:
            await conn.execute(UNSCOPE_SQL)
        except Exception as e:  # noqa: BLE001
            observability.emit("tenant_scope_reset_failed", error=type(e).__name__)
            _discard(conn)
