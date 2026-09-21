"""One-off backfill: populate shared.destinations.country.

Why this exists
---------------
The deterministic seeder (run_deterministic.py) geocodes each place with an
empty country ("") — so shared.destinations.country was left blank for
every row. That makes the country-first filter (product requirement: filter
by COUNTRY, then activity) and GET /browse/countries return nothing.

The real country for each destination is on the source tour
(gold_aa_internal.published_tours.country), reachable via
tripplanner.itinerary_components.source_tour_id. A destination can appear in
components from several tours; we take the most common (mode) country for
that destination and normalise it (country_normalize.py, e.g.
SRI-LANDKA -> Sri Lanka).

Idempotent: only fills rows where country IS NULL OR country = '' by
default; safe to re-run. Pass --all to overwrite every row.

Usage
-----
    TRIPPLANNER_DATABASE_URL=postgresql://user:pass@localhost:15432/aa_cis_dev \
    python -m backend.extraction.backfill_country [--all]
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from backend import config
from backend.extraction.country_normalize import normalize_country


# Per-destination majority country from the tours its components came from.
# Country lives on silver_aa_internal.raw_tours.country (the source with
# known dirty values SRI-LANDKA/OKINAWA — hence country_normalize). It joins
# to itinerary_components via the tour uuid (source_tour_id is text).
SELECT_DEST_COUNTRY_SQL = """
    SELECT dest_id, country
    FROM (
        SELECT c.destination_id AS dest_id,
               rt.country       AS country,
               COUNT(*)         AS n,
               ROW_NUMBER() OVER (
                   PARTITION BY c.destination_id
                   ORDER BY COUNT(*) DESC, rt.country ASC
               ) AS rnk
        FROM tripplanner.itinerary_components c
        JOIN silver_aa_internal.raw_tours rt
          ON rt.tour_id::text = c.source_tour_id
        WHERE rt.country IS NOT NULL AND rt.country <> ''
        GROUP BY c.destination_id, rt.country
    ) ranked
    WHERE rnk = 1
"""


async def main() -> None:
    overwrite_all = "--all" in sys.argv
    dsn = config.DATABASE_URL or os.environ.get("TRIPPLANNER_DATABASE_URL")
    if not dsn:
        raise SystemExit("Set TRIPPLANNER_DATABASE_URL to the DB DSN first.")

    conn = await asyncpg.connect(dsn=dsn, command_timeout=30)
    try:
        rows = await conn.fetch(SELECT_DEST_COUNTRY_SQL)
        print(f"[backfill-country] {len(rows)} destinations have a tour country")

        updated = 0
        skipped = 0
        for r in rows:
            dest_id = r["dest_id"]
            country = normalize_country(r["country"])
            if not country:
                skipped += 1
                continue
            where_extra = "" if overwrite_all else \
                " AND (country IS NULL OR country = '')"
            res = await conn.execute(
                f"""
                UPDATE shared.destinations
                SET country = $2
                WHERE id = $1{where_extra}
                """,
                dest_id,
                country,
            )
            # asyncpg returns e.g. "UPDATE 1"
            if res.endswith("1"):
                updated += 1
            else:
                skipped += 1

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM shared.destinations "
            "WHERE country IS NULL OR country = ''"
        )
        distinct = await conn.fetchval(
            "SELECT COUNT(DISTINCT country) FROM shared.destinations "
            "WHERE country IS NOT NULL AND country <> ''"
        )
        print(f"[backfill-country] updated={updated} skipped={skipped} "
              f"still_blank={total} distinct_countries={distinct}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
