"""One-off backfill: populate itinerary_components.embedding for rows the
deterministic seeder left NULL.

Why this exists
---------------
`run_deterministic.py` inserted 1081 components with embedding = NULL (it
never calls Bedrock). Semantic search (`browse/search.py`) filters on
`embedding IS NOT NULL`, so search returns nothing until these are filled.
The full LLM pipeline (`run.py`) would set embeddings, but re-running it
would re-extract/re-geocode everything; this script ONLY fills the missing
embeddings in place, leaving every other column untouched.

Consistency with production
---------------------------
Search embeds the *query* with the prod config model (`us.cohere.embed-v4:0`,
us-west-1, 1536-dim). Documents MUST be embedded with the same model or
cosine distance is meaningless — so this script pins that model/region
explicitly (the local backend/.env has stale Titan/us-west-2 values) and
uses input_type="search_document" (queries use "search_query").

Embeddings run as a DIRECT acc2 Bedrock call (no satellite) — acc2 can
invoke Cohere embeddings. Uses whatever AWS creds are in the ambient chain
(here: the acc2 admin session), matching the Lambda role's own grant.

Vector write format
-------------------
No pgvector asyncpg codec is registered in this repo, so the vector is
passed as a pgvector text literal cast with `$2::vector` (same shape
search.py uses for the query vector), rather than a Python list.

Idempotent / resumable
----------------------
Only rows WHERE embedding IS NULL are selected, and each row is committed
individually, so an interrupted run (e.g. tunnel drop) can simply be
re-run and it will pick up where it stopped.

Usage
-----
    TRIPPLANNER_DATABASE_URL=postgresql://user:pass@localhost:15432/aa_cis_dev \
    python -m backend.extraction.backfill_embeddings
"""
from __future__ import annotations

import asyncio
import os

import asyncpg

# Pin the prod embedding config regardless of any stale local .env, so the
# document vectors match what live search will embed queries with.
os.environ["BEDROCK_REGION"] = "us-west-1"
os.environ["BEDROCK_MODEL_EMBED"] = "us.cohere.embed-v4:0"

from backend import config  # noqa: E402  (after env pin)
from backend.shared import bedrock_satellite  # noqa: E402


def _vector_literal(vec: list[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2,...]' — matches search.py."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def _fetch_pending(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, text_extract
        FROM tripplanner.itinerary_components
        WHERE embedding IS NULL
        ORDER BY id
        """
    )


async def main() -> None:
    dsn = config.DATABASE_URL or os.environ.get("TRIPPLANNER_DATABASE_URL")
    if not dsn:
        raise SystemExit("Set TRIPPLANNER_DATABASE_URL to the DB DSN first.")

    print(
        f"[backfill] model={config.BEDROCK_MODEL_EMBED} "
        f"region={config.BEDROCK_REGION} dim={config.EMBED_DIM}"
    )

    conn = await asyncpg.connect(dsn=dsn, command_timeout=30)
    try:
        pending = await _fetch_pending(conn)
        total = len(pending)
        print(f"[backfill] {total} components need an embedding")
        if total == 0:
            return

        done = 0
        failed = 0
        for rec in pending:
            comp_id = rec["id"]
            text = rec["text_extract"] or ""
            if not text.strip():
                # Nothing to embed; skip (leaves NULL, excluded from search).
                print(f"[skip] {comp_id} has empty text_extract")
                continue
            try:
                vec = bedrock_satellite.embed(text, input_type="search_document")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[embed-fail] {comp_id}: {e}")
                continue

            try:
                await conn.execute(
                    """
                    UPDATE tripplanner.itinerary_components
                    SET embedding = $2::vector
                    WHERE id = $1
                    """,
                    comp_id,
                    _vector_literal(vec),
                )
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[db-fail] {comp_id}: {e}")
                continue

            done += 1
            if done % 25 == 0 or done == total:
                print(f"[backfill] {done}/{total} embedded (failed={failed})")

        remaining = await conn.fetchval(
            "SELECT COUNT(*) FROM tripplanner.itinerary_components "
            "WHERE embedding IS NULL"
        )
        print(f"[backfill] done. embedded={done} failed={failed} "
              f"still_null={remaining}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
