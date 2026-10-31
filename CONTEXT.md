---
id: ARCH-06-tripplanner
title: "AA-TripPlanner-Web — Repo Context"
description: Stack, boundaries, ownership, domain terms and working rules of AA-TripPlanner-Web (Next.js frontend and its two backend Lambdas); read it before designing or changing code in this repo.
type: explanation
status: active
owner: nghiep
audience: [engineer, agent]
applies_to: [aa-tripplanner, tripplanner-lambda]
last_reviewed: 2026-10-28
review_every_days: 90
related: [ARCH-01, ARCH-05, ARCH-08, docs/adr/README.md]
---

# AA-TripPlanner-Web — Repo Context

B2C map-based trip planner in the Adventure Asia (AA) ecosystem. An end traveler explores
destinations on an interactive map, assembles a personal multi-day trip, and gets AI-assisted
suggestions and narrative. This repo is a self-contained product: its own frontend, its own
serverless backend, and its own database schema — it does **not** share application code with
AA-CIS-App. What it shares with the rest of the ecosystem is a small set of AWS-level resources
(Bedrock access, OIDC deploy roles, the destinations reference data) provisioned by AA-CIS-Infra.

> Scope of this file: describe the whole repo accurately — what it is, how it is laid out, how it
> runs, and where its boundary with the rest of the AA ecosystem sits. Deep per-subsystem design
> notes belong in their own doc next to the code, not here.

## What it is (role in the AA ecosystem)

- **Product surface**: consumer-facing (B2C). The visitor is a traveler planning a trip, not an
  AA admin or a marketplace tenant. This is the opposite audience from AA-CIS-App (admin +
  tenant content pipeline).
- **Position**: a leaf product. It reads CIS content only through the `catalog.*` views (tours, places,
  tour graph) that the CIS side owns, and it calls Bedrock for AI assembly. Nothing in the CIS content pipeline
  depends on TripPlanner — the arrow points one way (TripPlanner → shared/Bedrock), never back.
- **Ownership split**: this repo owns its application code and its own schema (`tripplanner.*`).
  AWS resources it needs (Lambda plumbing, OIDC deploy role, cross-account Bedrock trust) are
  owned by **AA-CIS-Infra**, consistent with the ecosystem rule "App owns code, Infra owns
  resources."

## Architecture at a glance

Architecture diagram (AWS icons): `AA-Ecosys/docs/architecture/diagrams/tripplanner.svg`, drawn from `tripplanner.py` in the
same folder; the catalog page `AA-Ecosys/docs/architecture/diagrams/README.md` holds every view and is shown in the CIS
admin at `/admin/docs?path=docs/architecture/diagrams/README.md`.

```text
Traveler (browser)
      │
      ▼
Next.js 16 frontend  ──(Mapbox GL)──►  interactive map UI
      │  same-origin /api/* (BFF, server-side; holds the TripPlanner API key)
      ▼
API Gateway HTTP v2  (ANY /browse/{proxy+}, ANY /trip/{proxy+}; edge shared secret
      │               X-TripPlanner-Key; guest token on trip mutations, AA-827, and reads, AA-909)
      ▼
Two Python 3.12 Lambda functions
  ├─ browse      → stateless, cacheable reads (destinations, suggestions); embeds search text
  └─ assembly    → stateful writes (trip mutations) + Bedrock (AI narrative/plan)
(+ jobs Lambda, no HTTP route: daily EventBridge tick → tripplanner.job worker, AA-861)
      │
      ▼
Postgres (the shared RDS in acc2)
  ├─ tripplanner.*        (owned here — customers, sessions, trip_events, trip_drafts, outbox,
  │                        narration_cache, llm_spend_daily, component_id_map, job, ai_rate_counter)
  ├─ shared.llm_*         (CIS Model Gateway: read config, write llm_call_log)
  └─ read-only: catalog.* views (CIS-owned: tours, places, tour graph, embeddings)
      │
      ▼
Amazon Bedrock  — Cohere Embed v4: acc2 first, then acc3 via the satellite role (AA-862);
                  Claude via the cross-account role on acc3 (the only LLM satellite, AA-998)
```

The journey ends with a **draft trip + customer** sent to an advisor ("Send to advisor" +
sign-up), not a completed booking — booking belongs to the future AA-Booking (AAA) product
(see `docs/tripplanner-to-aaa-handoff.md` in the root docs repo).
TripPlanner sends no e-mail: the staff "new inquiry" e-mail is sent by AA-Booking when it processes the
`inquiry.submitted` outbox row (job `bk_notify_email`, AA-811); `backend/assembly/notify.py` only logs the hand-off.

### Frontend

- **Next.js 16.4** (App Router, Turbopack build) on **React 19.2**, deployed on **Vercel** (AA-904 15.5,
  AA-906 16 — same major as the CIS admin/portal and Booking). Route `params` / `searchParams` are
  Promises (await them); route segment config (`export const revalidate`) must be a number literal
  (`lib/segmentConfig.test.ts` keeps the catalog pages equal to `CATALOG_REVALIDATE_S`). The request
  hook is `frontend/proxy.ts` (Next 16 name for `middleware.ts`; runs on the Node.js runtime).
  Lint is the ESLint 9 CLI with a native flat config (`frontend/eslint.config.mjs`,
  `eslint-config-next/core-web-vitals`), which includes the **React Compiler rules** of
  eslint-plugin-react-hooks 7 as errors: no ref read/write during render (`react-hooks/refs`), no
  synchronous setState in an effect body (`set-state-in-effect`), no self-reference before declaration
  (`immutability`). Fix them with lazy state init, derived state (tag async results with the key they
  were fetched for), the "adjust state when a prop changes" render pattern, or a layout-effect ref
  sync — not with rule disables (see `docs/implementation-notes/AA-906.md`). The React Compiler itself
  is not enabled (no `babel-plugin-react-compiler`). `next-env.d.ts` is generated, not committed.
  `package.json` `overrides` pins `postcss-selector-parser` 7.1.6 under Tailwind 3 (advisory fixed
  only in 7.x; Tailwind 3 asks `^6`); drop it when Tailwind moves to v4. Next 16 no longer pins a
  vulnerable postcss, so the AA-904 postcss override is gone (`npm audit --omit=dev`: 0).
- **Mapbox GL** for the interactive map (destination pins, trip routing, day-by-day view).
- Talks to the backend Lambdas over HTTP; no direct DB access from the browser.
- Deploy is CI-driven (S241): `.github/workflows/vercel-deploy.yml` runs `vercel deploy` when a PR
  or a push to `main` touches `frontend/**` — PR → preview (URL in one PR comment), `main` →
  production. The Vercel Git integration is off (`frontend/vercel.json`, `git.deploymentEnabled:
  false`) because the Hobby team cap (~100 deployments / 24 h, all projects) was being used up.
  Needs the repo secret `VERCEL_TOKEN`. Check the "Vercel deploy" job on the PR for deploy success.
- **Security headers (AA-876)** on every response (`frontend/next.config.mjs`, same list as the CIS admin and
  portal): HSTS, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, a minimal
  CSP (`frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'` — no `script-src` yet),
  `Permissions-Policy` (camera, microphone, geolocation off), and no `x-powered-by`. `frame-ancestors 'none'`
  blocks any partner iframe of TripPlanner until an allow-list is designed (KAN-20 B2B). A production deploy
  fails red if `https://aa-tripplanner.vercel.app/` lacks any of them ("Check the production security headers"
  step in `vercel-deploy.yml`; TripPlanner has no UI smoke workflow, so this is its header smoke).
- **Visual run (AA-994 S5b).** After every successful deploy, `vercel-deploy.yml` job `visual` calls
  `.github/workflows/visual.yml` (also dispatchable with any `base_url`): Playwright (`e2e/visual/`, its own pinned
  package) opens every page of `e2e/visual/pages.ts` at 1440 x 900 and 390 x 844 in light and dark, checks no page-level
  horizontal overflow, no console / page error, no failed same-origin request, and uploads full-page PNGs + `report.json`
  (14 days), with one table in the job summary and one PR comment. Previews use the deployment URL (skipped with a
  notice while the project has no automation bypass), production the public host. Report-only: the deploy verdict
  (`result`) does not wait on it. `frontend/lib/visualPages.test.ts` fails when an `app/**/page.tsx` route is neither
  listed nor excluded with a reason.

### Theme and contrast (AA-812, AA-837, AA-995, AA-994)

- **Kit core, tokens profile (AA-994 S5a, root ADR 0016).** `frontend/ui/core/` is a byte copy of AA-CIS-App
  `frontend/app/_kit/core/` (tokens.css, tokens.ts, brand.ts, theme.ts, check-kit-copy.mjs, eslint-kit.mjs,
  MANIFEST.json), written only by the root `tools/kit/kit-sync.sh` (the CIS kit-sync bot opens the PR) and pinned in
  `frontend/kit.lock.json`; never edit it here. The Frontend job checks integrity (`check-kit-copy.mjs`, plus
  `--css .`: no `--aa-*` definition outside `ui/core/tokens.css`) and provenance
  (`.github/scripts/check_kit_provenance.py`: the manifest is CIS's at the lock's ref, and the ref is on CIS main),
  and lints `app/`, `components/`, `ui/` with the kit's token rules (`no-hex-colour`, `no-raw-aa-var`,
  `no-aa-var-definition`) at error; older violations (Mapbox paint colours, the theme-color meta) sit in
  `frontend/eslint-suppressions.json`, which may only shrink.
- One palette: the `--aa-*` tokens of the kit core (`frontend/ui/core/tokens.css`, imported first in
  `app/layout.tsx`; `frontend/ui/tokens.css` adds only the `--tp-*` values), exposed to Tailwind as `k-*` (`bg-k-card`, `text-k-muted`,
  `text-k-accent-deep`, `bg-k-accent text-k-on-accent`, inverted `bg-k-ink text-k-bg`…). Every page
  uses them, **the map planner included**: AA-837 retired the planner's light-only `aa-*` hex palette.
  Tokens are CSS variables, so Tailwind opacity modifiers (`/40`) do not apply — pick a token.
