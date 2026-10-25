"""TripPlanner traveller issuer for the multi-issuer verifier (root ADR 0006 §8, Phase 4; AA-908).

TripPlanner accepts exactly one (issuer, client) pair: the Cognito EXTERNAL pool x the public `tripplanner` client.
Every other issuer, every other client of that pool (cis-portal, booking-customer) and every ID token is refused
by the verifier (identity_verifier.py, a byte copy of AA-CIS-App's).

The issuer and the client id are public values (the issuer is in every token, the client id in every Cognito API
call), so they are code constants, like AA-Booking's ``DEV_EXTERNAL_ISSUER`` (Terraform outputs
``identity_external_issuer`` / ``identity_external_client_ids`` of AA-CIS-Infra). The env vars
``TP_TRAVELLER_ISSUER`` / ``TP_TRAVELLER_CLIENT_ID`` override them (another environment, tests); no Lambda env is
needed on Dev.

The JWKS is fetched through the NAT (the TripPlanner Lambda security group allows all egress). The verifier is
built once per container; the handler prefetches its key set on a cold start, and a failed fetch keeps the last
good key set (identity_verifier rule 3).
"""
from __future__ import annotations

import os
from typing import Optional

from backend.shared.identity_verifier import IssuerConfig, JwksFetcher, MultiIssuerVerifier

# Public by design: the `iss` claim of every traveller token (same value as AA-Booking DEV_EXTERNAL_ISSUER).
DEV_TRAVELLER_ISSUER = "https://cognito-idp.us-west-1.amazonaws.com/us-west-1_nKOvHan0W"  # docs-lint: ignore
DEV_TRAVELLER_CLIENT_ID = "2fqmuij0lf47qpf1phjfcgseds"  # aa-external-dev-tripplanner (public client, no secret)

ENV_ISSUER = "TP_TRAVELLER_ISSUER"
ENV_CLIENT_ID = "TP_TRAVELLER_CLIENT_ID"

_verifier: Optional[MultiIssuerVerifier] = None


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def traveller_issuer() -> IssuerConfig:
    """EXTERNAL x {tripplanner}, access tokens only (the Cognito profile defaults of IssuerConfig)."""
    return IssuerConfig(
        issuer=_env(ENV_ISSUER, DEV_TRAVELLER_ISSUER),
        client_ids=frozenset({_env(ENV_CLIENT_ID, DEV_TRAVELLER_CLIENT_ID)}),
        name="external",
    )


def build_verifier(fetcher: Optional[JwksFetcher] = None) -> MultiIssuerVerifier:
    return MultiIssuerVerifier([traveller_issuer()], fetcher)


def get_verifier() -> MultiIssuerVerifier:
    """The container's verifier (one JWKS cache, kept across warm invocations)."""
    global _verifier
    if _verifier is None:
        _verifier = build_verifier()
    return _verifier


def set_verifier(verifier: Optional[MultiIssuerVerifier]) -> None:
    """Replace (tests) or forget (None) the container's verifier."""
    global _verifier
    _verifier = verifier
