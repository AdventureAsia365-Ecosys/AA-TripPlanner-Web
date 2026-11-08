# AA-1046 follow-up: revoke the booking read of the inquiry outbox (tp-027, S273 lane l8)

Found by an independent review in S273: the inquiry.submitted contract is retired (Booking dropped its B2C intake,
send-to-advisor answers 410 and writes no inquiry row), but the tp-009 grants to role `booking` were still live. The
view's `payload` is tagged `pii:contact`, `pii:name`, `pii:free_text`, `pii:travel`, and the view is owned by
`tp_owner`, which bypasses the tp-025 shadow RLS, so `booking` could read every tenant's traveller data.

## Decisions

- **Revoke both grants.** Before the file `booking` held exactly USAGE on schema `tripplanner` and SELECT on
  `tripplanner.v_inquiry_outbox` (schema reference dump of 2026-11-07; AA-CIS-Infra `owners.yaml` consumer grants).
  `lead.submitted.v1` reaches the tenant's CRM over HTTP / webhook / e-mail with no grant (ADR 0008a), and
  `price.from.v1` goes the other way (`tripplanner` reads `booking.v_from_prices`). Nothing else needs the USAGE.
- **`REVOKE ALL` on the view**, not only SELECT: it also clears anything granted by hand.
- **Keep the view.** A drop is destructive (DRY RUN, `-- migrate: destructive`) and belongs with the removal of the
  leftover hand-off code (`backend/assembly/handoff.py`, the contract test copies). The view comment says it has no
  consumer.
- **Contract class, migration-only PR, no code PR.** REVOKE on existing objects and a comment change on an existing
  view are refused by the expand classifier. Nothing in TripPlanner's runtime uses role `booking`, so no code follows.
- **Guards fail loudly**: no privilege of `booking` on the schema, any relation or sequence of it, or in a default
  privilege of the schema; the other readers keep theirs (`tripplanner` on the view, `cis_app` on
  `v_ops_traffic_daily`). Each privilege function sits in a `CASE` on `relkind`: without it the planner called
  `has_sequence_privilege` on a `pg_toast` relation (found by the replay test).

## Changed

- `migrations/027_revoke_booking_inquiry_grant.sql` and its replay test `migrations/tests/test_tp027_pg.py`
  (booking reads before, gets 42501 through its own login after; guards; re-run no-op; mutation of a left-over grant
  refused).
- CONTEXT.md: the owner-bypass bullet and the AA-Booking row of the cross-repo table say the grant is revoked.
  `docs/jobs-runbook.md`: the redaction note no longer names AA-Booking as a reader.

## Tradeoffs

- Revoking now vs. dropping the view in one go: the revoke closes the exposure at once and is reversible; the drop
  waits for the code cleanup and its DRY RUN.

## Should know

- Same wave, other repos: AA-CIS-Infra removes the `owners.yaml` consumer grant and the isolation probe's
  `CONSUMER_GRANTS` row and adds a cross-app cell (booking must get 42501 on the view, expected from tp-027); AA-CIS-App
  drops the two `g` allowlist rows of `rls_catalogue_rules.py` (Infra copies the bytes); the root repo updates the
  contracts README and schema-ownership.md. The Infra registry PR must merge before the Migration Apply of tp-027:
  the nightly probe's `consumer.contract_grants` cell expects the booking grant until that row is gone.
- `backend/shared/tenant.py` and `backend/assembly/handoff.py` docstrings still name `booking` as a reader of the view;
  left for the hand-off code cleanup so this migration-only PR does not redeploy the Lambdas.
- AA-Booking `app/inquiry/leg_keys.outbox_report` reads the view in the one-off AA-830 back-fill; it already returns
  `{"error": "InsufficientPrivilegeError"}` on 42501, so nothing breaks, but it is dead code now.
