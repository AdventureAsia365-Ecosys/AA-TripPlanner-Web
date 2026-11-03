# AA-1027 E5 step 4 — tp-025 drops tripplanner.llm_spend_daily (lane l3-e4e5-tp, S265)

Root plan: `docs/audits/2026-11-01-S263-gateway-gap-audit/plan.md` row E5, step 4. Comes after the TripPlanner PR
"gateway reads the CIS views only (fail closed)" (no reader or writer of the table left, deployed on both Lambdas) and
after AA-CIS-App migrations 350 / 351.

## Decisions

- **Destructive file** (`-- migrate: destructive` on line 2, as tp-018): DB Migrate takes the pre-apply RDS snapshot
  (MIG-12) and the DRY RUN comes first (AA-CIS-Infra `db/scripts/aa1027_tp_spend_daily_dry_run.py`, read-only).
- **Guards:** tp_owner only; no dependent view / rule / foreign key; no row written in the last hour (a recent write
  means a pre-E5 build still runs somewhere). The guards read the table only when it exists, so a re-run is a no-op.
- **No quarantine, no 14-day wait, no S3 archive** (DM-3.8 / DM-3.9 exception, as tp-018 in S248 d14): the rows are a
  derived roll-up of `shared.llm_call_log`, recomputable by UTC day and stage; the archive is the DB Migrate snapshot.
  The tech lead approves the exception and the drop after the DRY RUN.
- **No retention row to retire**: the table never had a DM-7.1 row (root `02-data-management.md` §7); the lineage row
  ("Retire tripplanner.llm_spend_daily", §2) and the ERD design page are updated by the root PR.

## Changed

- `migrations/025_drop_llm_spend_daily.sql` (new), `migrations/tests/test_tp025_pg.py` (new: drop, guards, re-run).
- `migrations/tests/test_replay_twice_pg.py`: tp-010 joins `SUPERSEDED_BY_DROP` (a re-run would recreate the table).
- `CONTEXT.md` diagram line.

## Tradeoffs

- The one-hour write guard is a heuristic, not proof: the DRY RUN shows the real last write and the counters, and the
  Lambda deploy evidence of the E5 PR is the proof that no writer is left.

## Should know

- **Number tp-025:** tp-024 is held by another S265 lane. `test_replay_twice_pg.py::test_file_numbers_are_contiguous_from_001`
  fails until tp-024 is on main (locally: 106 passed, only that test red). If tp-024 does not land first, rename to
  `024_drop_llm_spend_daily.sql` and replace `tp-025` / `025_` / `< 25` in the file, the test, the replay reason, this
  note and the root docs.
- Merge only after the TripPlanner E5 PR is deployed: before that, the Lambdas write the counter on every online call,
  and a drop would make each write fail and pause AI for 60 s per container.
