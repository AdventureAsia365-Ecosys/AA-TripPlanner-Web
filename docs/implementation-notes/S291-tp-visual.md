# S291 — Visual job red on main (planner.spec.ts), AA-1119 follow-up

Session S291 (24/11/2026). The `Visual / Visual (public pages, 1440/390, light/dark)` job of `deploy-web-dev.yml` was red
on main from the deploy of 12b6a4d (#298, AA-1152 day add-ons), then on 1b8502c (#299) and bc7daf0 (#301). Every planner
test that opens `/plan` timed out on `getByTestId("day-list")` (20.8 s, or 1.5 min at 390 on the "Your trip" button).

## Root cause

- #298 added `GET /api/trip/addons?trip_id=` to the planner (`DayAddonsProvider` inside `DayList`).
- `e2e/visual/planner.spec.ts` mocks `**/api/trip**`. That glob also matches `/api/trip/addons`, and a GET without
  `resource` was answered with the trip body (`{trip_id, itinerary, ...}`), which has no `days`.
- `fetchTripAddons` cast any 200 body to `TripAddons`; `addonsOfDay` then ran `data.days.find(...)` on `undefined`,
  the TypeError escaped the render, and the whole planner fell to the "This page couldn't load" error page.
- Confirmed against the deployed host with a `pageerror` listener: `Cannot read properties of undefined (reading 'find')`.
- #299 (403 for another agency's trip id) is not involved: every planner test mocks `/api/trip`, so no request reaches
  the tenant isolation check. The red started one deploy earlier.

## Decisions

- Fix both sides. The mock was wrong (it answered a new endpoint with the trip), but the planner was also fragile: a
  200 with an unexpected body (a proxy error page turned JSON, a backend change, an old Lambda) took the whole planner
  down, against the component's own rule "a failed read shows nothing".
- App: `tripAddonsOf(body)` in `frontend/lib/dayAddons.ts` parses the answer field by field (null unless `days` is an
  array; malformed days and items dropped; missing lists empty; a price only when `agencyPriceOf` accepts it).
  `fetchTripAddons` and `setTripAddon` use it; `addonsOfDay` also checks `Array.isArray(data.days)`.
- Spec: `/api/trip/addons` gets its own answer (default: no add-on); any other `/api/trip/<path>` not mocked gets a 404
  (the planner's fail-soft path), never the trip body, so the next new endpoint cannot repeat this.
- Spec diagnostics: `openPlanner` records `pageerror`; a planner that never shows its day list now fails with the page
  error text instead of a bare timeout (and the 390 "Your trip" click has a 20 s ceiling instead of the 90 s test
  timeout).

## Changed

- `frontend/lib/dayAddons.ts`: `tripAddonsOf`, guarded `addonsOfDay`.
- `frontend/lib/api.ts`: both add-on calls go through `tripAddonsOf`.
- `frontend/lib/dayAddons.test.ts`: other shapes are "no add-on"; a well-formed answer keeps its add-ons.
- `e2e/visual/planner.spec.ts`: explicit add-on mock, 404 for other sub-paths, page-error diagnostics, and two tests:
  a day card lists its add-ons and a pick is saved (POST body with trip, session, day and the `sv:` id; AA's planner
  shows no price), and an answer of another shape shows no add-on while the planner keeps working.

## Tradeoffs

- The parser drops what it does not understand instead of failing the read; a backend shape change shows fewer add-ons
  rather than an error. That matches the provider's documented "a failed read shows nothing".
- The new e2e regression test can only pass after the fix is deployed (the Visual job runs after the deploy of the
  same commit). Verified locally against `next start` of this branch (planner spec 32 passed; only the two map sync tests
  failed, because a local build has no Mapbox token) and against the deployed host before the fix (the regression test
  fails there with the TypeError, every other test passes with the corrected mock).

## Should know

- The planner interaction spec runs only after a deploy of main, never on a PR, so a frontend PR that adds a planner
  `/api/*` call gets no PR-time signal. Follow-up: run `planner.spec.ts` (without the map sync tests) against the
  Preview job's `next start` on PRs that touch `frontend/**` or `e2e/visual/**`.
