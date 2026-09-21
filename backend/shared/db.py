"""Async Postgres connection pool (asyncpg), shared by both Lambdas.

The same RDS instance is shared with AA-CIS-App. This module only ever
connects; the SQL it runs is confined to tripplanner.* (read/write),
shared.destinations (read/write), and gold_aa_internal / acp_contract
(read-only, extraction pipeline only).
"""
from __future__ import annotations

import os
from typing import Optional

import asyncpg

from backend import config

_pool: Optional[asyncpg.Pool] = None


def _resolve_dsn() -> str:
    """Return the Postgres DSN.

    Precedence:
      1. TRIPPLANNER_DATABASE_URL (a full DSN in the env) — used locally.
      2. TRIPPLANNER_DATABASE_URL_ARN — a Secrets Manager secret holding the
         DSN; fetched at runtime (production Lambda path, IAM-role auth, no
         plaintext DSN in the function config).
    """
    if config.DATABASE_URL:
        return config.DATABASE_URL

    secret_arn = os.environ.get("TRIPPLANNER_DATABASE_URL_ARN")
    if secret_arn:
        import boto3  # lazy: tests never hit this branch

        client = boto3.client("secretsmanager", region_name=config.BEDROCK_REGION)
        resp = client.get_secret_value(SecretId=secret_arn)
        dsn = resp.get("SecretString")
        if dsn and dsn != "REPLACE_ME":
            return dsn
        raise RuntimeError(
            f"Secret {secret_arn} has no usable DSN yet (value is empty or the "
            "REPLACE_ME placeholder) — set the real tripplanner DB connection "
            "string in Secrets Manager."
        )

    raise RuntimeError(
        "No DB config: set TRIPPLANNER_DATABASE_URL (DSN) or "
        "TRIPPLANNER_DATABASE_URL_ARN (Secrets Manager ARN)."
    )


async def get_pool() -> asyncpg.Pool:
    """Lazily create and return the process-wide connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=_resolve_dsn(),
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
