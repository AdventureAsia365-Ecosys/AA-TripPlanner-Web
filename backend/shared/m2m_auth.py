"""AA-1038 R5 slice 3: service-to-service tokens of the Cognito M2M pool ``aa-m2m-<env>`` on TripPlanner.

TripPlanner serves one HTTP contract to the partner CRM (AA-Partner), which holds no grant on the tripplanner schema
(root ADR 0008a decision 1): ``lead.submitted.v1`` (``/trip/m2m/v1``, ``backend/assembly/leads.py``), scope
``tripplanner.leads/deliver``. The route class accepts exactly one caller: the M2M pool issuer x the AA-Partner (CRM)
app client, an OAuth client-credentials access token carrying that scope. It replaces the edge key for these routes
only (the CRM never holds the BFF's edge key); every other route keeps the edge key check.

Same pattern as AA-CIS-App ``api/core/m2m_auth.py`` and AA-Booking ``app/auth/m2m.py``: the shared multi-issuer
verifier (``identity_verifier.py``, a byte copy, never edited here) with a Cognito access-token profile whose "groups"
claim is ``scope`` (one space-separated string, split here). Pool and client ids are public values (they are in every
token), so the Dev values are code constants with an env override, like ``identity_config.py``: no Lambda env needed.

- ``TP_M2M_ISSUER``          default :data:`DEV_M2M_ISSUER`
- ``TP_M2M_CRM_CLIENT_ID``   default :data:`DEV_M2M_CRM_CLIENT_ID`

Outcomes of :func:`verify_bearer`: no or malformed ``Authorization: Bearer`` / bad signature / expired / wrong issuer
-> :class:`Refused` 401 ``unauthorized``; a validly signed token of another client, a token whose ``sub`` is not its
client id, or one without the scope -> 403 ``forbidden``; no key set ever loaded -> 503 ``unavailable``. The token is
never logged.
"""
from __future__ import annotations

import os
from typing import Optional

from backend.shared.identity_verifier import AuthUnavailable, InvalidToken, IssuerConfig, MultiIssuerVerifier

DEV_M2M_ISSUER = "https://cognito-idp.us-west-1.amazonaws.com/us-west-1_QZ9xcik2F"  # docs-lint: ignore
DEV_M2M_CRM_CLIENT_ID = "1lstqvrbqc4sgrbhf3143cqot7"  # aa-m2m-dev-crm (public id; its secret stays with the CRM)
ENV_ISSUER = "TP_M2M_ISSUER"
ENV_CLIENT_ID = "TP_M2M_CRM_CLIENT_ID"
SCOPE_LEADS_DELIVER = "tripplanner.leads/deliver"
CLIENT_NOT_ALLOWED = "client not allowed"   # the shared verifier's message for a valid token of another client

_verifier: Optional[MultiIssuerVerifier] = None


class Refused(Exception):  # noqa: N818 - an outcome
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def issuer_config() -> IssuerConfig:
    client = _env(ENV_CLIENT_ID, DEV_M2M_CRM_CLIENT_ID)
    if not client.isalnum():
        raise ValueError(f"{ENV_CLIENT_ID} must be a Cognito app client id")
    return IssuerConfig(issuer=_env(ENV_ISSUER, DEV_M2M_ISSUER), client_ids=frozenset({client}),
                        groups_claim="scope", name="m2m")


def get_verifier() -> MultiIssuerVerifier:
    global _verifier
    if _verifier is None:
        _verifier = MultiIssuerVerifier((issuer_config(),))
    return _verifier


def set_verifier(verifier: Optional[MultiIssuerVerifier]) -> None:
    """Install (tests) or drop (None) the container's verifier."""
    global _verifier
    _verifier = verifier


def scopes_of(groups: tuple[str, ...]) -> frozenset[str]:
    return frozenset(s for g in groups for s in str(g).split() if s)


async def verify_bearer(authorization: Optional[str], scope: str = SCOPE_LEADS_DELIVER) -> str:
    """The verified CRM client id, or :class:`Refused`. ``authorization`` is the raw header value."""
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise Refused(401, "unauthorized")
    token = authorization[len("Bearer "):].strip()
    try:
        principal = await get_verifier().verify(token)
    except AuthUnavailable:
        raise Refused(503, "unavailable") from None
    except InvalidToken as e:
        raise Refused(403 if str(e) == CLIENT_NOT_ALLOWED else 401,
                      "forbidden" if str(e) == CLIENT_NOT_ALLOWED else "unauthorized") from None
    except ValueError:
        raise Refused(503, "m2m_not_configured") from None
    if principal.sub != principal.client_id:   # a client-credentials token names its client as sub
        raise Refused(403, "forbidden")
    if scope not in scopes_of(principal.groups):
        raise Refused(403, "forbidden")
    return principal.client_id
