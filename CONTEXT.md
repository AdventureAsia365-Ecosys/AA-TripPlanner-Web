---
id: ARCH-06-tripplanner
title: "AA-TripPlanner-Web — Repo Context"
description: Stack, boundaries, ownership, domain terms and working rules of AA-TripPlanner-Web (Next.js frontend and its three backend Lambdas); read it before designing or changing code in this repo.
type: explanation
status: active
owner: nghiep
audience: [engineer, agent]
applies_to: [aa-tripplanner, tripplanner-lambda]
last_reviewed: 2026-11-11
review_every_days: 90
related: [ARCH-01, ARCH-05, ARCH-08, docs/adr/README.md]
---

# AA-TripPlanner-Web — Repo Context

Map-based trip planner in the Adventure Asia (AA) ecosystem, offered B2B as a white-label product of partner agencies
(root ADR 0019). An end traveler explores destinations on an interactive map, assembles a personal multi-day trip,
and gets AI-assisted suggestions and narrative. This repo is a self-contained product: its own frontend, its own
serverless backend, and its own database schema — it does **not** share application code with
AA-CIS-App. What it shares with the rest of the ecosystem is a small set of AWS-level resources
(Bedrock access, OIDC deploy roles, the destinations reference data) provisioned by AA-CIS-Infra.

> Scope of this file: describe the whole repo accurately — what it is, how it is laid out, how it
> runs, and where its boundary with the rest of the AA ecosystem sits. Deep per-subsystem design
> notes belong in their own doc next to the code, not here.

## What it is (role in the AA ecosystem)

- **Product surface**: a B2B white-label planner (root ADR 0019 d10/d11). Adventure Asia sells to partner agencies
  only; each agency tenant offers the planner to its own travellers under its brand, and a trip's lead goes to that
  tenant's CRM (`lead.submitted.v1`), never to an Adventure Asia traveller record. The visitor is still a traveller
  planning a trip, not an AA admin, but the planner is the tenant's product, not AA's consumer storefront. Today the
  only tenant is the AA-hosted demo (`aa-tripplanner`, kind `demo`): "Send to an advisor" is a demo notice (AA-1034
  R0), and every trip row belongs to the demo tenant (see "Tenancy (R5, AA-1038)"). Since slice 2 a served agency
  tenant has its planner on `/{slug}/plan` of the same host (signed BFF tenant assertion); theme, the tenant's own
  tour text and the lead route come in later R5 slices.
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
Two HTTP Python 3.12 Lambda functions
  ├─ browse      → stateless, cacheable reads (destinations, suggestions); embeds search text
  └─ assembly    → stateful writes (trip mutations) + Bedrock (AI narrative/plan)
(+ jobs Lambda, no HTTP route: daily EventBridge tick → tripplanner.job worker, AA-861)
      │
      ▼
