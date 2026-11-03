# AA-1034 R0 — "Send to an advisor" becomes a demo (lane l6, TripPlanner PR)

Root ADR 0019 d8/d11 (B2B-only ecosystem model, approved S265).

## Decisions

- **Backend flag `ADVISOR_HANDOFF_DEMO`** (`backend/config.py`), a code default **on**; only `false` turns it off, so an
  unset or mistyped Lambda env keeps the demo. No Infra change.
- **Refuse first, 410 `b2b_only`.** In `assembly/handler.py` the send route answers before `_guard` and before any SQL:
  no registration (`tripplanner.customers`), no `sent` event, no `inquiry.submitted` outbox row, no advisor notify. The
  answer is the same for every caller and says nothing about the trip. The earlier trip-id / session-id format checks
  still run first (400 for a malformed id).
- **Frontend flag `NEXT_PUBLIC_ADVISOR_HANDOFF`** (`lib/advisorDemo.ts`): demo unless exactly `live` (build time).
  In demo the button shows the notice at once and never calls the API, so no contact form opens. A 410 `b2b_only` from
  the Lambda (bundle / Lambda flag mismatch) shows the same notice (`useTrip.send` returns `demo: true`).
- **Notice in kit style**: the trip panel's footer box on kit tokens (`k-accent-tint`, `k-accent-border`, kit `Icon`
  `info`), `role="status"`, `data-testid="advisor-demo-notice"`, English copy without issue ids.

## Changed

- `backend/config.py`, `backend/assembly/handler.py`; tests `backend/tests/test_aa1034_b2b_only.py` (new) and an autouse
  fixture in `backend/tests/conftest.py` that keeps the older hand-off tests on the frozen live path.
- `frontend/lib/advisorDemo.ts` (+ test), `frontend/lib/useTrip.tsx`, `frontend/components/TripPanel.tsx`.
- `CONTEXT.md`: the B2B-only demo paragraph.

## Tradeoffs

- 410 Gone rather than 409: the hand-off is withdrawn, not in conflict with state.
- The registration form and the legal links next to it stay in the code (unreachable in demo) so the hand-off can come
  back by flag; they go when leads move to the tenant CRM.

## Should know

- Live check: `POST https://<TripPlanner API>/trip/<trip id>/send-to-advisor` with `X-TripPlanner-Key` and a valid
  `session_id` answers 410 `{"error": "b2b_only"}`, and `tripplanner.outbox` / Booking `booking.inbox_events` get no
  new row.
