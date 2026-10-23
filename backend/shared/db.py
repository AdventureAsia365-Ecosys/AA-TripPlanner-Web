"""Async Postgres connection pool (asyncpg), shared by both Lambdas.

The same RDS instance is shared with AA-CIS-App. This module only ever
connects; the SQL it writes is confined to tripplanner.* user state and
shared.llm_call_log. CIS content is read only through the catalog.* views
(ADR 0001; the place pipeline moved to CIS, AA-712 / ADR 0007, and the frozen
tripplanner place tables were dropped in AA-712 X4).
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
    """Lazily create and return the process-wide connection pool.

    AA-824 (data standards C2.2, connection budget): one connection per Lambda instance. An
    instance serves one invocation at a time and every handler uses its connections one after
    another (no gather, no nested acquire), so a larger pool only held idle connections: with
    max_size=5 the 10 reserved instances could open 50 connections on the shared RDS. With 1,
    the TripPlanner budget is exactly the Lambdas' reserved concurrency.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=_resolve_dsn(),
            min_size=1,
            max_size=1,
            command_timeout=30,
            # Named per Lambda (aa-tripplanner-dev-browse / -assembly) so pg_stat_activity shows which
            # function holds the connections on the shared RDS (AA-824, data standards C2.2).
            server_settings={"application_name": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "tripplanner-local")[:63]},
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
