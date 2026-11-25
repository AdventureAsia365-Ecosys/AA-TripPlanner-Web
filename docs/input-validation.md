---
id: SEC-01-tripplanner-input
title: "TripPlanner input validation per data item"
description: Where each kind of input the TripPlanner web, its BFF routes and its Lambdas accept is checked and how.
type: reference
status: draft
owner: nghiep
audience: [engineer, agent, cio]
applies_to: [tripplanner-web, tripplanner-lambda]
last_reviewed: 2026-11-26
review_every_days: 90
related: [SEC-01]
---

# TripPlanner input validation per data item

One page that lists, per kind of data, where TripPlanner checks its input. It summarises rules that live in code and
in `CONTEXT.md`; when they differ, the code wins and this page is fixed in the same PR. The ecosystem security
baseline is the root `docs/security/baseline.md` (SEC-01).

Rule of the house: the BFF routes (`frontend/app/api/*`) check what they can before calling a Lambda, and the Lambdas
check every value again; a refused value answers 4xx with a stable error code and is never echoed back. Every JSON
answer says `application/json; charset=utf-8` (BFF: `frontend/lib/jsonResponse.ts`; Lambdas:
`backend/shared/observability.py`).

| Data item | Where it enters | Check | Enforced in |
|---|---|---|---|
| Trip and session ids | `/api/trip`, trip Lambda routes | canonical UUIDs only (400 otherwise, value not echoed) | `backend/assembly/handler.py` |
| Guest token | `__Host-tp_guest` cookie | shape check, then its SHA-256 must match the session that owns the trip; any refusal is the unknown-route 404 | `frontend/lib/guestToken.ts`, `backend/assembly/guest_session.py` |
| Component and stop ids | `/browse/stops?ids=` | UUIDs, at most 50 per call | `backend/browse/handler.py` |
| Tenant slug and host | `/{slug}` planners, agency domains | fixed-shape slug, looked up against the tenant list; a malformed slug never reaches a Lambda | `frontend/lib/routeFinder.ts`, `frontend/lib/tenantHost.ts` |
| Search text | `/api/browse?resource=search` | a query with no letter or digit answers an empty list with no model call; per-address limit | `backend/browse/handler.py` |
| Chat message | planning chat | length cap (400 `message_too_long`), per-session and per-address limits, the tenant's AI budget | `backend/assembly/handler.py`, `backend/assembly/plan_chat.py` |
| Lead: name, e-mail, phone | the lead form | control characters removed, NFC, trimmed, e-mail lower-cased, code-point length caps, a name needs a letter or digit, phone in E.164; the same rules in the form, the Lambda and the CRM (shared test vectors) | `frontend/lib/leadForm.ts`, `backend/assembly/leads.py`, `backend/tests/contracts/lead.submitted.v1.traveller-rules.json` |
| Lead: consent | the lead form | contact consent required (422 `consent_required`) | `backend/assembly/leads.py` |
| Agency webhook target | lead delivery | https only, and the host must resolve to public addresses only | `backend/assembly/leads.py` |
| Catalog purge call | `/api/revalidate/catalog` | signature header, 64 KiB body cap (413 `too_large`) | `frontend/lib/catalogPurge.ts` |
| Request id | `x-request-id` | `[A-Za-z0-9._:-]{1,128}`, else a new id is made | `frontend/lib/requestId.ts`, `backend/shared/observability.py` |
| Cross-site writes | every POST through the BFF | same-origin check (403 `cross_origin`) | `frontend/proxy.ts`, the route handlers |
| Text written by a model | itinerary parts | the model may only pick from closed lists (Pydantic enums), checked again against the source text | `backend/shared/schemas.py` |
