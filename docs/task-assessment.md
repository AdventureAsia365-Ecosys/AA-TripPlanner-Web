# Task assessment — AA-TripPlanner-Web (spec v0.3)

Assessment of each task in `.kiro/specs/tripplanner-mvp/tasks.md`:
what Kiro can do autonomously, what needs real DB / secrets, and what is
a human/Terraform gate. Legend:

- 🟢 Kiro can do fully, offline, verifiable now
- 🟡 Kiro writes the code, but running/verifying needs a real resource
  (DB, Mapbox key, AWS) the sandbox may not have
- 🔴 Human-gated — Kiro must ask + report before doing (per your rule)

---

## Task 1 — Repo scaffold 🟢
Create `frontend/`, `backend/{browse,assembly,shared,extraction}/`.
No dependency on anything external. Fully doable and verifiable now.
**Risk:** none. **Adjustment:** none.

## Task 2 — Database schema 🟡 (002 is 🔴 to apply)
- `001_tripplanner_schema.sql` — new `tripplanner.*` schema, entirely
  owned by us. Kiro writes it. Applying it to the real RDS needs DB
  access; writing + local validation (against a local Postgres+pgvector)
  is 🟢.
- `002_shared_destinations.sql` — touches the EXISTING `shared` schema.
  Writing is fine, but **applying must be human-gated** — I must confirm
  migration numbering doesn't collide with concurrent AA-CIS-App work
  before it runs. Will ask before applying.
- **Adjustment I recommend:** the design uses `ivfflat` for the vector
  index. With only ~31 tours (a few hundred components), `ivfflat` gives
  poor recall on a tiny/empty table (it needs data to train lists). I'll
  use **`hnsw`** instead (better recall, no training step, fine at this
  scale). Same query operator (`vector_cosine_ops`), no API change. Will
  flag in the migration.

## Task 3 — Extraction pipeline 🟡 (blocked on real DB + Mapbox key)
`country_normalize.py`, `geocode.py`, `verify.py`, `run.py`.
- Code is fully writable now and unit-testable with fixtures.
- **Actually running it needs:** (a) read access to
  `gold_aa_internal.published_tours` + `acp_contract.tour_atoms`, (b) a
  Mapbox geocoding token, (c) Bedrock access for the categorize step,
  (d) write access to `tripplanner` + `shared`.
- **Blocker:** if this Kiro environment has no DB tunnel / no Mapbox key,
  I can write and unit-test everything but cannot do the real 31-tour
  run. That run would be something you execute, or that I run once you
  provide connectivity/keys.
- **Adjustment:** I'll add a tiny local fixture (a handful of fake atom
  groups) purely so `verify.py`/`run.py` logic is testable offline —
  this is NOT the "mock data phase" the steering rejects, just a test
  fixture. The real run still uses real atoms.

## Task 4 — Bedrock satellite module 🟢 (code + test), 🔴 (live call)
`backend/shared/bedrock_satellite.py`: STS assume-role acc3→acc1,
invoke wrapper, streaming.
- Code + unit test against a stubbed STS/Bedrock (per the task) is 🟢.
- A **live** Bedrock call needs the IAM trust policy to list our Lambda
  roles as trusted principals — that's Terraform in AA-CIS-Infra, which
  is 🔴 human-gated. I'll write the code + stubbed tests now and NOT
  attempt a live call until you've applied the trust change.

## Task 5 — Lambda A (Map/Browse) 🟡
`tiles.py`, `search.py`, `handler.py`.
- Code fully writable + unit-testable now.
- Integration testing needs a DB with real components loaded (depends on
  task 3 having run). I can test query logic against a local Postgres
  with seeded rows.
- **Risk:** the 1° tile grid is coarse for clustered destinations in one
  country; acceptable for MVP per spec. No change.

## Task 6 — Lambda B (Trip Assembly) 🟡
`events.py`, `sequencing.py`, `agent.py`, `registration.py`,
`notify.py`, `handler.py`.
- Most logic (event log + projection, nearest-neighbor sequencing,
  dedupe, debounce) is 🟢 code + unit test.
- `agent.py` compose/renarrate needs Bedrock (see task 4 gate) to run
  live; logic + prompt assembly + SSE framing testable with a stubbed
  satellite.
- `notify.py`: advisor email. **Adjustment:** put the address
  (`pqnghiep1354@gmail.com`) in a single config value / env var, never
  inline — matches design.md. Sending real email needs an SMTP/SES
  path; I'll abstract the sender so the transport is swappable and
  test with a fake sender.

## Task 7 — Frontend 🟡
`MapView`, `FilterChips`, `DestinationPopup`, `TripPanel`, BFF routes.
- Fully writable + builds locally (`next build`).
- Rendering a real map needs the Mapbox public token; the app should
  degrade gracefully (or show a clear "set NEXT_PUBLIC_MAPBOX_TOKEN"
  message) without it. BFF routes need the Lambda URLs as env vars —
  until Lambdas are deployed, they can point at a local backend.
- **Risk:** none blocking; UI is verifiable via build + local run.

## Task 8 — End-to-end smoke test 🔴/🟡
Manual full-loop run. Needs everything above wired against real DB +
Mapbox + Bedrock + a deployed (or locally-run) backend. This is the
final integration and depends on the human-gated pieces being in place.

---

## Human-gated items (I will ask + report before doing each)
Per your instruction, these are allowed but require confirmation first:
1. Applying `002_shared_destinations.sql` to real RDS (numbering
   coordination with AA-CIS-App).
2. Any Terraform / IAM change in AA-CIS-Infra (Lambda roles as trusted
   principals on acc3/acc1 Bedrock roles; CloudFront distribution).
3. Vercel project creation + domain setup.
4. The real 31-tour extraction run (writes to real DB, spends geocoding
   + Bedrock quota).
5. Merge to `main` (open PR, human merges).

## Hard blockers for a fully-working product in this environment
- **DB connectivity** to the shared RDS (acc2, us-west-1). Needed for
  migrations-apply, extraction, and integration tests.
- **Mapbox token** (you're registering — see docs/mapbox-setup.md).
- **Bedrock access** via the satellite roles (Terraform-gated).

Everything not blocked by the above (all code, all unit tests, local
builds) I can complete now. The build sequence I'll follow:
1 → 2 → 4 → 3 → 5 → 6 → 7 → 8, opening a PR after each.