- Light / dark / system theme on every page (the kit's `createThemeStore({ storageKey: "tp_theme" })` in
  `ui/restoreTheme.ts`, used by `ui/theme.ts`; restore script in `app/layout.tsx`,
  `ThemeToggle` in both the site header and the planner header). The Mapbox basemap stays the light
  `outdoors-v12` style in both themes (map imagery, drawn on a canvas); the planner chrome around it
  follows the theme.
- Guards (`npm test`): the `--aa-*` text/background pairs are checked in CIS (`lib/tokensContrast.test.mjs`, on
  the core file, before any sync); `lib/contrast.test.ts` fails on raw palette classes (`aa-*`, `bg-white`, `text-gray-500`,
  `[#hex]`…) in the planner's files. Rendered-page check: axe `color-contrast` on `/plan` in light
  and dark at 1440 and 390 px (AA-837 implementation notes).
- **Customer pages, "Golden Hour" (AA-995).** The type, radius, space, shadow and motion scales (`--aa-*`) are
  in the kit core since AA-994 S1b; `ui/tokens.css` holds the B2C surfaces (`--tp-*`: sand band, navy band
  and its text, photo scrims, script face, ribbon speed), mapped to new Tailwind names only (`text-display|h1|h3|
  lead|body|small|caption|kicker|script`, `rounded-card|tile|panel|pill`, `shadow-e1..e3`, `duration-fast…`,
  `ease-std|out|in`, `k-sand`, `k-band`, `k-on-band`…), so the planner's Tailwind defaults are untouched. Fonts:
  Poppins + Satisfy (section titles only, `display: optional`) in the root layout; the map planner
  `/plan` included (spec R10); Inter and Fahkwang removed. Text on photos sits only on `--tp-scrim-text` (alpha .70) or darker.
- **Motion gate (AA-995).** The restore script also sets `<html data-motion="ok|reduce">` (prefers-reduced-motion,
  live, or `localStorage.tp_motion = "reduce"`). Every keyframe animation and every hidden "before reveal" state is
  scoped under `html[data-motion="ok"]` (`app/globals.css`); no JS = static and fully visible. Only one element moves
  by itself on a page: the photo ribbon (`components/site/PhotoRibbon.tsx`), with a pause toggle (stored in
  `tp_motion_paused`), paused off screen and on hover, and a still scroll row on keyboard focus.
  The footer "Reduce motion" toggle (`components/site/MotionToggle.tsx` + `ui/motion.ts`) writes `tp_motion` and sets
  `data-motion` at runtime; `html[data-motion="reduce"]` also carries the global animation / transition safety net, and
  JS motion (the planner map camera in `components/MapView.tsx`, `TourCarousel` smooth scroll) reads the gate at call
  time.
- **`.tp-cv` (content-visibility, AA-995).** Below-fold sections only, via the class + `cvSize(px)`
  (`lib/contentVisibility.ts`); never on an anchor-first section, a section with an AI-label popover, or one whose
  children draw shadows / overflow to its edge (paint containment clips them). `[data-reveal]` inside a `.tp-cv`
  section works because `RevealObserver` measures the section, not the skipped child.
- More guards (`npm test`): `lib/contrast.test.ts` also checks the sand / navy band pairs and every photo scrim
  (composited over a white pixel); `lib/tokens.test.ts` bans raw px font sizes, hex, raw durations / easings,
  `rgba()` and `rounded-2xl` / `shadow-aa` in the rebuilt customer files; `lib/restoreTheme.test.ts` runs the
  inline script; `lib/photos.test.ts` covers the duplicate-cover rule, grid order and the ribbon fill rule.
- Day counts go through `formatDays` / `formatDayRange` (`lib/text.ts`): "1 day", "3 to 8 days".
- **Prefetch rule (AA-833; breadcrumbs and links to `/` added by AA-995, S255).** No `prefetch` on card / tile /
  photo links, footer links, breadcrumbs, or any link to `/`: a prefetch of `/` runs the home hero preload (60 KB
  AVIF, high priority) on the current page, inside its LCP window. The favicon is `app/icon.svg`
  (without it every view logged a `/favicon.ico` 404).
- **LCP images render through a Client Component (S256).** React Flight turns every eager `<img>` / `<link
  rel="preload">` a Server Component renders into an image preload hint in the RSC payload, and a prefetch of that
  route applies the hints on the page the visitor is on. The LCP images (home hero picture `HomeHeroPicture`,
  destination hero, tour gallery photo, first `/destinations` tile) therefore render through
  `components/site/LcpImage.tsx` / `HomeHeroPicture.tsx` ("use client", same HTML, no hint).

### Pages (AA-812)

- `/` search-first home (destination, days, travellers → `/plan?country=&days=&travellers=`). AA-995 order:
  photo hero (static curated asset in `public/hero/`, from a CIS catalog photo) under a transparent navy header →
  search card → ways in → featured tours row (9) → proof line → destinations grid → how it works → navy CTA band
  → photo ribbon → navy footer (trust strip inside). Still two catalog reads.
- `/plan` the map planner (moved from `/`), with a Map | List-of-tours toggle; `?tour=<tour_id>`
  pins that tour's whole route through the existing apply-route flow (`&ask=1` opens the trip).
- `/tours/[slug]` and `/destinations/[country]`: Server Components with ISR (30 min, the browse
  CDN TTL), JSON-LD (`TouristTrip`, `TouristDestination`), `sitemap.xml`, `robots.txt`.
- AA-995 (S254) puts the rest of the customer pages on "Golden Hour": `/destinations/[country]` (photo hero under the
  transparent header → tours carousel / grid → places map + list → navy CTA band → "Around {country}" ribbon),
  `/tours/[slug]` (gallery strip / mosaic → title, key facts, chips → highlights → day-by-day timeline → where you go
  → included, sticky "Make it yours" card / bottom bar), a new `/destinations` index (every country, ISR, one
  `getCountries()` read; in the sitemap and the purge paths), and `/trips`, `/sign-in`, the legal pages, not-found
  and the error page on one navy title band (`components/site/PageIntro.tsx`). The planner keeps its layout and
  Inter; only its header turned navy. **LCP photo of a destination / tour page** is resolved on the server
  (`lib/photoResolve.ts`): the catalog photo URL's 302 `Location` (the image CDN) is read once per ISR render (1.5 s
  timeout, any failure keeps the catalog URL) and used for that one image; a presigned
  (expiring) Location or a redirect cached for less than a day is refused, since the ISR page can be served stale
  long after a presign expires. Since S257 the photos a page fetches while it loads are resolved the same way
  (`resolvePhotoMap`: up to 24 distinct URLs in parallel under ONE shared 1.5 s deadline): every tile of
  `/destinations` and the tour cards of a destination page. Every other photo (places list, photo ribbons, home)
  keeps the catalog URL. Same browse reads per page as before. Since S256 a resolved photo-CDN file is served
  **same-origin** as `/photo-cdn/<path>?v=…`, an external rewrite in `next.config.mjs` to the CIS photo CDN
  (`img.lumiguides.it.com`, AA-844) with `x-vercel-enable-rewrite-caching: 1`, so the Vercel CDN caches the versioned
  file and the browser reuses the HTML connection (no preconnect needed). The hero also asks the catalog for
  `?size=medium` (960 w) and adds it to the srcset only when CIS redirects it to a file (CIS answers 422 today).
  The first-row tiles of `/destinations` load eagerly (the first is the LCP); the others stay lazy.
  Since S258 the static map (destination and tour pages) and the destination page's place thumbnails render through
  `components/site/NearViewImage.tsx` (no `src` until 300 px below the viewport; `<noscript>` copy), because Chrome's
  `loading="lazy"` fetched them during load on a phone; and a tour card that shows the hero's own file reuses the
  hero's srcset + `sizes` (`sameFileAsHero`), so the browser takes the file it already has.
- **On-demand purge on a CIS tour lifecycle event (AA-987).** CIS posts one HMAC-signed notice per event of
  `catalog.v_tour_lifecycle_outbox` (CIS job `tripplanner_purge`, retried with backoff, swept every 5 min) to
  `POST /api/revalidate/catalog` (`app/api/revalidate/catalog/route.ts`, logic in `lib/catalogPurge.ts`). Header
  `X-AA-Signature: t=<unix s>,v1=<hex hmac-sha256 of "t.body">`, secret = Vercel env `CATALOG_REVALIDATE_SECRET`
  (same value as CIS secret `aa-cis/<env>/tripplanner-revalidate-hmac`), 5-minute timestamp window, body contract
  `catalog.tour.lifecycle.v1` (`events[]`: `event_id`, `source_id`, `event`, …). It drops every cache that can show the
  old state: `revalidateTag("tp-catalog", { expire: 0 })` (every catalog fetch of `lib/catalog.ts` carries that tag),
  `revalidatePath` of `/`, `/tours/[slug]`, `/destinations/[country]`, and the Vercel CDN entries of `/api/browse`
  (`dangerouslyDeleteByTag("tp-catalog")`, `@vercel/functions`). A cacheable `/api/browse` answer is now kept by the
  CDN for the Lambda TTL (`vercel-cdn-cache-control`, tag `vercel-cache-tag: tp-catalog`) and by the browser for 60 s
  at most. Idempotent (a replay purges again); 401 bad / stale signature, 400 bad body, 503 secret unset, 502 purge
  failed (CIS retries). One `{"evt":"catalog_purge",…}` log line per call (event ids and kinds, no source ids).
- **Tours AA no longer serves (AA-986 L6, root ADR 0015 decision 5 / Q5).** Lists, countries, the sitemap and every
  planner read already hide them (they read `catalog.v_tour` / `v_tour_component`, live tours only). A tour page of a
  **retired** key answers **410 Gone** (a plain noindex page): Next has no "gone" for a page, so `frontend/proxy.ts`
  (matcher `/tours/:slug`, no cookie ever set there) checks the slug's source-id suffix against an in-memory list of
  retired suffixes (`lib/goneTours.ts`, `GET /browse/catalog/retired`, refreshed every minute per instance in the
  background; AA-987: a request waits for the browse call, at most 1.5 s, only when the list is over 2 minutes old or
  was never loaded, so a retired tour answers 410 within 2 minutes on every instance; a failed refresh keeps the last
  list and makes nobody wait until its retry is due). The 410 is never kept by the CDN (`vercel-cdn-cache-control:
  no-store`, browser 60 s), so a restored tour comes back as fast. The browse Lambda reads the CIS tombstone `catalog.v_tour_retired` (CIS 274) and
  answers `/browse/catalog/tours/{slug}` 410 for a retired key too (the page shows 404 until the proxy list catches
  up). A **paused** key stays **404** (temporary). A **superseded** version needs nothing: the slug suffix is the
  stable `source_id`, so the old slug finds the current version and the page permanent-redirects (308) to its slug.
  A tombstone that cannot be read (before CIS 274, grant missing) degrades to the pre-L6 404.
