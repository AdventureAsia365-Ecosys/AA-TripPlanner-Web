"""AA-1139 — the lead webhook signing key of a tenant, with a version the agency owner rotates in the agency app.

The key of version 0 (no row) is HMAC-SHA256(master, "aa-tripplanner lead webhook v1:" + tenant id), the key every
tenant had before AA-1139 (AA-1038 slice 3); the key of version n >= 1 adds ":" + n to the message. Only the version is
stored (tp-035 ``tripplanner.lead_webhook_key``, scope ``tenant:<uuid>``); the key is derived from the environment's
master secret (``leads.webhook_master_key``) at signing time and shown to the agency once, at rotation
(``planner_admin.rotate_key``). Used as the 64-character lowercase hex text, as ASCII bytes.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Optional

from backend.shared import observability

WEBHOOK_KEY_LABEL = b"aa-tripplanner lead webhook v1:"
KEY_VERSION_SQL = "SELECT version FROM tripplanner.lead_webhook_key WHERE scope = $1"


def webhook_key(master: bytes, tenant_id: str, version: int = 0) -> bytes:
    """The tenant's webhook signing key as bytes: the hex of HMAC-SHA256(master, label + tenant id) for version 0 (no
    rotation yet, the key every tenant had before AA-1139), and of HMAC-SHA256(master, label + tenant id + ":" + n)
    for version n >= 1 (``tripplanner.lead_webhook_key``, tp-035: the agency owner rotates it in the agency app)."""
    if version < 0:
        raise ValueError("a key version is 0 or more")
    message = WEBHOOK_KEY_LABEL + tenant_id.encode() + (f":{version}".encode() if version else b"")
    return hmac.new(master, message, hashlib.sha256).hexdigest().encode()


async def webhook_key_version(conn: Any, tenant_id: str) -> Optional[int]:
    """The version of the tenant's webhook signing key (tp-035; no row = 0). Before tp-035 is applied the table is
    missing (42P01): version 0, the key every tenant has then. Any other read error is None: the caller does not sign
    with a key that may be the wrong one (``webhook_key_unavailable``, the lead waits for the daily retry). A read
    inside a transaction runs in a savepoint, so a failure never aborts the caller's transaction."""
    async def read() -> Optional[int]:
        value = await conn.fetchval(KEY_VERSION_SQL, f"tenant:{tenant_id}")
        return int(value) if value is not None else 0

    try:
        in_tx = getattr(conn, "is_in_transaction", None)
        if callable(in_tx) and in_tx():
            async with conn.transaction():
                return await read()
        return await read()
    except Exception as e:  # noqa: BLE001 — only the type and SQLSTATE are logged
        if getattr(e, "sqlstate", None) == "42P01":
            return 0
        observability.emit("lead_webhook_key_version_unreadable", error=type(e).__name__,
                           sqlstate=getattr(e, "sqlstate", None))
        return None
