"""Registration on send-to-advisor: customer dedupe by phone/email + session claim.

S235 security fix (Phase 0, until AA-Booking owns customer identity + OTP): typing a phone or email
proves nothing. So a submission that matches an EXISTING tripplanner.customers row is never attached
to that row — the session stays a guest, the stored customer is left untouched (no consent update),
and the hand-off carries only what was submitted in this request, flagged unverified
(handoff.submitted_contact). A new customer row cannot be created for it either: phone and email
are UNIQUE. Only a first-time contact (no match at all) creates a customer and claims the session
(clearing its guest expiry) so the trip built as a guest is kept.

Email is matched and stored as lower(trim()); matching uses lower(email) so rows written before
this fix with mixed case still count as a match.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> Any: ...


def normalise_contact(phone: Optional[str], email: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Phone trimmed; email trimmed and lower-cased. Empty → None."""
    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None
    return phone, email


async def _find_existing(conn: _Conn, phone: Optional[str], email: Optional[str]):
    return await conn.fetchrow(
        "SELECT id FROM tripplanner.customers "
        "WHERE (phone IS NOT NULL AND phone = $1) "
        "   OR (email IS NOT NULL AND lower(email) = $2) "
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
    consent: bool = False,
) -> Optional[str]:
    """Create a customer for a first-time contact and attach it to the session; return its id.

    Returns None when the phone or email already belongs to a customer: the session is NOT
    attached to that customer (unverified match) and nothing about it is changed. The caller
    hands off the submitted contact instead (handoff.submitted_contact).

    AA-676: an explicit contact consent is recorded on the new customer, with its time."""
    name = (name or "").strip()
    phone, email = normalise_contact(phone, email)
    if not name or not (phone or email):
        raise ValueError("registration requires name and at least phone or email")

    async with conn.transaction():
        if await _find_existing(conn, phone, email) is not None:
            return None

        row = await conn.fetchrow(
            "INSERT INTO tripplanner.customers (name, phone, email) "
            "VALUES ($1, $2, $3) RETURNING id",
            name,
            phone,
            email,
        )
        customer_id = str(row["id"])

        if consent:
            await conn.execute(
                "UPDATE tripplanner.customers SET contact_consent = true, contact_consent_at = now() "
                "WHERE id = $1 AND NOT contact_consent",
                customer_id,
            )

        # Attach to the EXISTING session; clear guest expiry (indefinite).
        await conn.execute(
            "UPDATE tripplanner.sessions "
            "SET customer_id = $1, expires_at = NULL WHERE id = $2",
            customer_id,
            session_id,
        )

    return customer_id