- `/sign-in` and `/trips` (AA-908): traveller sign-in by email code and My Trips (see "Traveller sign-in in the BFF").
- `/privacy` and `/terms` (AA-886): **draft** legal pages (static, `noindex`, not in the sitemap) built on
  `components/site/LegalPage.tsx`, which always shows a "Draft — pending legal review" banner. Paths and
  the draft label live in `lib/legal.ts`; the site footer and the contact form in `TripPanel` (the only
  form that collects personal data) link both pages. Every factual sentence must match what the code
  does; open legal points stay `[to be confirmed]` until counsel approves the text.
- Kit pages use `frontend/ui/` (tokens copied from the CIS kit with the same values, light +
  dark via `<html data-theme>`, localStorage `tp_theme`); the planner keeps its light `aa-*` palette.

### Backend — two Lambdas, split by statefulness on purpose

- **browse Lambda** — read-only, **stateless and cacheable**. Serves destination browsing and
  suggestion queries. Because it never mutates trip state, its responses can be cached at the
  edge/CDN layer without correctness risk. Keep new read-only endpoints here. Since AA-812 it
  also serves the public catalog under `/browse/catalog/*` (tours, tour by slug, countries,
  country page, sitemap), reading **only** the CIS catalog views (`catalog.v_*`, ADR 0001), and
  `/browse/tour-route` (one tour's whole route for "Customise this trip": the Tour Graph under the
  current or the linked tour_id, else built from the tour's catalog places onto planner experiences
  at those places, `match` = `tour_graph` | `places`; a Tour Graph route spans every tour day 1..N and either
  route must pin at least half of its days (`tour_days` / `pinned_days` in the proposal, AA-990 F2), else the graph
  gives way to a places route that pins more, else a 404 that carries the tour's title and day list —
  the `/api/browse` BFF hands that 404 to the browser as a 200 `{ "match": "none", "tour": … }`,
  `frontend/lib/tourRoute.ts`, AA-837, so an expected "no planner route" logs no console error).
  Map tiles (`/browse/tiles/{lat}_{lng}[_{size}]`) are fixed-grid cells of 1–90 degrees; the map
  (`frontend/lib/tileLoader.ts`) picks the finest grid that covers the view in ≤ 16 cells, caches
  them, runs ≤ 4 requests at a time and has a request budget (AA-812 hotfix). Its one model
  call is the embedding for free-text search (`/browse/search`; the response is no-store, the query
  embedding is cached in the container and a failed embed falls back to keyword ranking, AA-856).
- **assembly Lambda** — **stateful**, owns all trip mutations, and is the only side that calls
  **Claude**. Building/AI-narrating a trip is a write that also incurs model cost, so it is
  deliberately isolated from the cacheable read path. Keep anything that writes trip state or
  calls a text model here.
- Runtime: **Python 3.12**. The two functions are separate deploy units so the cacheable read
  path and the expensive stateful path scale and cache independently.
- **Edge shared secret** (`backend/shared/auth.py`, AA-878): both Lambdas check the
  `X-TripPlanner-Key` header the BFF sends (Vercel env `TRIPPLANNER_API_KEY`, same value). On Lambda
  the expected key is read from Secrets Manager by the name `tripplanner/<ENVIRONMENT>/api-key`
  (derived from the existing `ENVIRONMENT` env, IAM-role read, cached 10 min per container so a
  rotation lands without a redeploy); a `TRIPPLANNER_API_KEY` env value still wins (local dev). It
  **fails closed**: on Lambda a missing, unreadable, empty or `REPLACE_ME` key answers 503
  (`{"evt":"api_key_unavailable"}` log line, no value). Only off Lambda with no env key is the gate
  disabled (local dev, unit tests). The secret itself is Infra's (`tripplanner.tf`).

### Jobs Lambda and tripplanner.job (AA-861)

- **`tripplanner.job`** (tp-021) is TripPlanner's job queue, rendered from the shared job table template (root ADR
  0003a; AA-CIS-App `shared/jobs/job_table_template.sql`, byte copy + comparator in `migrations/tests/job_template/`,
  checked by `test_tp021_pg.py`). Kinds are `tp_*`. Payloads and results hold flags, ids and counts only (DM-6.3).
- **The worker is a third Lambda**, `aa-tripplanner-<env>-jobs` (`backend/jobs/handler.py`, same zip as browse and
  assembly: `jobs.zip`; Infra `tripplanner_jobs.tf`): its own role (DB + Sentry DSN secrets only: no Bedrock, edge key
  or SES), reserved concurrency 1 (one DB connection), no HTTP route. EventBridge Scheduler sends `{"op": "tick"}`
  daily at 19:30 UTC: the Lambda queues the day's scheduled runs (idempotency key per kind and UTC day) and runs due
  jobs (`backend/jobs/runner.py`: reap expired leases, claim with `FOR UPDATE SKIP LOCKED`, lease = time left + 60 s,
  retry with backoff, stop with 45 s left). A tick that ends with a failed job or a HELD retention run raises, so the
  errors alarm fires. Operators use `enqueue` / `run` / `status` by direct invoke (`docs/jobs-runbook.md`).
- **`tp_retention_outbox_redact`** (`backend/jobs/outbox_redact.py`, DM-6.6 / DM-7.2): outbox rows older than 90 days
  get `payload` rebuilt from an allowlist (`backend/jobs/redact.py`, AA-Booking's `bk_retention_inbox_redact` rule plus
  `customer.identity`): name, email, phone, identity and custom day notes nulled, unknown keys dropped; `redacted_at`
  set; row, `idempotency_key` and `schema_version` kept, so AA-Booking's dedupe and the outbox's own `ON CONFLICT` keep
  working. DRY RUN by default; the daily run stays a DRY RUN until a manual live run of the same window succeeded, then
  runs live and HOLDs above 2x the last live run. `manual_override` + `older_than_days` counts younger rows in a DRY
  RUN anywhere; a live override needs `RETENTION_OVERRIDE_ENABLED=true` on Dev (Terraform, default off).
- The outbox is still append-only for everyone else: the redaction kind is its one UPDATE (tp-021 table comment).

### Guest identity (AA-827)

- **Rule: `session_id` is an identifier, never a credential; every trip mutation goes through
  `_guard`** (`backend/assembly/handler.py`). The credential is a random guest token (32 bytes,
  base64url, 43 chars) that the BFF keeps in an HttpOnly cookie (`__Host-tp_guest` in production,
  `tp_guest` under local http; Secure, SameSite=Lax, Path=/). **Lifetime rule:** Max-Age 400 days
  (the browser cap), sliding — every matched request re-sends it with a fresh Max-Age; nothing else
  expires a guest (`sessions.expires_at` is informational, not enforced). `frontend/proxy.ts`
  mints it on `/plan` and on `/api/trip` only (the home, destination and tour pages are CDN-cached, so a
  per-visitor Set-Cookie must never ride on them), as a
  raw `Set-Cookie` header — never `res.cookies.set`, which also echoes the value into an
  `x-middleware-set-cookie` response header that client JS can read — and refuses a cross-site POST
  (403) before any mint, so another site can never overwrite the cookie. Client JS never sees it. The
  browser keeps only `{sessionId, tripId}` (v4 UUIDs) in localStorage.
- The `/api/trip` BFF forwards the token on POST and on the GET reads (AA-909), in
  `x-tripplanner-guest`; it refuses a cross-origin POST (403) and never logs. `tripplanner.sessions.guest_token_hash` stores sha256(token)
  only (`bytea`, 32 bytes, migration tp-011); the plaintext `guest_token` (= id::text before AA-827) is
  no longer written, tp-012 nulls the legacy values and `sessions_guest_token_retired_chk`
  (`CHECK (guest_token IS NULL)`) keeps it empty, so a pre-AA-827 Lambda artifact can no longer insert
  a session (rollback = revert PR). DROP COLUMN is a later contract file. One cookie can bind several sessions, so the hash has no unique index.
- `_guard` runs on all 8 mutations (add, remove, reorder, send-to-advisor, narrate, apply-route, add /
  remove day): 401 `guest_session_invalid` when the token does not match the session
  (`assembly/guest_session.py`), 403 `forbidden` when the session does not own the trip
  (`trip_drafts.session_id`; a trip with no draft yet has no owner). It also creates the sessions row
  on first use (`events.append_event` no longer does). The frontend (`lib/api.ts` `postTrip`) forgets
  its ids (only if localStorage still holds the failing ones) and reloads with a new trip on either
  answer; a second reset in the same tab within 60 s does not reload.
- **No cookie is not "another browser".** When a POST came without a cookie, the BFF uses the token
  the proxy minted for it; if the Lambda refuses that token, the BFF answers 401
  `cookies_required` instead of `guest_session_invalid`. The client retries once (a cookie lost once is
  now replaced) and, on a second `cookies_required`, keeps its ids and says cookies are needed. A
  browser that blocks the cookie can make the first edit of a trip only.
- Known gap: two planner tabs opened at the same moment by a first-time visitor (no cookie yet) can
  each get a different token; the later Set-Cookie wins, and a session already bound to the earlier
  one is reset on its next edit. Minting on the `/plan` document makes this rare, not impossible.
- Legacy grace window `config.GUEST_LEGACY_GRACE_UNTIL` is **closed** (code constant 16/10/2026 00:00
  UTC, already past; S239: Dev has no real guests, so the planned 19/10 cutoff was not waited for). A
  session id alone, a tokenless request and a pre-AA-827 session (no hash) all get 401. The grace code
  path (bind a legacy session to its first token, accept tokenless for an unhashed session) is kept in
  `assembly/guest_session.py` but dormant; reopening it means moving the constant into the future in
  a reviewed deploy.
