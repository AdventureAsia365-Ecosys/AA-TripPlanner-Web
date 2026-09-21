"""Customer dedupe by phone/email + session claim on registration.

On registration we MUST check tripplanner.customers for an existing
phone or email BEFORE inserting, reusing the existing customer_id if
found. Then we attach that customer_id to the visitor's EXISTING session
(and clear its guest expiry) so the trip built as a guest is not lost.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> Any: ...


async def _find_existing(conn: _Conn, phone: Optional[str], email: Optional[str]):
    return await conn.fetchrow(
        "SELECT id FROM tripplanner.customers "
        "WHERE (phone IS NOT NULL AND phone = $1) "
        "   OR (email IS NOT NULL AND email = $2) "
        "LIMIT 1",
        phone,
        email,
    )


async def register_and_claim(
    conn: _Conn,
    session_id: str,
    name: str,
    phone: str,
    email: str,
) -> str:
    """Return the customer_id (existing or newly created) and attach it to
    the session. Idempotent w.r.t. an already-registered phone/email."""
    phone = (phone or "").strip() or None
    email = (email or "").strip() or None
    if not name or not (phone or email):
        raise ValueError("registration requires name and at least phone or email")

    async with conn.transaction():
        existing = await _find_existing(conn, phone, email)
        if existing is not None:
            customer_id = str(existing["id"])
        else:
            row = await conn.fetchrow(
                "INSERT INTO tripplanner.customers (name, phone, email) "
                "VALUES ($1, $2, $3) RETURNING id",
                name,
                phone,
                email,
            )
            customer_id = str(row["id"])

        # Attach to the EXISTING session; clear guest expiry (indefinite).
        await conn.execute(
            "UPDATE tripplanner.sessions "
            "SET customer_id = $1, expires_at = NULL WHERE id = $2",
            customer_id,
            session_id,
        )

    return customer_id
