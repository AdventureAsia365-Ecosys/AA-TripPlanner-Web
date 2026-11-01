# AA-1026 (slice D5, lane gw-embed) — search stays in one embedding space

## Decisions
- The space of the searched column comes from CIS (`catalog.v_embedding_space_v1`, CIS migration 329), not from this
  repo: CIS owns the vectors (`geo.tour_place.embedding`) and changes their space only by a re-embed + swap.
- Missing or ungranted view (CIS 329 not applied yet) = today's behaviour (one space). Any other read error pauses
  vector search for the request (keyword ranking, reason `space_unreadable`): never compare two spaces.
- The route is filtered to the space's models (`restrict`), so a fallback model of another space can never answer.
  When TripPlanner cannot embed in the column's space (no route model in it, or a dimension other than `EMBED_DIM`),
  search answers with keyword ranking (reason `embedding_space_mismatch`) instead of failing.

## Changed
- `backend/shared/embedding_space.py` (new), `backend/browse/search.py` (`_query_vector`), CONTEXT.md.
- Ledger rows of search embeds carry `quality_signal.embedding_space`.

## Tradeoffs
- `embed_texts` still builds only the Cohere request at 1536 dims. If CIS moves `geo.tour_place` to the Titan V2
  space (1024 dims), search degrades to keyword ranking until a TripPlanner change adds that request shape; the
  runbook (root `docs/ops/runbooks/embedding-re-embed.md`) lists it as a step before swapping the `tour_place` group.
- The suggestions path compares stored vectors with stored vectors only (same column), so it needs no guard.

## Should know
- No migration here; merge after CIS 329 is applied (works before too: the view is optional).
- Local runs: 8 modules need `jwt` and `test_handoff_contract.py` has 5 failures without this change too (env).