- `trip_id` / `session_id` must be canonical UUIDs (400 otherwise, value not echoed); an unexpected
  error answers 500 and logs only its type, place and route name. Each guarded request prints one
  `{"evt":"guest_auth","outcome":…,"route":…}` line (no token, hash or ids).
- **Reads (AA-909):** the four trip GETs (`/trip/{id}`, `/suggestions`, `/coverage`,
  `/stops/{dest}/activities`) run `_read_guard`: the token's sha256 must equal the hash of the session
  that owns the trip (`trip_drafts.session_id` → `sessions.guest_token_hash`,
  `guest_session.authorize_read`). Every refusal (no token, malformed, wrong, another trip's, no such
  trip, no draft yet, owner session without a hash) is the unknown-route 404 `{"error": "not found"}`,
  so existence never leaks. Reads never create or bind a session and never reset the client: the
  frontend treats a failed read as an empty trip (a fresh trip has no draft and also reads 404). The
  `/api/trip` BFF sends the itinerary read's 404 to the browser as a 200 empty trip
  (`tripReadAnswer`, AA-837: no red console error on a first visit); the other reads pass through.
  Outcomes in the `guest_auth` line: `read_ok`, `no_token`, `no_owner`, `bad_token`, `token_mismatch`, and since
  AA-908 `account_read_ok` (reads) / `account_ok` (mutations) for a signed-in owner on another device.

- **Traveller accounts (AA-908, tp-019; root ADR 0006 Phase 4).** `tripplanner.traveller_accounts` holds one row per
  signed-in traveller, keyed by the Cognito EXTERNAL identity `(iss, sub)` of the verified access token (UNIQUE,
  tagged `pii:online_id`), with no email column: the email is read from the token whenever it is needed.
  `sessions.account_id` (nullable FK, ON DELETE SET NULL, partial index `sessions_account_idx`) links a guest session to the account that
  claimed it at sign-in. The claim sets it only where it is NULL, so a session owned by an account is never moved.
  The runtime role may SELECT / INSERT / UPDATE accounts, never DELETE.
- **Signed-in traveller (AA-908 S4, `backend/assembly/account.py`).** The BFF forwards the traveller's Cognito EXTERNAL
  access token in `x-tripplanner-access` (`Bearer <jwt>`, from its sealed session cookie; S5). The assembly Lambda
  verifies it with `backend/shared/identity_verifier.py`, a **byte copy** of AA-CIS-App `api/core/identity_verifier.py`
  (root ADR 0006 §8 rule 9; `tests/test_identity_verifier.py` is the CIS suite plus a sha256 pin of the copy: change
  it only by copying the CIS file and updating the pin). Accepted: the EXTERNAL issuer x the public `tripplanner`
  client only, access tokens only (`backend/shared/identity_config.py`: public code constants, env
  `TP_TRAVELLER_ISSUER` / `TP_TRAVELLER_CLIENT_ID` override; no Lambda env needed). The JWKS is fetched through the
  NAT on the first signed-in request of a container and kept (a failed refresh keeps the last good key set).
  - `POST /me/claim` (at sign-in): upserts the account (`last_sign_in_at`) and links every session of the request's
    guest token that no account owns yet. `GET /me/trips` (My Trips): the account's trips, newest first (trip and
    session ids, status `draft` / `sent`, stop count, days, first stop names, dates). Both answer 401
    `{"error":"sign_in_required"}` without a valid token, 503 `auth_unavailable` when no key set could ever be loaded.
  - **The claim takes every unowned guest trip of this browser, by design.** On a shared browser that includes trips a
    previous anonymous visitor built: they appear in the new account's My Trips and can be sent under its verified
    email. The guest cookie is the only proof of a guest trip, so there is nothing finer to check. The answer carries
    the count (`claimed`); the privacy page tells travellers to sign out on a shared computer, and sign-out clears the
    guest cookie (S5).
  - Guards: when the guest token does not prove the session, `_guard` lets through the account that owns it
    (`account_ok`: My Trips opened on another device), and `_read_guard` the account that owns the trip's session
    (`account_read_ok`). A signed-in mutation that the guest token does prove links its session to the account (a trip
    started after sign-in). An owned session is never moved to another account. Without a valid access token (none,
    bad, expired, another client, an ID token) every answer is exactly the guest one (401 / 403 / the unknown-route
    404).
  - Hand-off: a signed-in send carries `customer.email` = the token's email, `verified` = the token's
    `email_verified`, `identity` = `{iss, sub}` (contract v1.4); no `tripplanner.customers` row is written or matched.
    A guest send is unchanged (`verified: false`, `identity: null`).
  - Log lines: `guest_auth` outcomes, `traveller_auth` (`invalid` / `unavailable`) and `traveller_account` (route,
    outcome, counts); never iss, sub, email or a token.