Postgres (the shared RDS in acc2)
  ├─ tripplanner.*        (owned here — customers, sessions, trip_events, trip_drafts, outbox,
  │                        narration_cache, component_id_map, job, ai_rate_counter, ai_spend_month (tp-029);
  │                        llm_spend_daily dropped by tp-024)
  ├─ catalog.v_llm_*      (CIS AI gateway contract views, CIS 323: route config + today's spend; CIS 347: app profile)
  ├─ shared.llm_call_log  (INSERT only: one ledger v2 row per attempt, app = 'tripplanner')
  └─ read-only: catalog.* views (CIS-owned: tours, places, tour graph, embeddings)
      │
      ▼
Amazon Bedrock  — Cohere Embed v4: acc2 first, then acc3 via the satellite role (AA-862);
                  Claude via the cross-account role on acc3 (the main chat account, root ADR 0009)
```

**B2B-only demo (AA-1034 R0, root ADR 0019 d8/d11, since 2026-11-03):** "Send to an advisor" is a demo. Adventure Asia
sells to partner agencies only and keeps no traveller record of its own, so the button shows a demo notice
(`frontend/lib/advisorDemo.ts`, build flag `NEXT_PUBLIC_ADVISOR_HANDOFF`, demo unless `live`) and
`POST /trip/{id}/send-to-advisor` answers 410 `{"error": "b2b_only"}` before any SQL (`backend/config.py`
`ADVISOR_HANDOFF_DEMO`, a code default, on unless `ADVISOR_HANDOFF_DEMO=false`): no registration, no `sent` event, no
`inquiry.submitted` outbox row, so no Booking case. The hand-off described below is frozen until leads go to the
tenant's CRM (`lead.submitted.v1`).

The journey ends with a **draft trip + customer** sent to an advisor ("Send to advisor" +
sign-up), not a completed booking — booking belongs to AA-Booking (live on Dev since 2026-10-14), which pulls the
`inquiry.submitted` outbox row (see "Contracts with other apps").
TripPlanner sends no e-mail: the staff "new inquiry" e-mail is sent by AA-Booking when it processes the
`inquiry.submitted` outbox row (job `bk_notify_email`, AA-811); `backend/assembly/notify.py` only logs the hand-off.

### Frontend

- **Next.js 16.4** (App Router, Turbopack build) on **React 19.2**, served by **ECS behind CloudFront** (AA-1038 R5;
  the Vercel project was retired after the S272 cut-over) (AA-904 15.5,
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
- Deploy is CI-driven: `.github/workflows/deploy-web-dev.yml` (below) is the only deploy of the frontend. The Vercel
  deploy (`vercel-deploy.yml`, `frontend/vercel.json`, the repo secret `VERCEL_TOKEN`) was retired with the Vercel
  project (AA-1038, root runbook `docs/ops/runbooks/tripplanner-web-ecs-cutover.md` section 4).
- **ECS hosting (AA-1038 R5, Dev; root ADR 0019 d23, S265 tech-lead decision).** The frontend ships as a
  Next.js standalone image (`frontend/Dockerfile`, `NEXT_OUTPUT=standalone`, non-root uid 10001, ARM64) to the ECS
  service `aa-tripplanner-dev-web` (one Fargate Spot task, AA-CIS-Infra `modules/environment/tripplanner_web.tf`)
  behind a CloudFront distribution (`accounts/aa365/tripplanner_web_edge.tf`) and three TripPlanner rules in the ALB
  web ACL (`modules/waf`, scoped to this host: a rate rule per viewer IP, a stricter one on the protected paths (the
  two model calls and "Send to advisor"), a Challenge on the protected paths and on a document load of a planner
  page; no Bot Control and no web ACL of its
  own, S272 cost decision). The planner gets its WAF token from that Challenge on its own document load: every
  in-site way into it is a full page load (`ui/SiteLink.tsx` renders a plain `<a>` for a planner href, `ButtonLink`
  goes through it, the home search uses `window.location.assign`), and a planner reached client-side anyway reloads
  once into a document (`lib/plannerNav.ts`). There is no JavaScript SDK (AWS WAF gives `challenge.js` only to a web
  ACL with a paid intelligent-threat group). A model call still challenged (`lib/wafChallenge.ts`: `202` +
  `x-amzn-waf-action: challenge`, e.g. after the 4 h token) triggers one planner reload per 5 minutes, then the
  `botCheck` notice (never an empty answer), and none while a lead form holds typed input (`holdWafReload` in
  `components/LeadForm.tsx`: the `botCheck` notice instead); a challenged send never reloads (the lead form would be
  lost) and is answered as the bot-check refusal ("reload the page"). `.github/workflows/deploy-web-dev.yml` builds on an arm64 runner, pushes the immutable git-SHA tag,
  copies the family's latest task definition with only the image swapped and verifies through CloudFront
  (`/api/health` git_sha, pages with real catalog content, headers, `202` + `x-amzn-waf-action: challenge` without a
  token on search, narrate, send and the document load of `/plan` and `/aa-tripplanner/plan` (a 200 there means the
  rule is broken), a tour page loaded as a document 200, the older spellings 404); then job `release` (post-deploy-verify.yml, app web: health git_sha and the
  release-please version on the public host) and job `visual` (report-only). Its PR job `preview` checks the image
  (the standalone server started in the runner); there is no per-PR deployment. Build-time values: `NEXT_PUBLIC_MAPBOX_TOKEN` (repo secret
  `TP_MAPBOX_PUBLIC_TOKEN`) and `NEXT_PUBLIC_SITE_URL`; every server value is task
  env or a Secrets Manager secret, so the image cannot read the catalog at build: the home, `/destinations` and
  `sitemap.xml` render on request there (`lib/renderMode.ts`), their catalog reads still cached by the tagged fetch
  cache, resolved photo redirects kept in process for the catalog TTL (`lib/photoResolve.ts`).
  The web ACL's rules are the whole bot check (Vercel BotID left with Vercel: no `botid` / `@vercel/functions`
  package, `lib/botCheck.test.ts` guards it), and the task env `TP_EDGE_GATE=on` makes
  the BFF answer search, narration and "Send to advisor" only on `/api/browse/search`, `/api/trip/narrate` and
  `/api/trip/send` (the paths the web ACL
  sees after URL-decoding and normalizing, matched with `CONTAINS` so a dot-segment spelling, a leading
  `/%2e%2e/` included, is caught at the edge too; the older spellings get
  404 `use_protected_path`, `lib/edgeGate.ts`). `TP_CLIENT_IP_SOURCE=cloudfront` takes the
  traveller's address from `CloudFront-Viewer-Address` (`lib/clientIp.ts`): behind CloudFront the leftmost
  `x-forwarded-for` entry is the viewer's own words. CloudFront caches only `/_next/static/*` and `/photo-cdn/*` (the latter keyed on `?v=`); pages
  and `/api/*` go to the task (the catalog purge drops the in-process ISR cache; there is no CDN entry to purge).
  `tripplanner.lumiguides.it.com` is a CNAME to the distribution (S272 cut-over; the DNS switch and its Vercel
  rollback target were removed at the Vercel retirement). Rollback = redeploy the previous image (Deploy Web Dev on
  the reverted commit). Runbook: root `docs/ops/runbooks/tripplanner-web-ecs-cutover.md`.
- **Security headers (AA-876)** on every response (`frontend/next.config.mjs`, same list as the CIS admin and
  portal): HSTS, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, a minimal
  CSP (`frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'` — no `script-src` yet),
  `Permissions-Policy` (camera, microphone, geolocation off), and no `x-powered-by`. `frame-ancestors 'none'`
  blocks any partner iframe of TripPlanner until an allow-list is designed (KAN-20 B2B). A deploy fails red if the
  public host (through CloudFront) or the PR's preview image lacks any of them (`.github/scripts/check_security_headers.sh`
  in `deploy-web-dev.yml`; TripPlanner has no UI smoke workflow, so this is its header smoke).
- **Visual run (AA-994 S5b).** After every verified deploy, `deploy-web-dev.yml` job `visual` calls
  `.github/workflows/visual.yml` (also dispatchable with any `base_url`): Playwright (`e2e/visual/`, its own pinned
  package) opens every page of `e2e/visual/pages.ts` at 1440 x 900 and 390 x 844 in light and dark, checks no page-level
  horizontal overflow, no console / page error, no failed same-origin request, and uploads full-page PNGs + `report.json`
  (14 days), with one table in the job summary. It captures the public host `https://tripplanner.lumiguides.it.com`
  and reads no secret (no protected preview any more). Report-only: the deploy verdict
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
  **R7 planner UX (AA-1041 slice 1, `docs/implementation-notes/AA-1041.md`):** the trip panel's days
  (`components/DayList.tsx`) show a photo per day and per stop (the place's CIS cover photo from `/browse/stops`, the
  stop's icon on the brand tint when there is none) and an icon per stop and leg (`lib/placeIcon.ts`, ~30 classes);
  trip stops on the map are DOM markers (buttons named "Day N: …", k-* tokens) synced with the list through `useTrip`
  (`activeStopId`: hover / focus highlights both sides, a click selects: the map eases to it or the list scrolls to
  it); days and stops reorder by a pointer drag of their handle (mouse and touch) or the arrow keys / Home / End on it,
  saved as the one `reorder` op the planner always used, with one step of undo (`lib/itineraryOrder.ts`); the panel
  and the place popup use the kit loading, empty and error states (a failed trip read offers "Try again").
- `/plan/shared#v=1&s=<component ids>` and `/{slug}/plan/shared#…` (AA-1041): the read-only share / print view of a
  trip. The fragment carries component ids only (never text, no trip id or guest token; `lib/shareTrip.ts`), the
  page reads the stops from `/browse/stops`, prints with the browser ("Print or save as PDF") and builds an `.ics`
  from a start date the traveller picks. noindex; the tenant page resolves and brands like `/{slug}/plan`.
- `/tours/[slug]` and `/destinations/[country]`: Server Components with ISR (30 min, the browse
  CDN TTL), JSON-LD (`TouristTrip`, `TouristDestination`), `sitemap.xml`, `robots.txt`.
- **Preview until go-live (TL-24, S262):** every deployment, the Dev host included, is a preview unless its runtime
  env sets `SITE_INDEXING=on` (`frontend/lib/siteMode.ts`): root-layout robots `noindex, nofollow`, an `X-Robots-Tag:
  noindex, nofollow` header on every response (`next.config.mjs`), `robots.txt` without the sitemap line (crawl rules
  kept so crawlers read the noindex), and a "Preview site" line in the footer. No other variable decides it. Go-live
  sets the flag on the live task only.
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
  (`img.lumiguides.it.com`, AA-844); CloudFront caches the versioned file (behaviour `/photo-cdn/*`, `?v=` in the
  cache key, AA-CIS-Infra `tripplanner_web_edge.tf`) and the browser reuses the HTML connection (no preconnect needed). The hero also asks the catalog for
  `?size=medium` (960 w) and adds it to the srcset only when CIS redirects it to a file (CIS answers 422 today).
  The first-row tiles of `/destinations` load eagerly (the first is the LCP); the others stay lazy.
  Since S258 the static map (destination and tour pages) and the destination page's place thumbnails render through
  `components/site/NearViewImage.tsx` (no `src` until 300 px below the viewport; `<noscript>` copy), because Chrome's
  `loading="lazy"` fetched them during load on a phone; and a tour card that shows the hero's own file reuses the
  hero's srcset + `sizes` (`sameFileAsHero`), so the browser takes the file it already has.
- **On-demand purge on a CIS tour lifecycle event (AA-987).** CIS posts one HMAC-signed notice per event of
  `catalog.v_tour_lifecycle_outbox` (CIS job `tripplanner_purge`, retried with backoff, swept every 5 min) to
  `POST /api/revalidate/catalog` (`app/api/revalidate/catalog/route.ts`, logic in `lib/catalogPurge.ts`). Header
  `X-AA-Signature: t=<unix s>,v1=<hex hmac-sha256 of "t.body">`, secret = task env `CATALOG_REVALIDATE_SECRET`
  (same value as CIS secret `aa-cis/<env>/tripplanner-revalidate-hmac`), 5-minute timestamp window, body contract
  `catalog.tour.lifecycle.v1` (`events[]`: `event_id`, `source_id`, `event`, …). It drops every cache that can show the
  old state: `revalidateTag("tp-catalog", { expire: 0 })` (every catalog fetch of `lib/catalog.ts` carries that tag),
  `revalidatePath` of `/`, `/tours/[slug]`, `/destinations/[country]`. No CDN step: CloudFront caches no page and no
  `/api/*` answer (the Vercel CDN tag purge left with the Vercel project, AA-1038). A cacheable `/api/browse` answer
  is kept by the browser for 60 s at most. Idempotent (a replay purges again); 401 bad / stale signature, 400 bad body, 503 secret unset, 502 purge
  failed (CIS retries). One `{"evt":"catalog_purge",…}` log line per call (event ids and kinds, no source ids).
- **Tours AA no longer serves (AA-986 L6, root ADR 0015 decision 5 / Q5).** Lists, countries, the sitemap and every
  planner read already hide them (they read `catalog.v_tour` / `v_tour_component`, live tours only). A tour page of a
  **retired** key answers **410 Gone** (a plain noindex page): Next has no "gone" for a page, so `frontend/proxy.ts`
  (matcher `/tours/:slug`, no cookie ever set there) checks the slug's source-id suffix against an in-memory list of
  retired suffixes (`lib/goneTours.ts`, `GET /browse/catalog/retired`, refreshed every minute per instance in the
  background; AA-987: a request waits for the browse call, at most 1.5 s, only when the list is over 2 minutes old or
  was never loaded, so a retired tour answers 410 within 2 minutes on every instance; a failed refresh keeps the last
  list and makes nobody wait until its retry is due). The 410 is never kept by the CDN (CloudFront caches no
  page; browser 60 s), so a restored tour comes back as fast. The browse Lambda reads the CIS tombstone `catalog.v_tour_retired` (CIS 274) and
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
  `/browse/stops?ids=<component uuid>,…` (≤ 50, AA-1041) serves the planner's stops by id with their place photo
  (`catalog.v_tour_component` joined to `catalog.v_destination`, `cover_photo_path` as the CIS photo URL), no point,
  CDN-cacheable: the trip panel's photos and icons and the share view read it.
- **assembly Lambda** — **stateful**, owns all trip mutations, and is the only side that calls
  **Claude**. Building/AI-narrating a trip is a write that also incurs model cost, so it is
  deliberately isolated from the cacheable read path. Keep anything that writes trip state or
  calls a text model here.
- Runtime: **Python 3.12**. The two functions are separate deploy units so the cacheable read
  path and the expensive stateful path scale and cache independently.
- **Edge shared secret** (`backend/shared/auth.py`, AA-878): both Lambdas check the
  `X-TripPlanner-Key` header the BFF sends (task env `TRIPPLANNER_API_KEY`, same value). On Lambda
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
  mints it on `/plan`, `/api/trip` and `/api/trip/narrate` only (the home, destination and tour pages are CDN-cached, so a
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
    = hex HMAC-SHA256(task env `TP_COGNITO_SIGNUP_PROOF_KEY`, `"<email>\n<bff_ts>"`); the Infra pre sign-up trigger
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
    purposes and salt; key = task env `TP_SESSION_SECRET`): `__Host-tp_auth` (access token, its expiry, email),
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
  - WAF: every Cognito call carries `x-aa-traveller-bff` = task env `TP_COGNITO_BFF_KEY` (AA-CIS-Infra secret
    `tripplanner/<env>/cognito-bff-key`) and `X-AA-Traveller-IP` = the first `x-forwarded-for` address, so the Cognito
    web ACL counts each traveller, not the shared egress address of the web server (Infra rule `BffRatePerTravellerIp`). The two
    go together: without a valid traveller address neither is sent, and the call is counted per egress IP.
  - Sign-in claims every unowned guest trip of this browser (§Traveller accounts); the privacy page tells travellers
    to sign out on a shared computer.
  - Without `TP_SESSION_SECRET` (32+ characters) sign-in answers 503 `sign_in_unavailable` and the header shows no
    link: the planner works as a guest. Per-PR previews can sign in only when the env values are set for Preview (new addresses also need `TP_COGNITO_SIGNUP_PROOF_KEY`).
  - Pages: `/sign-in` (two-step email -> code form, `?next=` same-site path, default `/trips`) and `/trips` (My
    Trips: status Draft / Sent to an advisor, stops, days, "Open" writes the ids to localStorage and opens `/plan`),
    both noindex. The header link (`components/AccountLink.tsx`, "Sign in" / "My trips") is fetched by the browser,
    because the kit pages are CDN-cached. The send form shows the signed-in email read-only.
- **My Payments on `/trips` (AA-682 S5) is retired (AA-1046, S271).** It read AA-Booking's customer payment requests
  (`booking.customer.v1`); root ADR 0019 made agencies the only customers, so the B2C half of AA-Booking (customer
  invoices and payments) is dropped and the BFF route `/api/me/payments`, `components/MyPayments.tsx` and
  `lib/myPayments.ts` are gone. `/trips` lists the trips only. `ui/Badge.tsx` keeps its `danger` tone.

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
  `tp_owner`, `test_tp016_pg.py` with tp-016, `test_tp017_pg.py` / `test_tp018_pg.py` / `test_tp019_pg.py` / `test_tp022_pg.py` with tp-017 / tp-018 / tp-019 / tp-022, `test_tp025_pg.py` with tp-025: tenant_id and the shadow RLS policies, checked as the logged-in runtime role; `test_tp028_pg.py` with tp-026 … tp-028: FORCE row-level security and the enforcing policies, as the runtime role, the owner and cis_app). `test_replay_twice_pg.py` (AA-849) applies every file twice: the second run must not error and
  must change nothing, and the registered `tp-NNN` keys must equal the files, so **every new file must be
  safe to run again** (a file whose objects a later file dropped — tp-003, tp-004, tp-013 after the tp-018 drop, tp-025 after tp-028 replaced its shadow policies — is
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
  the catalog views** (plus the gateway ledger INSERT): the `PLACES_SOURCE=legacy|cis` switch and every legacy
  reader of the frozen tripplanner place tables (`itinerary_components`, `tour_day`, `tour_stop`, `tour_graph_*`,
  `tour_leg`, `tour_junction`), `shared.destinations`, `gold_aa_internal.published_tours` (and its AA-tenant GUC)
  are gone, tp-018 drops the frozen tables and CIS 270 revokes the `tripplanner` grants on the CIS objects they
  used. The guard `backend/tests/test_extraction_ops.py` fails if a backend SQL string names any schema-qualified
  object outside `catalog.*`, the gateway ledger `shared.llm_call_log` and the live `tripplanner` tables (sessions,
  customers, trip_events, trip_drafts, outbox, narration_cache, component_id_map, traveller_accounts, job,
  ai_rate_counter, ai_spend_month), or writes place data. (AA-1027 E5: `shared.llm_role_config`, `shared.llm_model_catalog` and
  `tripplanner.llm_spend_daily` left the allowlist.)
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

#### Tenancy (R5, AA-1038)

Root ADR 0019 d10. Slice 1 (tp-025, the "shadow" phase) gave the trip state an owning tenant; the FORCE slice (tp-028,
S276) makes the database enforce it: a connection without a tenant context sees and writes no tenant row.

- **Tables.** `tripplanner.sessions`, `trip_drafts`, `trip_events` and `outbox` have `tenant_id uuid NOT NULL`
  (tp-025), a soft key to `shared.tenants.tenant_id` (no FK: `shared` is CIS's). Every row written before tp-025 was
  backfilled to the demo tenant `00000000-0000-0000-0000-000000000002` (slug `aa-tripplanner`, kind `demo`).
  Not tenant-scoped, on purpose: `narration_cache` and `ai_rate_counter` (hash keys, no tenant column),
  `ai_spend_month` (tp-029: the planning chat's month sums per scope `app` | `tenant:<uuid>`; the app sum is read
  across tenants, no traveller data), `customers`
  and `traveller_accounts` (frozen since R0, retired later), `job` (system queue) and `component_id_map` (global
  catalog ids).
- **Column default** (tp-028) `NULLIF(current_setting('app.tenant_id', true), '')::uuid`: an INSERT without
  `tenant_id` takes the bound tenant; with none bound it fails (the row fails the policy's WITH CHECK, and the column
  is NOT NULL). The demo fallback of tp-025 is gone. The code's INSERTs name no `tenant_id`.
- **Policies (tp-028).** Row security is ENABLED and FORCED on the four tables, with three permissive policies each:
  - `<table>_tenant` FOR ALL TO `tripplanner`: `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`
    for reads and writes. No context (unset or `''`) = no row; a row naming another tenant fails WITH CHECK; a
    context that is not a uuid fails the statement, it never widens access.
  - `<table>_system` FOR ALL TO `tripplanner`, `tp_owner`: every row while the GUC `app.tenant_scope` is `'system'`
    (nothing else spelled like it). Only the jobs Lambda opens it (below); a later tp- file that rewrites rows as the
    owner opens it with `set_config('app.tenant_scope', 'system', true)` first, else its UPDATE reaches no row.
  - `<table>_owner_read` FOR SELECT TO `tp_owner`: every row when the session binds no tenant, the bound tenant's rows
    when it does. FORCE puts the owner under row security, so this is what the owner-owned views read through:
    `v_ops_traffic_daily`, `v_ops_data_totals`, `v_ops_pin_components`, `v_ops_inquiry_keys` (granted to `cis_app`,
    which binds no TripPlanner tenant) keep every row. The runtime role holds SELECT on them too (the tp_owner default
    privileges); they carry counts and keys, no traveller data.
  - `v_inquiry_outbox` (retired inquiry view, no consumer since tp-027) is a `security_invoker` view since tp-028: the
    caller's own policies apply through it, so it is no way around `outbox_tenant`.
- **Binding (assembly Lambda).** `backend/shared/tenant.py`: `bound(conn, tenant_id)` runs
  `SELECT set_config('app.tenant_id', $1, false)` right after the pooled connection is acquired, before `route()`,
  and `RESET app.tenant_id` in a `finally`, also when `route()` raises (`handler.serve`, `handler.serve_m2m` and
  `local_server.py` the same way). Session level, not `SET LOCAL`, because the handler mostly runs in autocommit on
  one pooled connection; asyncpg's `RESET ALL` on release is a second safety. An unbind that fails terminates the
  connection (log line `tenant_unbind_failed`) instead of returning it to the pool. The bare path's tenant is
  `config.DEFAULT_TENANT_ID` (the demo constant; env override `TP_DEFAULT_TENANT_ID`, validated as a uuid at import,
  so a bad value fails the cold start and a non-uuid is never bound). Each request logs one line `tenant_context`
  `{tenant_id, source}` (no request values). `backend/tests/test_aa1038_force_rls.py` fails when a connection in
  `backend/assembly/` is acquired without `tenant.bound` in the same `async with`.
- **Jobs path (system scope).** The jobs Lambda (same login role `tripplanner`) binds no tenant. The two kinds that
  touch a tenant table, `tp_retention_outbox_redact` and `tp_lead_deliver`, open `tenant.system_scope(conn, op=KIND)`
  for their run (`SELECT set_config('app.tenant_scope', 'system', false)`, `RESET app.tenant_scope` in a `finally`; a
  reset that fails terminates the connection, log `tenant_scope_reset_failed`); each logs `tenant_context`
  `{tenant_id: null, source: "system", scope: "system", op}`. `test_aa1038_force_rls.py` fails when any module outside
  `backend/jobs/` opens the scope, when `app.tenant_scope` is spelled outside `shared/tenant.py`, or when a job module
  that names a tenant table acquires a connection without it. The queue (`tripplanner.job`) and the rate-counter
  purge read tables without row-level security. The browse Lambda reads no tenant table (guarded too).
- **Order of the FORCE slice.** The code (jobs system scope) works under tp-025 too, so it deploys first; tp-028 is
  applied after. Applied before that code is live, the two job kinds would see no outbox row (nothing redacted, no
  error) until it deploys.
- **Tests.** `backend/tests/test_aa1038_tenant.py` (bind order, unbind on error, invalid id runs no SQL, the system
  scope's set / reset / discard), `backend/tests/test_tenant_rls_pg.py` (on the tp-028 replay, as `tripplanner`: a
  demo trip is 200 under the demo tenant and 404 under a forged one, no context sees no row, the outbox redaction and
  the lead expiry reach every tenant through the system scope), `test_aa1038_tenant_site_pg.py` and
  `test_aa1038_leads_pg.py` (the slice 2 and 3 paths, also on the tp-028 replay), `migrations/tests/test_tp028_pg.py`
  (the policies as the runtime role, the owner and cis_app). The nightly DB Isolation Probe of AA-CIS-Infra checks
  the four tables through `tripplanner`'s own login (cell `tenant.tripplanner_isolation`, expected from tp-028).

#### The /{slug} path (R5 slice 2, AA-1038)

- **Which tenant.** `app/[tenant]/plan/page.tsx` (and `app/[tenant]/page.tsx`, a redirect to `/{slug}/plan`) resolves
  the slug on the server through the browse Lambda: `GET /browse/tenant/{slug}` (`backend/browse/tenant_site.py`)
  reads the CIS contract view `catalog.v_tenant_tripplanner_v1` (CIS 379, contract `tenant.entitlements.v1` 1.4,
  SELECT to `tripplanner`) and answers only a **served** tenant (in service and `tp.hosted` live): tenant id, slug,
  trading name, kind, launcher, the BO-8 badge flag, theme (kit token -> `#rrggbb`, filtered again here) and locale.
  Unknown, add-on-less, suspended and badly shaped slugs are the same 404 (no shared cache; the BFF keeps a per-
  instance cache of 60 s, 30 s for a miss, `lib/tenantSite.ts`). Until CIS 379 is applied the view is absent (42P01 /
  42501): every slug is a 404 (log `tenant_view_unreadable`), the bare paths are unaffected. Any other read error is a
  503 `no-store` (log `tenant_lookup_failed`), never cached by the BFF, and the `/{slug}` pages show a retryable error
  (`app/[tenant]/error.tsx`), not the 404 page. Static routes (`/plan`, `/tours`, `/destinations` …) win over the
  dynamic segment, so those names are **reserved** (`lib/tenantSlug.ts` `RESERVED_TENANT_SLUGS`: every top-level folder
  of `app/` and `public/`, pinned by a test) and never looked up; CIS slug creation should refuse them (follow-up).
- **The planner names its tenant, the BFF signs it.** `PlannerApp` sets the page's slug once
  (`lib/plannerTenant.ts`): every `/api/trip` call carries `x-tp-tenant-slug`, and the guest ids live under
  `aa-tripplanner:ids:<slug>` (the bare `/plan` keeps `aa-tripplanner:ids`), so one agency's trip is never sent to
  another's planner. The BFF (`lib/tenantProxy.ts`) resolves the slug and sends the assembly Lambda a fresh **tenant
  assertion** `x-tp-tenant: v1.<payload>.<sig>` (claims `exp`, `iat`, `slug`, `tid`, life 120 s), HMAC-SHA256 with a key
  derived from the edge key (`HMAC(edge key, "aa-tripplanner tenant assertion v1")`, `lib/tenantAssertion.ts` and
  `backend/shared/tenant_assertion.py`, pinned equal by one shared vector). No new secret: both sides already hold the
  edge key; both sides strip surrounding whitespace (space, tab, CR, LF) from the edge key before deriving. A slug that
  does not resolve is a 404 `unknown_tenant` from the BFF, a lookup that is down a 503 `tenant_lookup_unavailable`; no
  slug header = no assertion = the default (demo) tenant, exactly as in slice 1. The account calls do the same
  (`lib/accountProxy.ts`): the planner header's links keep the tenant (`/sign-in?next=/{slug}/plan`,
  `/trips?tenant={slug}`), the sign-in form sends `x-tp-tenant-slug` so `POST /me/claim` claims that tenant's guest
  trips (skipped, never claimed into the demo tenant, when the slug does not resolve), and My Trips sends it to
  `GET /me/trips` and opens a trip in `/{slug}/plan` under that tenant's ids key. Sign-out forgets every ids key
  (`aa-tripplanner:ids` and `aa-tripplanner:ids:<slug>`).
- **The Lambda trusts only the signature.** `assembly/handler.py` `tenant_assertion_answer` runs before any
  connection or SQL: a forged, altered, expired (or `exp - iat` over 300 s, or `iat` more than 30 s ahead) assertion is
  401 `tenant_assertion_invalid`, no bind; when the edge key cannot be read, 503 `tenant_assertion_unavailable`. Log
  line `tenant_assertion_refused {reason}` (never the value). A valid one binds its `tid` with source `assertion`
  (`tenant_context` line). The browse Lambda binds nothing (it reads catalog views only).
- **What a tenant sees of the catalogue.** AA master rows only, whatever is bound: the tour views read
  `catalog._live_tour`, whose WHERE keeps the AA master tenant (decided in the 379 view contract). The tenant's own
  text comes with `catalog.v_tenant_tour_v1` (later slice).
- **Isolation.** Under tenant A's assertion only A's sessions and trips are visible (tp-028 policy `<table>_tenant`):
  another tenant's trip is the same 404 as an unknown one and a mutation is 401 with no write
  (`backend/tests/test_aa1038_tenant_site_pg.py`, two synthetic agencies). The bare path binds the demo tenant, so it
  sees demo rows only; a connection with no tenant bound sees no row (FORCE slice, tp-028).
- **Not in this slice.** The tenant brand, `lead.submitted.v1` and the per-tenant AI caps came with slice 3
  (below); FORCE RLS (tp-028) and the ECS + CloudFront hosting move came after it; the launcher snippet is a later slice.

#### Leads, brand and the per-tenant AI cap (R5 slice 3, AA-1038)

Root ADR 0019 d11 (+ the S272 amendment); contract `lead.submitted.v1` (root `docs/contracts/lead.submitted.v1.openapi.yaml`,
vendored as JSON Schema in `backend/tests/contracts/lead.submitted.v1.json`); notes
`docs/implementation-notes/AA-1038-r5-slice3.md`.

- **Send to advisor on an agency planner** (`/{slug}/plan`, a verified tenant assertion, never the bare path or the AA
  demo tenant, which keep the demo notice / 410 `b2b_only`): `assembly/handler.py _send_lead` + `assembly/leads.py`.
  The form (`components/LeadForm.tsx`, `lib/leadForm.ts`) sends `lead` = name, e-mail, optional phone, party, optional
  start date / budget band / message, contact consent (required) and marketing consent. The Lambda validates it (422
  `consent_required` / `invalid_lead` + field codes), reads the tenant's route from `catalog.v_tenant_tripplanner_v1`
  (CIS 390: `lead_channel` crm / webhook / email / none) and refuses BEFORE storing when it could not deliver (409
  `lead_route_missing`, 503 `lead_route_unavailable` before CIS 390, 503 `lead_channel_unavailable` while the CRM
  pull is not switched on (`TP_LEAD_CRM_PULL_ENABLED`, default off) or the CRM has not listed THIS tenant within
  `TP_LEAD_CRM_PULL_FRESH_S` (600 s; the list call is the tenant's beacon, kept in `ai_rate_counter` scope `crm_pull`),
  without a webhook key or an e-mail sender, or when the webhook URL is not https or its host does not resolve (2 s
  bound, `TP_LEAD_WEBHOOK_RESOLVE_TIMEOUT_S`) to public addresses only: `leads.webhook_target_problem`). Also before
  storing: the web ACL's rate rule and Challenge on `POST /api/trip/send` (AA-1038; a challenged send never reaches
  the app and the form shows `bot_check_failed`'s "reload the page" line), then per guest session and per traveller IP limits (`ratelimit` scopes `send` / `send_ip`, 5 and
  20 an hour, 429 `rate_limited`) and a per-tenant cap of leads in 24 hours (`TP_LEAD_TENANT_DAILY_CAP`, 200, 429
  `lead_cap_reached`; counted in the send transaction after `pg_advisory_xact_lock(hashtext('tp_lead_cap:' ||
  tenant_id))`, so concurrent sends of one tenant never pass it). A phone needs `+` and the country code (contract
  `Traveller.phone`, E.164). A name needs at least one letter or digit after NFKD (`leads.has_name_token`: the CRM
  files the traveller by the name's tokens; `-`, `...` or an emoji only is 422 `invalid_lead` on `name`). Name, e-mail
  and phone follow one rule in the form, the Lambda and the CRM (control characters out, NFC, trimmed, the e-mail
  lower-cased before it is checked, code-point length, the name token, the patterns), pinned by the shared vectors `backend/tests/contracts/lead.submitted.v1.traveller-rules.json`. Then, in one transaction with the trip's `sent` event (a double click answers the same
  hand-off), one `tripplanner.outbox` row (event `lead.submitted`, `delivery_channel`, tp-026). The guest stays a guest:
  no `customers` row. Never in a lead: the guest session or token, an access token, an IP, a user agent or a price.
- **Delivery.** crm: the CRM pulls `GET /trip/m2m/v1/tenants/{id}/leads` and acknowledges `POST
  .../leads/{lead_id}/ack` (`serve_m2m`): an aa-m2m bearer token of the CRM client with scope
  `tripplanner.leads/deliver` (`shared/m2m_auth.py`, Dev issuer and client are code constants with an env override)
  replaces the edge key on these routes only, checked before any SQL; the connection binds the path's tenant (source
  `m2m`), and only a tenant whose route is crm now is served (404 `tenant_not_on_crm`: the CRM client is a platform
  credential). An ack never changes a final state (delivered, rejected; an expired lead keeps its expiry unless the
  CRM stored it). Only a public address is ever posted to (`leads.public_address_check`, checked again at each
  POST). webhook: one signed POST right after the commit (`X-AA-Signature: t=<unix s>,v1=<hex HMAC-SHA256(key,
  "<t>." + body)>`, key per tenant = HMAC-SHA256(master, "aa-tripplanner lead webhook v1:" + tenant id), master from
  `TP_LEAD_WEBHOOK_KEY` or Secrets Manager `tripplanner/<env>/lead-webhook-key`). email: one SES e-mail from
  `TP_LEAD_EMAIL_FROM`, Reply-To the traveller. Delivered = payload redacted at once (ids only).
- **Hold window.** Jobs Lambda kind `tp_lead_deliver` (daily, 3 attempts): FIRST, batch after batch (500 rows per
  transaction, 90 s budget, a failing batch tried again at once), every pending lead older than
  `TP_LEAD_HOLD_DAYS` (clamped 3..7) minus two tick intervals and a 1 h margin (`leads.hold_cutoff`, 4 d 23 h at 7
  days) is redacted with `delivery_error = 'expired'`, so under the real schedule a lead is redacted at the latest 7
  days after it was written even when the tick that picks it fails (the re-queued job runs at the next daily tick;
  two failed ticks in a row are the residual risk, each one loud); then the leads the CRM rejected since the last tick
  are counted; then one retry per pending webhook / e-mail lead within a 120 s budget, with a breaker per tenant and
  per channel; a webhook URL on a non-public address is a route defect (`route_defect`, not retried). An expiry, a
  rejected lead, a route defect or rows left after the budget mark the job `attention` and the daily tick fails
  loudly. It
  skips before tp-026. The jobs role needs the webhook key and SES send in the same Infra apply as the assembly role
  (a deploy prerequisite of those channels).
- **Brand.** `app/[tenant]/plan/page.tsx` renders the tenant theme through the kit core brand-token layer
  (`ui/core/brandTheme.ts`, synced from CIS, root ADR 0016 amendment): one `<style>` over the accent family of `--aa-*`,
  contrast-checked; the trip panel names the agency and shows "Powered by Adventure Asia" only when `tp.branding` is on
  (BO-8, off by default).
- **Per-tenant AI cap** (`shared/tenant_budget.py`): `llm_gateway.budget_status` checks, after the app caps, the
  tenant's month spend against its resolved `ai.budget_usd_monthly` from `catalog.v_tenant_ai_budget_v1` (CIS 390,
  cached 30 s per tenant, this container's calls charged on top). A reached budget is a cap (`ai_budget_exhausted`,
  keyword search); no budget key, no row, the view missing or a read error pause AI for that tenant only (fail closed).
  The bare path / demo tenant has no tenant budget. Every ledger row now carries the request's tenant
  (`tenant.current()`), so CIS Tenant 360 › Cost and the tenant's budget count planner AI. The browse Lambda checks the
  same assertion on search (`/api/browse/search` sends `x-tp-tenant-slug`; the BFF signs it as on `/api/trip`).

### AI (Bedrock)

- **Every model call goes through the ecosystem AI gateway** (AA-685, root ADR 0009; as built: root
  `docs/architecture/ai-gateway.md`), in `backend/shared/llm_gateway.py`. The model, fallback order and price of each
  stage come from AA-CIS-App's control plane, read through the CIS 323 contract view (below). Every call attempt writes
  one ledger v2 row in `shared.llm_call_log` (failed attempts too; since AA-1027 E3 the cost and `pricing_mode` come
  from the shared core `aa_llm.pricing.quote`: catalog prices, the deployment's price overrides on top, a model with
  no catalog price charged the core's flagged `estimate` rates, never NULL or 0) with `app = 'tripplanner'`, so it shows on the CIS Cost Operations page, tab "AI & data spend". If the
  route cannot be read, AI pauses (`AiPaused("route_unreadable")`, fail closed since AA-642 S0) and the caller
  degrades; the built-in routes are never called. `backend/tests/test_no_gateway_bypass.py` (AA-642 L6) fails CI on
  any Bedrock / vendor SDK call, vendor SDK import or vendor endpoint string outside `llm_gateway.py` /
  `bedrock_satellite.py` (backend and `tools/`), and on a vendor AI SDK import or endpoint in `frontend/`.
  **AA-642 S8 (root ADR 0009 decision 13):** the gateway uses the shared core `backend/aa_llm/`, a byte copy of
  AA-CIS-App `shared/aa_llm/` pinned by `backend/aa_llm.lock.json` (synced by CIS `.github/scripts/llm-core-sync.sh`
  / the Llm Core Sync bot; never edit it here; `tests/test_vendored_copies.py` checks it offline,
  `backend/scripts/check_vendored_copies.py` also against CIS). The route comes from the contract view
  `catalog.v_llm_gateway_config_v1` and today's spend from `catalog.v_llm_app_spend_today_v1` (CIS 323), the only
  config and spend sources since **AA-1027 E5**: there is no table fallback, so a view that is missing, not granted
  or failing pauses AI (`route_unreadable` / `spend_unreadable`) and the caller degrades; CIS 351 revokes the
  grandfathered SELECT of CIS 173 on the two tables and tp-024 drops the tp-010 counter. Each attempt
  writes the ledger v2 row (`app = 'tripplanner'`, `status`, `error_class`, `latency_ms`, `attempt_no`, connection,
  wire id, cache tokens, `pricing_mode`, and `effort` when the body carried one: always NULL today, see E4).
  **AA-1027 E4 (stage x model params):** CIS 350 appends `supports_temperature` (catalog) and `stage_params` (the
  `shared.stage_model_params` row of the stage and model, NULL = none) to the config view. `llm_gateway.request_params`
  applies them with CIS's precedence (call site, then the row, then the protocol default): the call sites always pass
  max_tokens, so the row's `max_tokens` / `min_output_tokens` never apply; `temperature` is sent when the catalog says
  the model takes one (both wire styles); `effort` and `reasoning_mode` never: every TripPlanner attempt is a Bedrock
  call (InvokeModel or Converse) and CIS sends effort only on the Anthropic Messages API, so the field comes back only
  after a probe shows Bedrock takes it for the catalog model (or behind a catalog capability flag). What is not applied is said once per container (`llm_stage_params_not_applied`).
  No row (Dev today) = byte-identical request bodies. Kill switch `LLM_STAGE_PARAMS=off`. Before 350 is applied the
  read narrows to the 341 columns (SQLSTATE 42703) and no params apply.
  **AA-642 S263 E2:** each row also carries `request_id` (Bedrock RequestId of the reply or of the provider's error),
  and each text-generation row `prompt_hash` (sha256 of the system prompt sent) and `prompt_version` (the stage's
  newest version in the prompt manifest `backend/shared/prompt_versions.json` while the deployed source still matches
  it; `backend/shared/prompt_registry.py`, same hash as the CIS AA-900 register). The CI test
  `tests/test_prompt_registry_gate.py` fails when a prompt (`_COMPOSE_SYSTEM`, `_RENARRATE_SYSTEM`, `_build_body` in
  `backend/assembly/agent.py`) changes without `python3 backend/scripts/prompt_registry_check.py --bump`; AA-CIS-App registers the manifest rows in
  `shared.prompt_version` (the `tripplanner` role cannot read that table).
  Stages:
  - `tp_compose`: assembly narration (Claude Sonnet 4.6 via the satellite role);
  - `tp_plan_chat` (AA-1041 slice 2): the planning chat, `POST /trip/{id}/chat` (see "Planning chat" below). Until
    AA-CIS-App seeds a route row for it, it runs on the `tp_compose` route's models, logged under its own stage
    (`llm_gateway.load_route(..., borrow_from="tp_compose")`: only a view that answers with no row borrows; an
    unreadable view still pauses AI). Prompt `_SYSTEM` / `_build_user` in `backend/assembly/plan_chat.py`;
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
  - `tp_extract` / `tp_component_embed` are gone (the former offline extraction pipeline, no caller since AA-712
    slice 6a; CIS migration 336 switches their routes off and the gateway's route table no longer names them,
    AA-1024 D8).

  An admin can change a stage's model in the CIS admin, AI providers › Routing, without
  a TripPlanner deploy; the Lambdas pick the change up within 60 s. Shadow config on a tp_* stage
  is not run here (a warning is printed): a consumer app does not pay for shadows.
- **AI guards (AA-805, migration tp-010)**: a daily hard stop on **customer-facing** model spend
  only — stages `tp_compose` + `tp_search_embed` + `tp_plan_chat` (`llm_gateway.ONLINE_STAGES`,
  `TP_DAILY_SPEND_CAP_USD`, 5 USD on Dev, set by AA-CIS-Infra; today's spend is read through
  `catalog.v_llm_app_spend_today_v1` (CIS 323), the ledger sum, with no fallback since AA-1027 E5; the role cannot
  read `shared.llm_call_log` itself; an unreadable spend, or a lost ledger row of an online stage in the last 60 s,
  fails closed). Any other
  stage would be logged but neither counted nor capped here, so a backfill never takes search/narration down.
  Over the cap narration falls back to AA text + neutral day lines (`ai_budget_exhausted`) and search
  answers the keyword ranking (200, `ai_budget_exhausted`; it was a 503 before AA-642 S0).
- **App profile (AA-1025 H1b, CIS 347)**: `backend/shared/app_profile.py` reads TripPlanner's row of the CIS app
  profile (`shared.llm_app_profile`, CIS 345) through `catalog.v_llm_app_profile_v1` (cached 20 s per container,
  fails open: no row / no view / a read error = AI on and no profile budget; `TP_APP_PROFILE=off` skips the read). AI
  switched off for app `tripplanner` in the CIS admin (AI providers › Providers › App profiles): `load_route` writes one
  0 USD ledger row (status `refused`, error_class `disabled`, no connection) and raises `AiPaused("ai_off")`, so
  narration answers basic / master_basic with `ai_paused` (`narration_degraded` reason `ai_off`, not counted by the
  per-session limit) and search the keyword ranking with `ai_paused`. The profile's `default_daily_budget_usd`, when
  set, is the daily cap over **every** `tp_*` stage (offline ones included) in place of `TP_DAILY_SPEND_CAP_USD`
  (online stages only, the fallback); that whole-app spend is the same `catalog.v_llm_app_spend_today_v1` summed over
  every stage (no table fallback, so an unreadable view fails closed like the per-stage read, AA-1027 E5), and the
  alert line then has scope `app`, `cap_source: app_profile`. The CIS ops check
  `llm_spend_alert` (sampler tick, 5 min) reads the same row and ledger and raises the admin bell + one ops e-mail a UTC
  day per threshold, so TripPlanner's spend alert reaches CIS without a CloudWatch alarm.
- **Per-visitor AI limits (AA-1015, migration tp-023)**: `backend/shared/ratelimit.py` counts each request that would
  call a model in `tripplanner.ai_rate_counter` (fixed windows, key = HMAC of scope + kind + value keyed with the edge
  key: no IP, session or account stored): narrate 10 / hour / guest session, search 30 / minute / traveller IP, chat
  (AA-1041) 20 / hour / guest session and 40 / hour / traveller IP, x2 signed in. Over the limit: 429 `rate_limited` with `retry_after_s` and a calm EN / VI line in the planner. The BFF
  forwards `x-tp-client-ip` (all trip calls and search) and `x-tp-signed-in` (search; narration uses the verified
  access token); both are trusted only with the right `x-tripplanner-key`. The counter fails open (ERROR line); the
  jobs Lambda kind `tp_retention_ai_rate_counter` deletes windows older than 2 days (DM-7.2). Optional stage caps
  `TP_DAILY_SPEND_CAP_SEARCH_USD` / `_COMPOSE_USD` (unset = combined cap only) and a once-a-day `llm_spend_alert`
  line at 80 % and 100 % of a cap.
- **Bot check (AA-1015, S262; AA-1038)**: the three TripPlanner rules of the ALB web ACL in front of the ECS task
  (rate per viewer IP, a tighter rate and a silent Challenge on the protected paths), never the app. The planner
  calls the protected calls at their own paths, `GET /api/browse/search`, `POST /api/trip/narrate`,
  `POST /api/trip/send` and (AA-1041) `POST /api/trip/chat` (`frontend/lib/botCheck.ts`), so map tiles and trip edits are never challenged; the edge gate
  (`lib/edgeGate.ts`, task env `TP_EDGE_GATE=on`) answers the older spellings 404 so nothing skips the rules. A
  challenged call (202 `x-amzn-waf-action: challenge`) never reaches the app, is not counted by the limits and shows
  a calm EN / VI line (`botCheck` notice). Vercel BotID did this until the Vercel retirement (AA-1038): no `botid`
  package, build flag or client init is left.
- **Planning chat (AA-1041 slice 2, `docs/implementation-notes/AA-1041.md`)**: `POST /trip/{id}/chat {session_id,
  message, history}` (assembly; owner guard like narrate). The model answers one JSON object per call (a read-only
  catalog tool of `backend/assembly/chat_tools.py`, or a reply with an optional proposal), at most 3 calls a message.
  The server keeps it inside the catalog: a proposal names only references the turn's tools returned (or the trip's
  stops) and is built from their catalog facts; a reply with a capitalised word the catalog did not give, a link, a
  dropped reference or the prompt's own words becomes a server sentence pointing to what AA offers (it names every
  country AA sells, by name). Ordinary English words, plurals, contractions and the demonyms of sold countries pass the
  grounding check in any case; only a word that is nothing but a name must come from the catalog. When the turn's last
  tool was `route_options` and the model answered without a proposal, the server proposes the first option and says so
  (ledger signal `server_proposal`). The planner shows the AI notice under each reply with `ai_generated: true` only
  (lane S277 l33, `docs/implementation-notes/AA-1041-l33.md`). The chat writes
  no trip state: "Apply to my trip" runs `remove` + `apply-route`. Money gates before every model call: the chat's
  month caps (tp-029 `tripplanner.ai_spend_month`, `TP_PLAN_CHAT_MONTHLY_CAP_USD` for the app and
  `TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD` per tenant, 2 USD each by default), then `budget_status` (daily app caps, the
  tenant's AI budget); fail closed. At a cap: 200 `{status: "paused", reason: "budget"}`, the planner closes the
  composer with a plain line; AI off / provider down / unreadable spend: `reason: "unavailable"`. UI:
  `components/PlanChat.tsx`, a "Plan with chat" tab of the desktop side panel and a "Chat" view on a phone.
- **Fail closed + degrade mode (AA-642 S0, root ADR 0009 decisions 5 and 6)**: the gateway never calls a
  model it cannot account for. Spend counter unreadable (or a counter write failed in the last 60 s,
  `SPEND_UNREADABLE_HOLD_SECONDS`, per container) → `budget_status` is `exhausted` with reason
  `spend_unreadable`; route config unreadable → `load_route` raises `AiPaused("route_unreadable")` (the
  hard-coded `BUILTIN_ROUTES` models are never called any more); every model of the route failed
  (provider outage, e.g. acc3 refusing) → degrade too. **AA-642 S263 E1:** a reply cut at its token limit
  (`stop_reason` max_tokens / length) is never returned: it is a billed failed attempt (ledger `status` /
  `error_class` `truncated`, tokens and cost kept, counted in the spend) and the route moves on. Each narration request
  has a route deadline (`route_deadline_s` of the route when the config view carries it, else 20 s, capped at 25 s
  under the HTTP API's 30 s): every attempt's boto client gets the time left as its timeout (one attempt, no boto
  retry; the satellite AssumeRole too), and when less than 1 s is left the next attempt is not started (one 0 USD
  row, `error_class` `deadline`) and narration degrades with reason `route_deadline`. Narration then answers 200 `basic` /
  `master_basic` with `"ai_paused": true` (never cached; a defect that is not a provider error still
  answers 502), search answers the keyword ranking with `"ai_paused": true`. Each degraded narration
  prints `{"evt":"narration_degraded","reason":…}`; each gateway failure that changes what travellers
  get prints `{"evt":"llm_gateway_error","level":"error","what":…}` (route_unreadable,
  spend_unreadable, spend_write_failed, call_log_write_failed). The planner shows one calm line under
  the narration (`lib/aiNotices.ts`, English by default, Vietnamese when `<html lang="vi">`).
  `/narrate` is limited to the session that created the trip and reuses
  `tripplanner.narration_cache` for an identical itinerary (the key includes the AA master day
  text, so CIS edits miss the cache). A trip whose every day has AA master text is answered first
  (`source: master`, `cached: false`): no model, cache, spend counter, route config or limit is touched, and it
  never carries `ai_budget_exhausted` / `ai_paused` (AA-1015, S262); only model text (`llm`, `mixed`) is cached. Search embeds the query as `search_query` and ranks only
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
- **AI notice (AA-1033)**: beside the labels, the ecosystem's one traveller notice ("Some descriptions are written
  with the help of AI and may contain mistakes. Prices, itineraries and visa conditions are confirmed in your official
  quote.") — `ui/AiNotice.tsx` (TripPlanner's own B2C look, `role="note"`) renders the sentence from the kit core copy
  `ui/core/aiNotice.messages.ts` (the CIS kit `tokens` profile carries it with `i18n.ts`; never a local copy of the
  text): under the tour page's day-by-day itinerary; in the planner (`/plan` has no site footer), under each surface
  with AI-written text — the map popup's experience list (`DestinationPopup`), a stop's activity list
  (`StopActivities`) and the narration (`TripPanel`), each with a "Use of AI" link (`termsHref`, new tab); and in the
  site footer with a "Use of AI" link to `/terms#ai` (terms §3 "Use of AI": what AI writes, that it can be wrong, for
  information, the confirmed quote and booking conditions bind; no liability waiver). Tests: `lib/aiNoticeKit.test.ts`.
  **AA-1040 (EU AI Act Art. 50):** under a narration whose answer `source` is `llm` or `mixed` (days the AI wrote on
  this request) the notice takes the kit `narration` variant ("This text was written by AI when you asked for it…",
  `lib/aiNotices.ts` `narrationNoticeVariant`); `master` / `master_basic` keep the traveller sentence. The narration
  block carries `data-ai-generated="true"` and `data-ai-source` (machine-readable mark in the page).
  On tour pages (`app/tours/[slug]`) the catalog text blocks (summary, highlights, legacy itinerary text, each
  day body) carry `data-ai-generated="true" data-ai-source="catalog" data-ai-generated-at=<updated_at>`
  (`catalogTextMark`); never a page-wide mark, since the page also shows prices, photos and facts.
  The browse Lambda's catalog JSON carries the same mark as data (AA-1040 lane l10): every tour card and the tour
  detail have `ai` = `{ai_generated: true, generator: "Adventure Asia catalog writer", model: null, generated_at:
  <text_updated_at, else published_at>, human_edited: null}` (`backend/browse/catalog.py` `catalog_ai_mark`, the
  object of the CIS tour reads and catalog.http.v1 1.3 `Tour.ai`); the page's attributes take `ai.generated_at` first
  (`tourTextMark`). `ai` is a Lambda helper, not a `catalog.tour` view field. `text_updated_at` (CIS 359) is when the
  Master text was last published; `v_tour.updated_at` also moves with a photo change, so the mark uses it only while
  359 is not applied (`has_text_updated_at` probes `pg_attribute` until the column is there; the `*_PRE_359` reads
  select NULL in its place).
  Narration day choice (`master_content.resolve_days`), because a component row's tour day can be
  wrong: per trip day, the own tour day text of any entry whose text names its stop (component or
  place name, accent-insensitive, generic suffixes such as "Temple" dropped); else the nearest other
  day of the same tour that names it and that no other entry of the itinerary owns or already took;
  else the own day text as it is; only a day with no AA text goes to the LLM.
- **Dispatch by deployment + circuit breakers (AA-1027 E3, root plan S263):** each model of a route is called on its
  deployments (`shared.llm_model_deployment` via the view's `deployments`: connection, wire id, priority, price
  overrides), in priority order, like CIS `vendor_route.deployment_plan`. `llm_gateway.deployment_plan` leaves out a
  disabled deployment or connection, a connection whose `served_roles` exclude the stage's role (acc2 never serves a
  writer), a data scope the stage's `allowed_scopes` exclude, a connection TripPlanner cannot call (Bedrock only, T-11:
  the Lambda role for a `task_role` connection in its region, the satellite role from the Lambda env for an
  `assume_role` connection whose account comes from `BEDROCK_ACCT_PRIMARY`), and anything behind an open circuit
  (`backend/shared/llm_breaker.py`): the CIS breaker state (`shared.llm_provider_health`) that CIS migration 341
  appends to the view as `deployment_routing` — a tripped **connection** circuit (account-wide refusal, e.g. Bedrock
  "Error 002" on acc3) skips every deployment of the connection, open or half-open (TripPlanner takes no probe lease:
  a CIS call that answers clears it), a deployment circuit skips while open or while another caller holds its probe —
  plus the Lambda container's own breaker over its own attempts (CIS rules: an account refusal opens at once,
  throttling after 3, anything else after 5; a content refusal is not counted). A model with nothing left to call
  gets one 0 USD `refused` ledger row with no connection (never a provider attempt), and a request where nothing was
  attempted degrades at once with reason `circuit_open` (narration `basic` + `ai_paused`, search keyword ranking).
  Before CIS 341 is applied (SQLSTATE 42703 on the appended columns, retried after 5 minutes), on the pre-323 table
  read or for a model without deployment rows, the deployments are the catalog's `bedrock_profile_ids` on the two
  connections TripPlanner always called (acc3 by the satellite role, acc2 by the Lambda role, `account_route` first);
  a profile naming any other account (the retired acc1, AA-998) is never called. Cross-account Claude access is
  **acc3 only** (the same account the CIS side uses; see `docs/ecosystem-architecture.md`); when acc3 fails or is
  skipped the route's next model is tried, then the call degrades (narration falls back to AA text, search to
  keyword ranking). The route view also carries `route_deadline_s` since CIS 341 (the E1 deadline reads it).
  Cohere embeddings run on acc2 first; the route's satellite row (`cohere-embed-v4-acc3`, CIS
  migration 240) takes over on an acc2 throttle (acc2 has 20 rpm, acc3 2,000 rpm). Each
  container caches one no-retry satellite embed client per account (one AssumeRole until 5 minutes
  before the creds expire), AA-862.
  Search embeds the query only in the embedding space of `geo.tour_place.embedding`, read from
  `catalog.v_embedding_space_v1` (CIS 329, AA-1026; `backend/shared/embedding_space.py`, cached 60 s): the route
  is limited to that space's models; when no route model is in it or its dimension is not `EMBED_DIM`, search
  answers with keyword ranking (reason `embedding_space_mismatch`); an unreadable space pauses vector search
  (`space_unreadable`); before CIS 329 (view missing) nothing changes. The ledger row carries
  `quality_signal.embedding_space`.
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
  `{"evt":"bff_upstream","route","op","status","ms","request_id"}` line per upstream call (the web task's log group `/ecs/aa-tripplanner-dev-web`).
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
  errors, no source maps, no headers / query / body; `release` = `SENTRY_RELEASE` (the image's commit),
  `environment` = `SENTRY_ENVIRONMENT` (task env).

## Deploy & CI

- **Frontend**: ECS Dev behind CloudFront, deployed by `.github/workflows/deploy-web-dev.yml` on a merge to `main` that
  touches `frontend/**` (PR: the `preview` job checks the image in the runner). The Vercel project and its deploy
  workflow were retired (AA-1038).
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
  `.github/scripts/post_deploy_verify.sh`, rows `.github/verify/{web,lambdas}.rows`. Web, after every verified ECS
  deploy (job `release` of `deploy-web-dev.yml`): `GET https://tripplanner.lumiguides.it.com/api/health` → 200 with
  `git_sha` = the deployed commit (`github.sha`) and `version` = its manifest. Lambdas: each function `Active`, `LastUpdateStatus`
  `Successful`, `CodeSha256` = the uploaded zip, whose S3 metadata `git-sha` / `app-version` = the deployed commit /
  version (written by the upload step), plus the smoke rows above. Red/green only, table in the job summary; a
  `result` job per deploy workflow is red when the deploy or verify did not succeed (Deploy Web Dev: verify and
  release may skip only while the service runs no task). A PR that adds a route or Lambda worth guarding adds a row.
- **CI checks**: required "Backend (pytest)", "Frontend (lint + build)", "Migrations (lint, PG 15
  replay)" (on a PR it also refuses an added line naming a migration by an old number after a renumber,
  `.github/scripts/migration_refs.py`, AA-1024 l22; a history note carries `old-mig-ok`; and an added tp-NNN that is not main max + 1 on the main of the run, or that an older open PR also adds under another name, `.github/scripts/migration_numbers.py`: no merge queue here, so re-run the PR's CI after another migration PR merged); plus "Security Audit" (AA-879: pip-audit on `backend/requirements.txt`, failing). Secret scan
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
- **Security headers (AA-879, AA-876)**: `.github/scripts/check_security_headers.sh` (plain curl, optional
  `RESOLVE` pin) in `deploy-web-dev.yml`: on the PR's preview image (`/privacy`) and on the public host through the
  distribution (`/`). A missing header or no answer fails (tests: `.github/scripts/test_check_security_headers.py`,
  run in "Backend (pytest)"). The Vercel Deployment Protection branches left with the Vercel project (AA-1038).
- **Releases (AA-891)**: release-please in manifest mode (`release-please-config.json`,
  `.release-please-manifest.json`, `CHANGELOG.md`, tags `vX.Y.Z`, `bump-minor-pre-major`), run by
  `.github/workflows/release-please.yml` on push to `main` in the environment `release` with an `aa-ecosys-ci-bot`
  App token (so the release PR gets its checks). The manifest is the only version file (the frontend `package.json`
  has none). A release commit touches only root files, so it deploys nothing; a build reports the manifest version
  of its commit: Lambdas `version` next to `git_sha` on log lines and as a Sentry tag (`backend/VERSION`, written
  by `package_lambdas.sh`); web app `GET /api/health` → `{"status","service","version","git_sha"}` (`APP_VERSION` /
  `GIT_SHA` baked into the image by `deploy-web-dev.yml`).

## Contracts with other apps

| Other side | This repo reads | This repo writes | Granted by |
|---|---|---|---|
| AA-CIS-App (catalog, AA-812) | `catalog.v_tour`, `v_tour_day`, `v_tour_place`, `v_tour_photo`, `v_destination`, `v_destination_photo` (contract `catalog.tour.v1`); photo URLs = `CIS_PUBLIC_API_BASE` + path | — | CIS migration 225 |
| AA-CIS-App (tenants, AA-1038 R5 slice 2) | `catalog.v_tenant_tripplanner_v1` (contract `tenant.entitlements.v1` 1.4: `tenant_id`, `slug`, `name`, `kind`, `account_status`, `in_service`, `served`, `tp_launcher`, `tp_branding`, `theme`, `locale`), read by the browse Lambda's `/browse/tenant/{slug}` for the `/{slug}` path; unreadable = every slug 404 | — | CIS migration 379 (SELECT to `tripplanner` only) |
| AA-CIS-App (tour liveness, AA-986) | `catalog.v_tour_retired` (tombstone: `source_system`, `source_id`, `state`, `reason`; contract `catalog.tour.v1.3`) for the saved-trip reasons and the tour page 410 | — | CIS migration 274 (until applied every read degrades to the pre-L6 answer) |
| AA-CIS-App (places / tour graph, AA-712) | `catalog.v_tour_component`, `v_tour_stop`, `v_tour_leg`, `v_tour_junction`, `v_graph_node`, `v_destination` (+ `located_by`, `precision`; 256: shape kind / status / length / bbox / `shape_lo`), `v_component_embedding` (HNSW top-k, AA-847), `v_destination_shape` (line geometry by id, slice 6d), `v_destination_redirect` (merged destination id → keeper: `/browse/destinations/{id}` answers 301, AA-847 Problem 2), `v_component_redirect` (dropped component id → the component of the same place, followed by every assembly reader, AA-990) — contract `catalog.graph.v1.2` / `catalog.tour.v1.2` | — | CIS migrations 228, 253 (`v_component_embedding`), 256 (`v_destination_shape`): SELECT to `tripplanner` only; 257 (`v_destination_redirect`); 285 (`v_component_redirect`, `tripplanner` only) |
| AA-CIS-App (AI gateway) | `catalog.v_llm_gateway_config_v1`, `catalog.v_llm_app_spend_today_v1` (AA-642 S8, CIS 323, applied); `catalog.v_embedding_space_v1` (AA-1026, CIS 329: the space of `geo.tour_place.embedding`); the view's 341 / 350 columns (deployment routing, stage x model params); `catalog.v_llm_app_profile_v1` (AA-1025 H1b, CIS 347: TripPlanner's app profile row). No read of `shared.llm_role_config` / `shared.llm_model_catalog` since AA-1027 E5 (CIS 351 revokes them) | `shared.llm_call_log` (ledger v2, `app = 'tripplanner'`) | CIS 173, 323, 329, 341, 347, 350, 351 |
| AA-Booking (tour from price, AA-1029) | `booking.v_from_prices` (contract `price.from.v1`: per catalog key the suggested retail price per person for 2 travellers, root ADR 0019 b15), read by the browse Lambda's tour detail (`from_price`, `browse/catalog.py`); unreadable or no row = no price shown, never computed here. The tour page shows "From … per person" in the "Make it yours" card and the mobile bar, and a schema.org `Offer`. Per-tenant reads (`catalog.v_tenant_tour_v1`, ADR 0019 d10) replace this AA catalogue path when that view exists | — | Booking `bk-039` (SELECT on the view + USAGE on schema `booking` to role `tripplanner`, nothing else of `booking`; guard `tests/test_extraction_ops.py`) |
| AA-Booking (retired, AA-1046: no consumer; tp-027 revoked `booking`'s SELECT on the view and USAGE on the schema, the view stays until a destructive drop with the leftover hand-off code) | — | `tripplanner.outbox` row `inquiry.submitted` v1 … v1.4 (customer + consent, days, leg chain, custom days), written in the same transaction as the `sent` event (AA-676, migration tp-008). AA-Booking reads `tripplanner.v_inquiry_outbox` and keeps its own inbox by `idempotency_key`; it never writes here. Contract: `AA-Ecosys/docs/contracts/inquiry.submitted.v1.4.json` (payload `version` 1.4 since AA-908: `customer.identity` = `{iss, sub}` of a signed-in traveller, else null, and `verified` true only with it; AA-Booking accepts 1.4 since bk-024 / Booking #90 and links the identity to one customer; 1.3 since AA-986 L6: every leg and day adds `available`, `reason`, `reason_detail`, and stops whose tour AA no longer serves follow the served ones with `available: false`; 1.2 since AA-830: each leg also carries the catalog tour key `source_system` + `source_id`, resolved against `catalog.v_tour` at send time — or, for a stop that left, from the tombstone — both null when unresolvable; outbox column `schema_version` = major 1) | ecosystem ADR 0001 decision 6 (outbox); migration tp-009 granted role `booking` USAGE on schema `tripplanner` + SELECT on the view only (AA-681); tp-027 revoked both |
| AA-CIS-App (admin overview, AA-858) | — | — (it **reads** `tripplanner.v_ops_traffic_daily`: per UTC day `planner_session_started`, `trip_started`, `trip_edited`, `inquiry_sent`, aggregates only, last 400 days; contract `ops.overview.v1`, metric names append-only) | migration tp-015 grants role `cis_app` (the CIS runtime from X1 S18; until then the CIS runtime `aa_cis_admin` reads it through its `tp_owner` membership) USAGE on schema `tripplanner` + SELECT on the view only |
| AA-CIS-App (admin data coverage, AA-992) | — | — (it **reads** `tripplanner.v_ops_data_totals` (metric, n: sessions, trip drafts / sent, trips with events, pin events, id-map rows, customers, traveller accounts, outbox rows, narration cache rows; totals, aggregates only), `tripplanner.v_ops_pin_components` (pinned catalog component id + number of trips; no trip / session id, no payload text) and `tripplanner.v_ops_component_id_map` (id-map edges, catalog ids only) to count pins whose component is gone, walking map + CIS redirect like `component_ids.id_map`; contract `ops.data_coverage.v1`, metric names and columns append-only) | migration tp-020 grants role `cis_app` SELECT on the three views only (USAGE on the schema since tp-015) |
| AA-CIS-App (TP-03 inquiry handoff check, AA-1003; retired 2026-11-03 with the B2B-only model, root ADR 0019: no CIS reader now, the view goes with the B2C drop) | — | — (it **read** `tripplanner.v_ops_inquiry_keys` (`idempotency_key`, `created_at` of each `inquiry.submitted` outbox row of the last 30 days; the hand-off key `<trip id>:<hand-off id>`, no payload, no contact) and matches it with `booking.v_ops_inquiry_inbox` (bk-035) to name each inquiry not received and its age; contract `ops.overview.v1` key view, columns append-only) | migration tp-022 (contract file: manual DB Migrate apply) grants role `cis_app` SELECT on the view only |
| AA-CIS-Infra | — | — | owns the Lambdas, API Gateway, OIDC deploy role, secrets and the DB Migrate workflow that applies `migrations/` |

Schema owners and version keys: `AA-Ecosys/docs/database/schema-ownership.md`.

## Boundaries — what this repo does NOT do

- It does not run the content pipeline (Atom/Segment/Route/Slate/Piece/publish) — that is
  AA-CIS-App's domain entirely.
- It does not own the `shared` schema (CIS does). It created `shared.destinations` (migrations
  002 / 005), but since ADR 0002 / CIS migration 201 the table is CIS-owned, and since AA-712 X4 (CIS 270) this
  repo cannot read it either (it reads `catalog.v_destination`); the only `shared` table it writes is the AI
  gateway ledger `llm_call_log` (INSERT), and it still holds the grandfathered SELECT on two gateway config tables.
- It does not provision its own AWS resources or OIDC roles — those live in AA-CIS-Infra.
- **No direct push to `main`.** Every change ships as a PR. The merge rule is the ecosystem's (root data standards
  DM-11.5): during the dev phase the orchestrating agent squash-merges its own PR once the required checks are green
  (the tech lead settled in S262 that this repo follows that rule too); subagents never merge. From the first prod or
  paying tenant, Terraform / IAM, migration, auth / security and data-deletion PRs wait for the tech lead's approval.
  `main` is protected: deletion and force push are blocked, and the checks Backend (pytest), Frontend (lint + build),
  Migrations (lint, PG 15 replay), Security Audit and gitleaks are required.

## Known gaps (deferred)

CloudFront in front of
browse; Mapbox token URL restriction; more countries in the gateway table.

## Related documents

- `README.md` (this repo) — setup/run instructions.
- `docs/ecosystem-architecture.md` (root docs repo) — the ecosystem-wide account map, Bedrock
  routing, OIDC design, and how TripPlanner sits next to AA-CIS-App / AA-CIS-Infra.
- `AA-CIS-Infra/tripplanner.tf` — the OIDC deploy role and any TripPlanner-specific AWS resources.
