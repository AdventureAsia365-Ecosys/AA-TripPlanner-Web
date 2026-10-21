# AA-847 — destination redirect after an automatic merge (CIS 257)

Own notes file (parallel AA-847 lanes, lesson S242). CIS side: AA-CIS-App `feat/aa-847-dedupe-job`
(`docs/implementation-notes/AA-847-dedupe.md`).

## Decisions
- CIS merges duplicate destinations automatically (job `place_dedupe`, owner decision S246). A merged id loses its
  `catalog.v_destination` row; `GET /browse/destinations/{id}` then reads `catalog.v_destination_redirect` and answers
  **301** with `Location: <keeper id>` (relative: resolves to `…/browse/destinations/<keeper>` whatever prefix the
  CDN / BFF adds) and `{"redirect_to": keeper}`. Only with `PLACES_SOURCE=cis`; a malformed id is never looked up.
- Drafts need nothing: trip events pin `component_id`, and `COMPONENTS_SQL_CIS` re-reads `destination_id`.

## Changed
- `backend/browse/destinations.py` (`destination_redirect`, `REDIRECT_SQL_CIS`), `backend/browse/handler.py` (301
  before the 404), 3 tests in `backend/tests/test_places_source.py`, CONTEXT.md consumer row.

## Tradeoffs
- The 301 carries the CDN cache headers like a 200: after an undo on CIS the old id is live again, and a cached
  redirect lasts at most `CDN_TTL_SECONDS`.

## Should know
- Merge after CIS 257 is applied on Dev. Before it, the lookup fails and is logged (`destination_redirect_unavailable`): a 404 stays a 404.
- Tiles and search caches expire normally; no purge.