- **Traveller sign-in in the BFF (AA-908 S5; root ADR 0006 decisions 1 and 4: email code only, no password).**
  Option (b): the BFF drives Cognito's public API itself (`lib/cognito.ts`), there is no managed-login redirect.
  `POST /api/auth/start {email}` calls `SignUp` without a password (a new address), else `InitiateAuth` `USER_AUTH`
  with `PREFERRED_CHALLENGE=EMAIL_OTP` (an existing one), else `ResendConfirmationCode` (a sign-up never confirmed);
  the browser gets the same answer in every case.
  - **Only this BFF may create a traveller (sign-up proof).** `SignUp` carries `ValidationData` `bff_ts` + `bff_proof`
    = hex HMAC-SHA256(Vercel env `TP_COGNITO_SIGNUP_PROOF_KEY`, `"<email>\n<bff_ts>"`); the Infra pre sign-up trigger
    refuses a native sign-up without a valid proof less than 5 minutes old. Otherwise anyone could pre-register a
    traveller's address with a password through the public API and inherit the account once the traveller confirmed
    it with our code (review round, S250). So every UNCONFIRMED traveller was created here, without a password.
  - Two fallbacks for Cognito answers the unit tests cannot pin (confirm in the live E2E): a sign-up `Session` older
    than 3 minutes refused by `ConfirmSignUp` is retried without it (then a sign-in code is mailed), and a sign-in
    code refused as not authorised while its 3-minute session is still young requests a new sign-up code (an
    abandoned sign-up may get a simulated challenge under `prevent_user_existence_errors`). `POST /api/auth/verify {code}` confirms (`ConfirmSignUp`, then
  `InitiateAuth` with its session) or answers the challenge (`RespondToAuthChallenge`), stores the tokens and calls
  the Lambda's `POST /me/claim` with the guest cookie (best effort). After a sign-up, a first token that does not
  say `email_verified` is re-minted once with its refresh token (the auto sign-in can carry the attributes from
  before the confirmation; S251). `POST /api/auth/sign-out` revokes the refresh
  token (`RevokeToken`), clears the session cookies **and the guest cookie** (the next person on this browser must
  not open the account's trips), and the client forgets its trip ids. `GET /api/auth/status` (always 200) feeds the
  header link and the send form; `GET /api/me` (401 `sign_in_required` when signed out) returns the email and the
  Lambda's `/me/trips`. My Trips shows the sign-in prompt only on 401; any other failure shows Try again and Sign out.
  - Cookies (`lib/authSession.ts`, sealed with AES-256-GCM by `lib/seal.ts`, a copy of AA-Partner's with its own
    purposes and salt; key = Vercel env `TP_SESSION_SECRET`): `__Host-tp_auth` (access token, its expiry, email),
    `__Host-tp_refresh` (refresh token; its own cookie because a Cognito refresh token is about 1.8 kB) and
    `__Host-tp_signin` (the pending code step, 10 minutes). HttpOnly, Secure, SameSite=Lax, Path=/. When fewer than
    60 s of the access token remain, the BFF refreshes it (`GetTokensFromRefreshToken`; rotation is on, a rotated
    refresh token replaces the old one, the 60 s grace covers two parallel refreshes). Only a refresh refused as
    `NotAuthorizedException` (revoked or expired) signs out; a WAF block, throttling, a Cognito 5xx or a network error
    keeps the cookies (the still-valid access token is used, else that one request is signed out) so the next
    request retries.
    Only the no-store routes `/api/auth/*`, `/api/me` and `/api/trip` set them, never a CDN-cached page.
  - `/api/trip` forwards the access token (`x-tripplanner-access`) when signed in, so My Trips opens a trip on any
    browser and signed-in edits link their session.
  - WAF: every Cognito call carries `x-aa-traveller-bff` = Vercel env `TP_COGNITO_BFF_KEY` (AA-CIS-Infra secret
    `tripplanner/<env>/cognito-bff-key`) and `X-AA-Traveller-IP` = the first `x-forwarded-for` address, so the Cognito
    web ACL counts each traveller, not the shared Vercel egress address (Infra rule `BffRatePerTravellerIp`). The two
    go together: without a valid traveller address neither is sent, and the call is counted per egress IP.
  - Sign-in claims every unowned guest trip of this browser (§Traveller accounts); the privacy page tells travellers
    to sign out on a shared computer.
  - Without `TP_SESSION_SECRET` (32+ characters) sign-in answers 503 `sign_in_unavailable` and the header shows no
    link: the planner works as a guest. Per-PR previews can sign in only when the env values are set for Preview (new addresses also need `TP_COGNITO_SIGNUP_PROOF_KEY`).
  - Pages: `/sign-in` (two-step email -> code form, `?next=` same-site path, default `/trips`) and `/trips` (My
    Trips: status Draft / Sent to an advisor, stops, days, "Open" writes the ids to localStorage and opens `/plan`),
    both noindex. The header link (`components/AccountLink.tsx`, "Sign in" / "My trips") is fetched by the browser,
    because the kit pages are CDN-cached. The send form shows the signed-in email read-only.
- **My Payments on `/trips` (AA-682 S5; contract `booking.customer.v1`, root `docs/contracts/`).** Below the trips,
  `components/MyPayments.tsx` lists the traveller's payment requests from AA-Booking, read only: request number,
  total, paid, balance, next due date; the schedule with chips Scheduled / Due soon / Paid / Overdue; the payments AA
  verified; "How to pay" (Booking's `payment_instructions`, only while money is owed) and "Questions? Contact your trip
  consultant". A request sits under the planned trip it came from when that trip is in My Trips (`planner_trip_id` =
  this repo's trip id), else under "Your trip" + its booking reference. English, no pay button, no upload.
  - BFF `GET /api/me/payments` (no-store, like `/api/me`): forwards the traveller's own access token as
    `Authorization: Bearer` to `GET <BOOKING_API_URL>/booking/customer/v1/payments` (default the Dev Booking API
    `https://api-booking.lumiguides.it.com`; env `BOOKING_API_URL` overrides it, https origin only; 8 s timeout); no
    TripPlanner secret goes to Booking. It forwards only the contract's fields (`lib/myPayments.ts` `pickPayments`).
    401 `sign_in_required` only when there is no session (the section then shows "Sign in again"). Booking refusing
    the token (401/403) is 502 `booking_refused`: the section renders only after `/api/me` succeeded with the same
    session, so the refusal is Booking-side (client id, issuer, clock) and never clears the session or claims it ended
    (no sign-in loop); any other failure 502 `upstream_error`. Both show Try again (`lib/myPayments.ts`
    `upstreamFailure`). Log line `bff_upstream` route `booking`, op `my_payments`, status and ms only.
  - A trip sent as a guest (lead customer hand-off) never matches a signed-in identity in Booking, so its payment
    requests do not show here; the empty state says the consultant sends those by email.
  - Nothing is stored in `tripplanner.*`. `ui/Badge.tsx` gained the `danger` tone (red on red-soft, a pair
    `lib/contrast.test.ts` already checks) for the Overdue chip.

### Data model — event-sourced trips

- A trip is stored as an **append-only event stream** rather than a single mutable row. Each user
  action (add destination, reorder day, remove stop) is an event; the current trip state is the
  fold over its events. This gives a natural history/undo and makes concurrent edits reconcilable.
- **Saved stops AA no longer serves (AA-986 L6, root ADR 0015 decision 5).** The trip's components are read from
  `catalog.v_tour_component` (live tours only), so a pinned component of a retired or paused tour is not found. It is
  **not** dropped: it stays in the event fold, `GET /trip/{id}` lists it in `unavailable_stops` (`available: false`,
  `reason` = `retired` | `paused` | `stop_removed` (tour served, stop gone) | `not_served`, `reason_detail` = the CIS
  lifecycle reason) and marks each served stop `available: true`; the map, legs, coverage, suggestions and narration
  keep using the served stops only. Reasons come from `catalog.v_tour` + the tombstone `catalog.v_tour_retired`
  (`backend/assembly/availability.py`, each read in a savepoint; unreadable → `not_served`, one
  `tour_availability_unreadable` log line). To name such a stop after it left the catalog, the `trip_drafts`
  projection stores per stop its name, tour key, tour day, place and point, and carries an unavailable stop over
  (`available: false`) on every rewrite. The planner shows a banner and the stops greyed with a Remove button
  (`TripPanel`, `lib/availability.ts`); a restored tour comes back as a normal stop. The hand-off still goes, with
  the flag (contract `inquiry.submitted` v1.3).
- **Taste vector**: a per-traveler preference signal built from their interactions, combined with
  **geographic** proximity to drive suggestions ("you liked X, and Y is nearby and similar").
  Since AA-590 (PR #46) "suggest next place" re-ranks by 0.6 taste (pgvector) + 0.4 distance to
  the trip's last stop (absolute km, ramp 150-1500 km) in `backend/assembly/suggestions.py`;
  with no anchor stop it falls back to taste order only. Since AA-674 (step 2) the candidates are
  **route-constrained**: what real tours do next from the trip's last stop. That means the
  components of the days after any tour stop (`catalog.v_tour_stop`) within 150 km of it (same place, or a transfer to
  another tour), up to that tour's next stop elsewhere. The response carries `mode`
  (`route` | `taste`) and each suggestion a `tour_count`. When no tour passes near the last stop,
  it falls back to taste within the trip's countries.
- **Planner on AA routes (AA-674, Jira PR-11)**:
  - The trip panel starts with a route planner: "Heading to" + days → proposals from real AA
    tours (`/browse/routes`), previewed on the map. "Start with this route" pins them
    (`POST /trip/{id}/apply-route`).
  - A coverage block shows the AA tours the trip is built from (`/trip/{id}/coverage`: legs,
    transfers, gaps with a reason).
  - Each day card lists "Activities here" (`/trip/{id}/stops/{dest}/activities`); checking one
    pins its component.
  - Days can be added (extra night, free day, extra day at the end; events `add_day` /
    `remove_day`, migration 006) and are shown as the traveller's additions, not part of a tour.
- **Map extras (AA-589, PR #46)**: arrival/departure gateways are drawn on the map (airport
  marker + dashed arc to the first/last stop; `ALL_GATEWAYS` / `nearestGateway` in
  `frontend/lib/mapbox.ts`), and hovering a country in the country picker previews its highlight
  (custom listbox — a native `<option>` does not fire hover events). The gateway table covers
  only 6 countries so far (Laos, Sri Lanka, South Korea, Nepal, Japan, India); other countries
  snap to the nearest listed gateway.
- Schema ownership: everything under `tripplanner.*` is owned by this repo's migrations
  (ecosystem rules: `AA-Ecosys/docs/database/schema-ownership.md`). Every migration registers itself
  in `shared.schema_versions` with the `tp-NNN` key (from tp-007, which also backfills tp-001..006
  for objects that exist) and is applied through AA-CIS-Infra **DB Migrate** from an identical copy
  in `db/migrations/tripplanner/`.
  CIS content (places included) is read only through the `catalog.*` views; never migrate or
  mutate another owner's schema from this repo.
- DB roles (X1, AA-852): **from tp-013 (applied in the X1 S8 train; until that apply the RDS master
  `aa_cis_admin` still owns everything)** the schema and every object in it are owned by the NOLOGIN role
  **`tp_owner`** (created by the Infra role bootstrap `rl-001`), not by the RDS master. The Lambdas log in
  as **`tripplanner`** (runtime, DML only, name kept), which gets its rights from default privileges
  `FOR ROLE tp_owner IN SCHEMA tripplanner` (tables DML, sequences USAGE/SELECT). From tp-013's apply
  DB Migrate runs every later file as `tp_owner` (it reads the live schema owner and `SET ROLE`s to it),
  so a new file creates objects owned by `tp_owner` with no `OWNER TO`, writes `ALTER DEFAULT PRIVILEGES
  FOR ROLE tp_owner IN SCHEMA tripplanner`, never switches roles or grants role memberships itself and
  never creates roles or extensions (those live in Infra `db/migrations/roles/`). CI job "Migrations"
  enforces this: `migrations/tests/migration_lint.py` (TripPlanner rules), the AA-CIS-Infra
  `db/run_migration.py --inspect` lint at a pinned commit (`test_infra_runner.py`) and a replay of
  tp-001…013 on PostgreSQL 15 + pgvector (`test_tp013_pg.py`; `test_tp015_pg.py` continues it with tp-014/015 as
  `tp_owner`, `test_tp016_pg.py` with tp-016, `test_tp017_pg.py` / `test_tp018_pg.py` / `test_tp019_pg.py` / `test_tp022_pg.py` with tp-017 / tp-018 / tp-019 / tp-022). `test_replay_twice_pg.py` (AA-849) applies every file twice: the second run must not error and
  must change nothing, and the registered `tp-NNN` keys must equal the files, so **every new file must be
  safe to run again** (a file whose objects a later file dropped — tp-003, tp-004, tp-013 after the tp-018 drop — is
  listed in `SUPERSEDED_BY_DROP` with the dropping file, and must still fail or change the state when re-run) (`IF NOT EXISTS`, `CREATE OR REPLACE`, `ON CONFLICT (version) DO NOTHING`, no
  unguarded seed rows). Nothing in that job may be skipped (`--fail-on-skip`). Undoing tp-013 is the prepared Infra
  `roles/` file (branch `feat/x1-s7-tp013-undo`) run as the master, never a `tripplanner/` file.
- **Column tags (data standards DM-6.1, tp-016, AA-877).** A column holding personal data carries
  `COMMENT 'pii:<class>; <free text>'` (`contact`, `name`, `address`, `profile`, `online_id`, `free_text`, `travel`,
  `payment`; further classes named later as `also pii:<class>`), credentials and their hashes
  `conf:credential; …`. tp-016 tagged `customers.name` / `phone` / `email`, `outbox.payload`,
  `v_inquiry_outbox.payload`, `trip_drafts.itinerary` / `date_start` / `date_end`, `trip_events.payload` and restated
  the tp-011 `sessions.guest_token*` comments in the standard form. A new column holding such data ships its tag **in
  the same migration file** that creates it (COMMENT replaces the whole comment; a view rebuilt with DROP + CREATE must
  restate its tags). `migrations/tests/test_tp016_pg.py` fails on an untagged column named like personal data that is
  not on its allowlist; the schema reference section *Personal and confidential data tags (DM-6.1)* (AA-CIS-Infra
  `db/gen_schema_reference.py`, the authority) is the column inventory of the RoPA (AA-Ecosys `docs/legal/register.md`).
- **Only CIS writes content** (root ADR 0002, 01/10/2026). This repo writes only `tripplanner.*`
  user state (sessions, drafts, customers, trip events) and `shared.llm_call_log`.
  **The place pipeline (itinerary places, geocoding, tour graph) is CIS's** (root ADR 0007,
  AA-712: CIS job kinds `place_extract` / `place_geocode` / `place_graph`, schema `geo`). Since
  slice 6a this repo has **no place writers**; since **AA-712 X4** it has **no reader of anything of CIS but
  the catalog views** (plus the Model Gateway tables): the `PLACES_SOURCE=legacy|cis` switch and every legacy
  reader of the frozen tripplanner place tables (`itinerary_components`, `tour_day`, `tour_stop`, `tour_graph_*`,
  `tour_leg`, `tour_junction`), `shared.destinations`, `gold_aa_internal.published_tours` (and its AA-tenant GUC)
  are gone, tp-018 drops the frozen tables and CIS 270 revokes the `tripplanner` grants on the CIS objects they
  used. The guard `backend/tests/test_extraction_ops.py` fails if a backend SQL string names any schema-qualified
  object outside `catalog.*`, the Model Gateway tables (`shared.llm_role_config`, `llm_model_catalog`,
  `llm_call_log`) and the live `tripplanner` tables (sessions, customers, trip_events, trip_drafts, outbox,
  narration_cache, llm_spend_daily, component_id_map, traveller_accounts), or writes place data.
  The **browse** readers (tiles, countries, by-country, destination detail, search, routes, tour-route, shapes)
  read the CIS catalog views of CIS migration 228 and later (`catalog.v_tour_component`, `v_tour_stop`,
  `v_tour_leg`, `v_tour_junction`, `v_destination`, contract `catalog.graph.v1.2`; keyed by catalog
  `source_id`, live tours only, no AA tenant needed). The **assembly** readers (AA-847 slice 6c) too:
  `events._current_components` / `current_itinerary`, suggestions (taste, route candidates over
  `catalog.v_graph_node` / `v_tour_stop` / `v_tour_component`, the fallback top-k), coverage (options + tour names
  from `catalog.v_tour`), activities, apply-route, add-day and the narration day texts (`catalog.v_tour.itinerary_text`).
  Browse search and the suggestion fallback rank through `backend/shared/vector_topk.py` (`source=CIS`, the
  default): the HNSW top-k scans `catalog.v_component_embedding` (CIS 253, not a security_barrier view,
  `tripplanner` only; `hnsw.iterative_scan` on pgvector ≥ 0.8; the same rows as `v_tour_component` — live tours on
  usable places, never a suspect or merged one — so nothing is checked after the scan), the exact fallback
  `catalog.v_tour_component`. **Component ids across the flip** (22/10/2026, AA-847): the views serve stable uuid5
  component ids; drafts from before the flip hold ids of the dropped `itinerary_components`.
  `tripplanner.component_id_map` (tp-017; filled once, right before the flip, by AA-CIS-Infra
  `db/scripts/aa847_component_id_map_fill.py`) maps an old id to its catalog id and stays after tp-018; every
  assembly reader resolves the ids it is given through `backend/shared/component_ids.py` first. **Ids a CIS
  rebuild drops** (AA-990, CIS 285): CIS writes a redirect for every component id a place rebuild could not keep
  (`catalog.v_component_redirect`, component → the component of the same place); `component_ids.id_map` follows the
  chain across both sources (the id map first at each hop, then the CIS redirect; ≤ 5 hops, cycle-safe), so a saved
  pin survives later rewrites; an unreadable view (before 285, after its rollback) degrades to the id map alone with
  one `component_redirect_unreadable` log line. Added days resolve their `after_component_id` the same way. The map
  stays insert-only (tp-017); the one-time AA-CIS-Infra
  `aa990_component_id_backfill.py` only INSERTs (pins dropped before CIS 285). The hand-off legs
  use the narration's day check (`master_content.guarded_days`, TP #86): a tour day whose AA text does not name the
  stop moves to the nearest day that does. There is no rollback to the frozen tables after tp-018.
- **Map attribution and precision (ADR 0007, AA-847)**: wherever a Geoapify-located point is shown (planner map,
  static maps on tour / country pages, the destination popup) the page shows "Powered by Geoapify" (link) and
  "© OpenStreetMap contributors" (`components/GeocodeAttribution.tsx`, `lib/types.ts GEOCODE_ATTRIBUTION`). A place
  with `precision = 'area'` (the model's point) is drawn as a translucent area on the planner map and a grey pin on
  static maps, never as an exact pin (only `point`, or a legacy row, is an exact pin: `lib/lines.ts isExactPin`).
- **Line places (slice 6d, AA-847, owner decision S246: drawn, never a pin, a circle or hidden)**: a destination with
  `precision = 'line'` (river, road, trail; CIS 256) leaves the clustered point source for the unclustered
  `destination-lines` source (white casing; water blue, roads ink solid, trails / approximate routes ink dashed;
  the name along the line; a 14 px invisible hit line opens the same popup). Tiles select by bbox and carry no
  geometry; `GET /browse/shapes?ids=&level=lo|mid` (`backend/browse/shapes.py`, `catalog.v_destination_shape`) serves
  it by id (`lo` below zoom 7), cached per id in the client. On the trip map a line is never a waypoint: the day's
  stretch (`segment` on the component) is a gold **`feature` leg** that replaces the road / flight leg between the
  same two stops (no Directions request; joins only for an end > 2 km away), a line with no geometry yet is a dashed
  "approximate route" between the day's stops (`lib/lines.ts planTrip`, `lib/mapbox.ts fetchTripLegs`). List: a
  line glyph and "A → B · ≈ n km". Static maps: `path` overlays (≤ 3 lines, ≤ 60 vertices, `lib/staticMap.ts`).
  Kill switch `NEXT_PUBLIC_MAP_LINES_PROVISIONAL` (⚠️ underscore name, set by hand) draws every line provisional.
- Photos (AA-708, 01/10/2026): AA marketing photos are synced and matched by CIS (`photo_sync`,
  `shared.place_photo`). This repo reads them only through the catalog views (`catalog.v_tour_photo`,
  `v_destination_photo`, `v_destination.cover_image_url`, set by CIS from the photo matched to a place on the
  tour's own itinerary). Image URL = CIS public API base + view `path` + `?size=large|small`. Cover matching runs
  in CIS after its place build (`geo.*`) — see `docs/ecosystem-architecture.md` §4.5.

### AI (Bedrock)

- **Every model call goes through the ecosystem Model Gateway** (AA-685,
  `backend/shared/llm_gateway.py`). The model, fallback order and price for each stage come
  from the gateway tables that AA-CIS-App owns in the shared RDS (`shared.llm_role_config`,
  `shared.llm_model_catalog`). Every call attempt writes one `shared.llm_call_log` row (failed
  attempts too, with `stop_reason = 'error'`; a model with no catalog price logs `cost_usd` NULL),
  tagged `quality_signal.app = "tripplanner"`, so it shows on the CIS External Spend page. The
  `tripplanner` DB role has SELECT on the two config tables and INSERT on the log (CIS
  migration 173). If the tables cannot be read, built-in routes keep the previous models.
  Stages:
  - `tp_compose`: assembly narration (Claude Sonnet 4.6 via the satellite role);
  - `tp_search_embed`: browse free-text search, one Cohere Embed v4 call per distinct query (acc2
    first; on an acc2 throttle the route falls through to the satellite row `cohere-embed-v4-acc3`
    — same model and 1536-d, AA-862).
    **Search under load (AA-856)**: Cohere Embed V4 on acc2 allows 20 requests per minute (Service
    Quotas), so `llm_gateway.query_embed_cache` keeps query vectors per Lambda container (bounded LRU,
    1024 entries, 6 h TTL; key = stage + base Bedrock model id(s) of the route (geo prefix stripped, so account rows of one
    model share entries) + input type + normalised text; a hit makes no
    call, no log row, no spend, and is served even over the daily cap). When the embed fails (every
    account of the route failed: throttle or any Bedrock error; failed attempts still logged) search answers 200 with a keyword ranking
    (`ILIKE` per query word over destination/component name, country and text, same filters and
    active-tour rule; no pg_trgm on the RDS) and `"ranking": "keyword"` instead of a 500; when any
    attempt of the failed chain was a throttle the container skips the model for 10 s. A query with no letter or digit (empty,
    punctuation, zero-width characters) answers `{"destinations": []}` with no model call. The acc2
    and satellite embed clients make one attempt each (no boto retry); the satellite AssumeRole uses
    STS timeouts of 3 s connect / 5 s read, at most 2 attempts. Each search prints one `{"evt":"search_embed","outcome":hit|miss|fallback,...}` line
    with the container's cache counters (never the query text);
  - `tp_extract` / `tp_component_embed`: the former offline extraction pipeline; no caller since
    AA-712 slice 6a (the frozen tables it filled are dropped by tp-018; the built-in routes can go next).

  An admin can change a stage's model on the CIS admin Settings page (TripPlanner group) without
  a TripPlanner deploy; the Lambdas pick the change up within 60 s. Shadow config on a tp_* stage
  is not run here (a warning is printed): a consumer app does not pay for shadows.
- **AI guards (AA-805, migration tp-010)**: a daily hard stop on **customer-facing** model spend
  only — stages `tp_compose` + `tp_search_embed` (`llm_gateway.ONLINE_STAGES`,
  `TP_DAILY_SPEND_CAP_USD`, counted in `tripplanner.llm_spend_daily` by the gateway — the role
  cannot read `shared.llm_call_log`). Offline stages (`tp_extract`, `tp_component_embed`) are
  logged but neither counted nor capped here, so a backfill never takes search/narration down.
  Over the cap narration falls back to AA text + neutral day lines (`ai_budget_exhausted`) and search
  answers the keyword ranking (200, `ai_budget_exhausted`; it was a 503 before AA-642 S0).
- **Per-visitor AI limits (AA-1015, migration tp-023)**: `backend/shared/ratelimit.py` counts each request that would
  call a model in `tripplanner.ai_rate_counter` (fixed windows, key = HMAC of scope + kind + value keyed with the edge
  key: no IP, session or account stored): narrate 10 / hour / guest session, search 30 / minute / traveller IP, x2
  signed in. Over the limit: 429 `rate_limited` with `retry_after_s` and a calm EN / VI line in the planner. The BFF
  forwards `x-tp-client-ip` (all trip calls and search) and `x-tp-signed-in` (search; narration uses the verified
  access token); both are trusted only with the right `x-tripplanner-key`. The counter fails open (ERROR line); the
  jobs Lambda kind `tp_retention_ai_rate_counter` deletes windows older than 2 days (DM-7.2). Optional stage caps
  `TP_DAILY_SPEND_CAP_SEARCH_USD` / `_COMPOSE_USD` (unset = combined cap only) and a once-a-day `llm_spend_alert`
  line at 80 % and 100 % of a cap. The bot check (BotID / Turnstile) is not built yet (cost not checked).
- **Fail closed + degrade mode (AA-642 S0, root ADR 0009 decisions 5 and 6)**: the gateway never calls a
  model it cannot account for. Spend counter unreadable (or a counter write failed in the last 60 s,
  `SPEND_UNREADABLE_HOLD_SECONDS`, per container) → `budget_status` is `exhausted` with reason
  `spend_unreadable`; route config unreadable → `load_route` raises `AiPaused("route_unreadable")` (the
  hard-coded `BUILTIN_ROUTES` models are never called any more); every model of the route failed
  (provider outage, e.g. acc3 refusing) → degrade too. Narration then answers 200 `basic` /
  `master_basic` with `"ai_paused": true` (never cached; a defect that is not a provider error still
  answers 502), search answers the keyword ranking with `"ai_paused": true`. Each degraded narration
  prints `{"evt":"narration_degraded","reason":…}`; each gateway failure that changes what travellers
  get prints `{"evt":"llm_gateway_error","level":"error","what":…}` (route_unreadable,
  spend_unreadable, spend_write_failed, call_log_write_failed). The planner shows one calm line under
  the narration (`lib/aiNotices.ts`, English by default, Vietnamese when `<html lang="vi">`).
  `/narrate` is limited to the session that created the trip and reuses
  `tripplanner.narration_cache` for an identical itinerary (the key includes the AA master day
  text, so CIS edits miss the cache). Search embeds the query as `search_query` and ranks only
  components of active, not soft-deleted tours.
- **AI labels (AA-819, AUP 2026-11)**: every AI-written text block a traveller sees carries a label
  (`components/AiLabel.tsx`, a button with an aria-expanded explanation, not a `title` tooltip):
  the site pill, the tour page summary / highlights / day by day, the map popup and stop activity
  texts ("Adventure Asia AI", "AA AI" below the sm breakpoint — brand name chosen by Nghiệp S239: CIS writer-model output, published when it passes CIS's automated checks;
  only flagged tours reach a human, so no label claims a team review of every text), and the
  narration (`master` and `master_basic` = "AA itinerary · Adventure Asia AI", the same CIS text;
  `mixed` = "AA itinerary + Adventure Asia AI"; `llm` = "Adventure Asia AI narration"; `basic` = neutral place lines only, no label). The
  narrate API's `ai_generated` is true for every source but `basic`. Place search and "You might
  also like" suggestions both rank by Cohere embeddings (suggestions: route-constrained, ranked by
  the pinned components' taste vector), and the site pill names both.
  Narration day choice (`master_content.resolve_days`), because a component row's tour day can be
  wrong: per trip day, the own tour day text of any entry whose text names its stop (component or
  place name, accent-insensitive, generic suffixes such as "Temple" dropped); else the nearest other
  day of the same tour that names it and that no other entry of the itinerary owns or already took;
  else the own day text as it is; only a day with no AA text goes to the LLM.
- Cross-account Claude access follows the ecosystem Bedrock routing: **acc3 only** (the same
  account the CIS side uses; see `docs/ecosystem-architecture.md`). AA-998 retired the former acc1
  fallback and accepted the loss of a cross-account fallback: `llm_gateway.SUPPORTED_ACCOUNTS` is
  `acc3`, `acc2`, so a catalog profile or `account_route` naming any other account is never called;
  when acc3 fails the route's next model is tried, then the call fails (narration falls back to AA
  text, search to keyword ranking).
  Cohere embeddings run on acc2 first; the route's satellite row (`cohere-embed-v4-acc3`, CIS
  migration 240) takes over on an acc2 throttle (acc2 has 20 rpm, acc3 2,000 rpm). Each
  container caches one no-retry satellite embed client per account (one AssumeRole until 5 minutes
  before the creds expire), AA-862.
- **Former place tables (AA-675 `tour_day`, AA-673 Tour Graph `tour_stop` / `tour_graph_*` / `tour_leg` /
  `tour_junction`, `itinerary_components`)**: built by this repo's former offline extraction pilot, frozen since
  AA-712 slice 6a, unread since AA-712 X4 and **dropped by tp-018** (after a DRY RUN). CIS builds and serves their
  replacement (`geo.*` behind `catalog.v_tour_stop`, `v_tour_leg`, `v_tour_junction`, `v_graph_node`,
  `v_tour_component`).
- **Extraction ops (assembly Lambda, direct invoke only, not reachable over HTTP)**:
  `{"extraction": {"op": …}}` (`backend/extraction/lambda_entry.py`) answers every former op (`tour_days`,
  `components`, `relink`, `tour_graph`, `prune`, and since X4 the checks `list`, `orphans`, `neighbours`) with
  `status` 410 "moved to CIS", anything else 400, before opening a DB connection.
- The trust and role wiring that make this call possible live in **AA-CIS-Infra**, not here.

## Logs, request ids and errors (AA-871)

- **One request id end to end.** The BFF (`frontend/lib/requestId.ts`) keeps a well-formed incoming `x-request-id`
  (`[A-Za-z0-9._:-]{1,128}`) or makes a UUID, sends it to the Lambda as `x-request-id` and echoes it on its response.
  The Lambda (`backend/shared/observability.py`, `instrument()` around both handlers) takes that header, else API
  Gateway's `requestContext.requestId`, else the Lambda `aws_request_id`; it binds it to every log line of the
  invocation and echoes it as `x-request-id`. The ISR catalog reads (`lib/catalog.ts`) send no id: a per-request header
  would change Next's data-cache key; the Lambda then uses API Gateway's id.
- **Lambda log lines are JSON.** The existing `{"evt": ...}` lines go through `observability.emit()` (same fields,
  then the bound `request_id` / `aws_request_id`); stdlib `logging` calls are rendered as JSON by the root handler set
  up on the first invocation; the former plain-text `[llm_gateway]` / `[narration_cache]` prints are `llm_gateway_note`
  / `narration_cache_skipped` lines. One `request_completed` line per invocation: method, **route name** (never the
  path: it carries trip and session ids), status, `duration_ms`. Everything passes `backend/shared/scrub.py` (a copy of
  AA-Booking's: e-mails, `+` phones, Bearer tokens, JWTs, key-like query values, PII-named keys). BFF: one
  `{"evt":"bff_upstream","route","op","status","ms","request_id"}` line per upstream call (Vercel logs).
- **Sentry, only when a DSN is found.** Lambdas: the `SENTRY_DSN` env (local, tests) or, on Lambda with no env value,
  the secret `tripplanner/<ENVIRONMENT>/sentry-dsn` read by name once per cold start (same pattern as the edge key; no
  DSN in the Lambda env; Infra creates the shell and grants the read); no value / failed read = Sentry off, logged as
  `sentry_disabled` with the reason, requests unaffected. Errors only (no tracing, no auto-enabled integrations),
  `send_default_pii=False`, no frame locals, no request data, an isolation scope per invocation (a warm container
  carries nothing over), exception messages of asyncpg / pydantic / jsonschema / json dropped (they echo input values),
  the rest scrubbed; captured: unhandled errors (re-raised) and the assembly catch-all 500. `release` = `backend/GIT_SHA`
  (written by `package_lambdas.sh` from the deployed commit) or `GIT_SHA`; tag `version` = the release (AA-891). `SENTRY_REQUIRED=true` without a DSN fails
  every invocation (the missing-DSN guard; off by default). Live check: a direct `lambda invoke` with
  `{"sentry_test": true}` (not reachable over HTTP) raises `SentryTestError`, which must appear in Sentry with its fake
  e-mail shown as `[email]`. Next.js: `frontend/instrumentation.ts` `onRequestError`
  posts server-side errors with a dependency-free reporter (`lib/sentryLite.ts`, no `@sentry/nextjs`): no browser
  errors, no source maps, no headers / query / body; `release` = `VERCEL_GIT_COMMIT_SHA`.

## Deploy & CI

- **Frontend**: Vercel, deployed by `.github/workflows/vercel-deploy.yml` (preview per PR that
  touches `frontend/**`, production on such a merge to `main`); Git integration off via
  `frontend/vercel.json`; needs the repo secret `VERCEL_TOKEN`.
- **Backend Lambdas**: deployed via GitHub Actions assuming the OIDC role
  `aa-tripplanner-dev-app-deploy` (an Infra-owned IAM role). The role's trust is scoped to this
  repo through the org OIDC subject
  `repo:AdventureAsia365-Ecosys*/AA-TripPlanner-Web*:*` — see AA-CIS-Infra's `tripplanner.tf`.
- Because the org has "include repo ID in the OIDC subject" enabled, the trust uses the
  `AdventureAsia365-Ecosys*/...` wildcard form; a plain `repo:Ecosys/...` subject fails
  AccessDenied. This is the same OIDC fact that AA-588 corrected in Infra.
- **Deploy after CI + smoke (AA-879)**: `deploy-lambdas.yml` calls the whole `ci.yml` (reusable via
  `workflow_call`) as its `ci` job; `deploy` has `needs: ci`. After the update the `verify` job runs the read-only
  smoke rows through the production BFF (`/api/browse?resource=countries` with a cache-buster → 200 + countries;
  `/api/trip?resource=coverage&trip_id=<all-zero UUID>` → the Lambda's 404 `{"error": "not found"}` passed
  through (AA-909); `/api/trip?trip_id=<all-zero UUID>` → the BFF's 200 empty trip (AA-837)), so a broken
  Lambda turns the run red.
- **Post-deploy verify (AA-993 V4, root ADR 0016 D3)**: `.github/workflows/post-deploy-verify.yml` (app `web` |
  `lambdas`; `workflow_call` from the deploy workflows, `workflow_dispatch` with `expected_sha` from `main`), script
  `.github/scripts/post_deploy_verify.sh`, rows `.github/verify/{web,lambdas}.rows`. Web, after every production
  deploy: `GET https://aa-tripplanner.vercel.app/api/health` → 200 with `git_sha` = the deployed commit
  (`steps.plan.outputs.sha`) and `version` = its manifest. Lambdas: each function `Active`, `LastUpdateStatus`
  `Successful`, `CodeSha256` = the uploaded zip, whose S3 metadata `git-sha` / `app-version` = the deployed commit /
  version (written by the upload step), plus the smoke rows above. Red/green only, table in the job summary; a
  `result` job per deploy workflow is red when the deploy or verify did not succeed (Vercel: production path only;
  previews unchanged). A PR that adds a route or Lambda worth guarding adds a row.
- **CI checks**: required "Backend (pytest)", "Frontend (lint + build)", "Migrations (lint, PG 15
  replay)"; plus "Security Audit" (AA-879: pip-audit on `backend/requirements.txt`, failing). Secret scan
  (AA-981 follow-up): `secret-scan.yml`, check "gitleaks" on every PR (lines the PR adds, rules and baseline from
  the base branch) and "gitleaks full history" (`secret-scan-history.yml`) nightly / on push to `main` (branches, tags, pull
  request heads);
  rules in `.gitleaks.toml` (same as the root repo), reviewed findings pinned in `.gitleaksignore`; a PR that
  changes either file, either secret scan workflow, `.github/CODEOWNERS` or any `.gitattributes`, or adds an inline
  `gitleaks:allow`, needs the label
  `secret-scan-baseline-reviewed` after the last push (any later push, reopen or label change removes it). Merge
  commits (remerge diff), renames and type changes are scanned too, every file as text. A red result means the
  value is public: rotate it, even if it was force-pushed away. False positives: prefer a narrow allowlist in `.gitleaks.toml`; a
  commit fingerprint does not survive the squash merge (header of `.gitleaksignore`).
  The PR check runs on `pull_request_target`: GitHub always runs the base branch's copy of the workflow and the PR
  head is only read as data, so a PR cannot change the check that judges it. It also scans a squash preview of the PR
  (what the squash merge writes), and it fails when another workflow has a job named `gitleaks`. `.gitleaks.toml`
  carries a copy of the gitleaks v8.30.1 default rules and no path allowlist, so every file is scanned (`.svg`,
  `go.sum`, `node_modules/`, images, lock files); UTF-16 files are transcoded to UTF-8 and scanned too.
- **Security headers on previews (AA-879)**: `vercel-deploy.yml` step "Check the preview security headers"
  (`.github/scripts/check_security_headers.sh`, GET `/` through `vercel curl`). It needs the aa-tripplanner
  project's "Protection Bypass for Automation" in Vercel (no GitHub secret). Until that exists the step runs
  with `ALLOW_PROTECTED=1`: a Vercel-protection answer (401/403, SSO redirect, no bypass token) is a warning,
  while a page the app answers without a header still fails (tests: `.github/scripts/test_check_security_headers.py`,
  run in "Backend (pytest)"). Production keeps its own
  header check on `https://aa-tripplanner.vercel.app/` (AA-876).
- **Releases (AA-891)**: release-please in manifest mode (`release-please-config.json`,
  `.release-please-manifest.json`, `CHANGELOG.md`, tags `vX.Y.Z`, `bump-minor-pre-major`), run by
  `.github/workflows/release-please.yml` on push to `main` in the environment `release` with an `aa-ecosys-ci-bot`
  App token (so the release PR gets its checks). The manifest is the only version file (the frontend `package.json`
  has none). A release commit touches only root files, so it deploys nothing; a build reports the manifest version
  of its commit: Lambdas `version` next to `git_sha` on log lines and as a Sentry tag (`backend/VERSION`, written
  by `package_lambdas.sh`); web app `GET /api/health` → `{"status","service","version","git_sha"}` (`APP_VERSION` /
  `GIT_SHA` set per deployment by `vercel-deploy.yml`).

## Contracts with other apps

| Other side | This repo reads | This repo writes | Granted by |
|---|---|---|---|
| AA-CIS-App (catalog, AA-812) | `catalog.v_tour`, `v_tour_day`, `v_tour_place`, `v_tour_photo`, `v_destination`, `v_destination_photo` (contract `catalog.tour.v1`); photo URLs = `CIS_PUBLIC_API_BASE` + path | — | CIS migration 225 |
| AA-CIS-App (tour liveness, AA-986) | `catalog.v_tour_retired` (tombstone: `source_system`, `source_id`, `state`, `reason`; contract `catalog.tour.v1.3`) for the saved-trip reasons and the tour page 410 | — | CIS migration 274 (until applied every read degrades to the pre-L6 answer) |
| AA-CIS-App (places / tour graph, AA-712) | `catalog.v_tour_component`, `v_tour_stop`, `v_tour_leg`, `v_tour_junction`, `v_graph_node`, `v_destination` (+ `located_by`, `precision`; 256: shape kind / status / length / bbox / `shape_lo`), `v_component_embedding` (HNSW top-k, AA-847), `v_destination_shape` (line geometry by id, slice 6d), `v_destination_redirect` (merged destination id → keeper: `/browse/destinations/{id}` answers 301, AA-847 Problem 2), `v_component_redirect` (dropped component id → the component of the same place, followed by every assembly reader, AA-990) — contract `catalog.graph.v1.2` / `catalog.tour.v1.2` | — | CIS migrations 228, 253 (`v_component_embedding`), 256 (`v_destination_shape`): SELECT to `tripplanner` only; 257 (`v_destination_redirect`); 285 (`v_component_redirect`, `tripplanner` only) |
| AA-CIS-App (Model Gateway) | `shared.llm_role_config`, `shared.llm_model_catalog` | `shared.llm_call_log` (`quality_signal.app = "tripplanner"`) | CIS 173 |
| AA-Booking (My Payments, AA-682 S5) | HTTP `GET /booking/customer/v1/payments` with the traveller's own EXTERNAL access token (contract `booking.customer.v1`, from the BFF `/api/me/payments`) | — | Booking accepts the `tripplanner` client on its customer routes (`app.auth.customer`) |
| AA-Booking | — | `tripplanner.outbox` row `inquiry.submitted` v1 … v1.4 (customer + consent, days, leg chain, custom days), written in the same transaction as the `sent` event (AA-676, migration tp-008). AA-Booking reads `tripplanner.v_inquiry_outbox` and keeps its own inbox by `idempotency_key`; it never writes here. Contract: `AA-Ecosys/docs/contracts/inquiry.submitted.v1.4.json` (payload `version` 1.4 since AA-908: `customer.identity` = `{iss, sub}` of a signed-in traveller, else null, and `verified` true only with it; AA-Booking accepts 1.4 since bk-024 / Booking #90 and links the identity to one customer; 1.3 since AA-986 L6: every leg and day adds `available`, `reason`, `reason_detail`, and stops whose tour AA no longer serves follow the served ones with `available: false`; 1.2 since AA-830: each leg also carries the catalog tour key `source_system` + `source_id`, resolved against `catalog.v_tour` at send time — or, for a stop that left, from the tombstone — both null when unresolvable; outbox column `schema_version` = major 1) | ecosystem ADR 0001 decision 6 (outbox); migration tp-009 grants role `booking` USAGE on schema `tripplanner` + SELECT on the view only (AA-681) |
| AA-CIS-App (admin overview, AA-858) | — | — (it **reads** `tripplanner.v_ops_traffic_daily`: per UTC day `planner_session_started`, `trip_started`, `trip_edited`, `inquiry_sent`, aggregates only, last 400 days; contract `ops.overview.v1`, metric names append-only) | migration tp-015 grants role `cis_app` (the CIS runtime from X1 S18; until then the CIS runtime `aa_cis_admin` reads it through its `tp_owner` membership) USAGE on schema `tripplanner` + SELECT on the view only |
| AA-CIS-App (admin data coverage, AA-992) | — | — (it **reads** `tripplanner.v_ops_data_totals` (metric, n: sessions, trip drafts / sent, trips with events, pin events, id-map rows, customers, traveller accounts, outbox rows, narration cache rows; totals, aggregates only), `tripplanner.v_ops_pin_components` (pinned catalog component id + number of trips; no trip / session id, no payload text) and `tripplanner.v_ops_component_id_map` (id-map edges, catalog ids only) to count pins whose component is gone, walking map + CIS redirect like `component_ids.id_map`; contract `ops.data_coverage.v1`, metric names and columns append-only) | migration tp-020 grants role `cis_app` SELECT on the three views only (USAGE on the schema since tp-015) |
| AA-CIS-App (TP-03 inquiry handoff check, AA-1003) | — | — (it **reads** `tripplanner.v_ops_inquiry_keys` (`idempotency_key`, `created_at` of each `inquiry.submitted` outbox row of the last 30 days; the hand-off key `<trip id>:<hand-off id>`, no payload, no contact) and matches it with `booking.v_ops_inquiry_inbox` (bk-035) to name each inquiry not received and its age; contract `ops.overview.v1` key view, columns append-only) | migration tp-022 (contract file: manual DB Migrate apply) grants role `cis_app` SELECT on the view only |
| AA-CIS-Infra | — | — | owns the Lambdas, API Gateway, OIDC deploy role, secrets and the DB Migrate workflow that applies `migrations/` |

Schema owners and version keys: `AA-Ecosys/docs/database/schema-ownership.md`.

## Boundaries — what this repo does NOT do

- It does not run the content pipeline (Atom/Segment/Route/Slate/Piece/publish) — that is
  AA-CIS-App's domain entirely.
- It does not own the `shared` schema (CIS does). It created `shared.destinations` (migrations
  002 / 005), but since ADR 0002 / CIS migration 201 the table is CIS-owned, and since AA-712 X4 (CIS 270) this
  repo cannot read it either (it reads `catalog.v_destination`); the only `shared` tables it touches are the
  Model Gateway's (`llm_call_log` written, config read).
- It does not provision its own AWS resources or OIDC roles — those live in AA-CIS-Infra.
- **Agents must not merge to the production branch directly.** Ship via PR and let the human
  merge (matches this repo's stated policy and the ecosystem program rules). This repo has no
  branch protection like AA-CIS-App; a squash merge works as soon as CI is green.

## Known gaps (deferred)

CloudFront in front of
browse; Mapbox token URL restriction; more countries in the gateway table.

## Related documents

- `README.md` (this repo) — setup/run instructions.
- `docs/ecosystem-architecture.md` (root docs repo) — the ecosystem-wide account map, Bedrock
  routing, OIDC design, and how TripPlanner sits next to AA-CIS-App / AA-CIS-Infra.
- `AA-CIS-Infra/tripplanner.tf` — the OIDC deploy role and any TripPlanner-specific AWS resources.
