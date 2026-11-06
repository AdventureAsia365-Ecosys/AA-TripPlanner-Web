"""AA-1038 R5 slice 1 (root ADR 0019 d10): the tenant context of a database connection.

tp-025 gives tripplanner.sessions, trip_drafts, trip_events and outbox a ``tenant_id`` and one shadow policy each for
the runtime role ``tripplanner``: with no tenant context (GUC ``app.tenant_id`` unset or ``''``) every row is
visible; with a context only that tenant's rows are, for reads and writes, and an INSERT without ``tenant_id`` takes
the context's tenant (column default). Row security is ENABLED, not FORCED, so the owner ``tp_owner`` and the
owner-owned views (``v_inquiry_outbox``, ``v_ops_*``) keep every row.

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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from backend.shared import observability

GUC = "app.tenant_id"
SOURCE_DEFAULT = "default"  # config.DEFAULT_TENANT_ID (every assembly request in this slice)
SOURCE_SYSTEM = "system"    # no tenant context on purpose (the jobs Lambda)
SOURCE_ASSERTION = "assertion"  # AA-1038 slice 2: a /{slug} request, the BFF's verified tenant assertion

BIND_SQL = "SELECT set_config('app.tenant_id', $1, false)"
UNBIND_SQL = "RESET app.tenant_id"


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
    try:
        yield tid
    finally:
        try:
            await unbind(conn)
        except Exception as e:  # noqa: BLE001
            observability.emit("tenant_unbind_failed", error=type(e).__name__)
            _discard(conn)


def system_context(op: str | None = None) -> None:
    """Log that this run uses no tenant context on purpose (the jobs Lambda): it reads and writes through the
    policies' unset-GUC branch, so it sees every tenant's rows."""
    fields: dict[str, Any] = {"tenant_id": None, "source": SOURCE_SYSTEM}
    if op:
        fields["op"] = op
    observability.emit("tenant_context", **fields)
