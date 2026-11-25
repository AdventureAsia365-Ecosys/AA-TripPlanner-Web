# AA-1111 / AA-1166 — TripPlanner hardening slice (S294, lane l22)

## Decisions

- **JSON answers name their charset** (`application/json; charset=utf-8`) in two places only: the BFF routes and the
  proxy through `jsonInit()` (`frontend/lib/jsonResponse.ts`), and every Lambda answer through
  `with_json_charset()` inside `with_request_id()` (`backend/shared/observability.py`, run by `instrument()` for the
  browse, assembly and jobs handlers), so no handler constant had to change.
- **A guard instead of a convention**: `frontend/lib/jsonResponse.test.ts` scans `app/` and `proxy.ts` for a
  `NextResponse.json` without `jsonInit` and for a bare `application/json` response header.
- **One reference page** `docs/input-validation.md` (`SEC-01-tripplanner-input`, draft until the tech lead reads it).

## Changed

- Seven BFF / proxy files wrap their `NextResponse.json` init in `jsonInit()`; the five pass-through answers use
  `JSON_CONTENT_TYPE`. Request headers to the Lambdas (`lib/tripProxy.ts`) are unchanged.

## Tradeoffs

- The Lambda side normalises at the wrapper instead of editing five header constants: one place, and a new handler
  gets it for free. A header that already names parameters is left alone.
- No new setting: nothing here is a threshold.

## Should know

- The kit language cookie is created by `ui/core/i18n.ts` (kit core, CIS canonical); any change to its name or
  attributes is a kit change made in AA-CIS-App and synced, not here.
