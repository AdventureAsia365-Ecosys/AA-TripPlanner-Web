# AA-1038 — R5 hosting slice: the TripPlanner frontend on ECS behind CloudFront + a CLOUDFRONT-scope web ACL

> **S272 follow-up (tech-lead cost decision, lane l16):** no Bot Control and no CLOUDFRONT web ACL; the three rules
> live in the ALB web ACL (AA-CIS-Infra `modules/waf`, scoped to the TripPlanner host). AWS WAF returns the JavaScript
> integration URL only for a web ACL with a paid Bot Control / ATP / ACFP group, so the `challenge.js` design below
> could not work. It is replaced by full page loads into the planner: see "S272 follow-up" at the end. Bullets marked
> *superseded* describe the first design.

Root ADR 0019 d23 and the S265 tech-lead decision (one Fargate Spot task + CloudFront + a CLOUDFRONT-scope WAF, about
13-15 USD a month on Dev; the Fargate and WAF cost yeses are given in the AA-1038 description). Three PRs, in order:
root runbook (`docs/ops/runbooks/tripplanner-web-ecs-cutover.md` + secrets register), AA-CIS-Infra
(`modules/environment/tripplanner_web.tf`, `accounts/aa365/tripplanner_web_edge.tf`), then this repo (image, deploy
workflow, edge-aware BFF). Vercel stays the live host until the DNS switch, which is a separate, approved step.

## Decisions

- **Same image model as the staff admins** (AA-Booking `frontend/Dockerfile`, AA-911): Next standalone behind
  `NEXT_OUTPUT=standalone` (CI and Vercel builds keep the default output), node:22-alpine, uid 10001, npm removed from
  the runtime, `KEEP_ALIVE_TIMEOUT=105000` (ALB idle 100 s), `HOSTNAME` forced to `0.0.0.0` at start (Fargate overrides
  it). Port 3000, health `/api/health` (unchanged AA-891 body; no upstream call), container check with node `fetch`.
- **Build-time values are build args, never secrets:** `NEXT_PUBLIC_MAPBOX_TOKEN` (the public pk token, URL-restricted;
  the repo stores it as the secret `TP_MAPBOX_PUBLIC_TOKEN` only because the workflow lint refuses a `vars.*TOKEN`
  name) and `NEXT_PUBLIC_SITE_URL`. `GIT_SHA`, `APP_VERSION` (release-please manifest) and `SENTRY_RELEASE` are baked
  into the runtime stage. Every server value comes from the task definition (AA-CIS-Infra).
- **BotID off in the image, the web ACL in its place.** BotID needs the Vercel edge. The CLOUDFRONT-scope web ACL
  (Infra) rate-limits per viewer IP and Challenges the two model paths and a document load of a planner page. Catalog
  pages are never challenged.
- *Superseded (S272 follow-up below):* **The planner's WAF token comes from the JavaScript integration (review S272, `lib/wafToken.ts`).** The first
  design relied on the challenged document load of `/plan`, but most ways into the planner are client-side Next
  navigations (header link, home search, tour and destination CTAs, all `next/link` or `router.push`): an RSC fetch
  with `sec-fetch-dest: empty`, never challenged, so search and narration then got the web ACL's 202, which `search()`
  parsed as JSON (throw) and `narrate()` reported as ok with an empty text. Now: the planner loads `challenge.js` from
  the web ACL's integration URL (build value `NEXT_PUBLIC_TP_WAF_INTEGRATION_URL`, repository variable
  `TP_WAF_INTEGRATION_URL` = Terraform output `tripplanner_web_edge.waf_integration_url`) as it mounts and calls
  `AwsWafIntegration.getToken()` before each model call (it stores the `aws-waf-token` cookie and refreshes it, which
  also ends the 4 h immunity gap). A call still challenged (`202` + `x-amzn-waf-action: challenge`, `isWafChallenge`)
  gets one token refresh and one retry (safe: the app never saw it), then one page reload per 5 minutes (a challenged
  document load), then the `botCheck` notice. `search()` and `narrate()` never read a challenge as an answer.
  Chosen over hard navigations into `/plan` (every link and `router.push` would need a guard forever, tenant links
  too) and over challenging every page (an interstitial on catalog pages and for crawlers). No CSP change: the CSP has
  no `script-src` / `connect-src`. Off the edge (Vercel, local, CI) the URL is empty and nothing loads.
- **Catalog routes render on request in the image (review S272, `lib/renderMode.ts`).** The image builds with no
  server value, so `next build` prerendered the home, `/destinations` and `sitemap.xml` with a failed catalog read
  (the "We couldn't load ..." state, a two-line sitemap), served for up to 30 / 60 minutes after each deploy. With
  `NEXT_OUTPUT=standalone` (both Dockerfile stages) those three routes call `connection()` and render per request
  (build output: `ƒ`); the catalog reads stay in the tagged fetch cache (`next.revalidate`, purged by CIS). Next never
  caches a 302 in its fetch cache, so the photo redirect lookups of `/destinations` (up to 24 per render) would have
  hit the catalog on every view: `resolveRedirect` now keeps successful targets in process for the catalog TTL
  (`REDIRECT_MEMO_MS`, at most 500, failures never kept); the ISR page kept them just as long. Chosen over BuildKit
  secret mounts of the catalog at build (the deploy role would need the edge key, and a build would depend on the
  catalog being up). Vercel, CI and local builds keep the prerendered ISR pages.
- **Edge gate (`lib/edgeGate.ts`, task env `TP_EDGE_GATE=on`).** A WAF rule sees the URI path only, so the older
  spellings of the model calls (`GET /api/browse?resource=search`, `POST /api/trip {op:"narrate"}`) would pass the edge
  unchallenged. With the gate on they answer 404 `use_protected_path` before any upstream call. The search route no
  longer rewrites its pathname to `/api/browse` (the handler reads only the query), so the handler can tell the two
  apart. Off on Vercel, local and CI (BotID covers both spellings there).
- **Dot-segment spellings are the web ACL's job (review S272).** Next resolves the request path with WHATWG `new URL`,
  so `/api/browse/%2e/search` or `/api/trip/./narrate` reaches the model route and the gate as the protected path.
  The gate cannot tell them apart and does not try; the web ACL URL-decodes and normalizes every path match
  (AA-CIS-Infra `tripplanner_web_edge.tf`), so they meet the same rate rule and Challenge. `lib/edgeGate.test.ts` pins
  the resolution the infra side relies on.
- **Client address behind CloudFront (`lib/clientIp.ts`, task env `TP_CLIENT_IP_SOURCE=cloudfront`).** CloudFront
  appends the viewer to the `x-forwarded-for` the viewer sent, so its first entry is forgeable there; the per-IP AI
  limits and the Cognito per-traveller WAF key would follow a forged value. CloudFront-Viewer-Address is set by
  CloudFront and forwarded by the origin request policy; fallback = the entry two from the right (AA-Partner
  `api/app/ratelimit.py` rule). On Vercel the source stays `x-forwarded-for` and the CloudFront header is ignored (a
  viewer could send it there).
- **Per-PR preview = the image started in the runner** (job `preview` of `deploy-web-dev.yml`, arm64, no AWS, no
  secret): health on the PR head commit, `/privacy` 200, the six security headers, the preview noindex header and the
  edge gate. Pages that read the catalog are not captured there (no backend): the Vercel preview and the visual run
  keep covering them until a mocked-API visual run exists (follow-up).
- **Verify through the real edge before DNS changes:** the deploy resolves `tripplanner.lumiguides.it.com` to the
  distribution (`vars.TP_WEB_CLOUDFRONT_DNS`) with `--resolve`, so the check passes CloudFront, the web ACL and the ALB
  host rule exactly as travellers will after the switch. `check_security_headers.sh` takes `RESOLVE=` for that.
- **Deploy role and trust unchanged:** the deploy job has no `environment:` because the TripPlanner deploy role trusts
  `ref:refs/heads/main` only (AA-CIS-Infra `tripplanner.tf`); it reuses `secrets.AWS_DEPLOY_ROLE_ARN`.

## Changed

- New: `frontend/Dockerfile`, `frontend/.dockerignore`, `.github/workflows/deploy-web-dev.yml`, `frontend/lib/clientIp.ts`
  (+ test), `frontend/lib/edgeGate.ts` (+ test), `frontend/lib/wafToken.ts` (+ test), `frontend/lib/renderMode.ts`
  (+ test).
- Review S272: `frontend/lib/api.ts` (`search()` and the narrate path of `postTrip()` through `throughWafEdge`, a
  challenge never ok), `frontend/app/plan/PlannerApp.tsx` (token on mount), `frontend/app/page.tsx`,
  `frontend/app/destinations/page.tsx`, `frontend/app/sitemap.ts` (render on request in the image),
  `frontend/lib/photoResolve.ts` (redirect memo), the deploy verify (real catalog content on `/`, `/destinations`,
  `sitemap.xml`; `challenge.js` answers), the build refuses an unset `TP_WAF_INTEGRATION_URL`.
- `frontend/next.config.mjs` (standalone switch), `frontend/lib/cognito.ts` (`travellerIp` delegates to `clientIp`),
  `frontend/app/api/browse/route.ts`, `frontend/app/api/browse/search/route.ts`, `frontend/app/api/trip/route.ts` (edge
  gate), `frontend/lib/health.ts` (comment), `.github/scripts/check_security_headers.sh` (+ test: `RESOLVE`),
  `CONTEXT.md` (ECS hosting bullet).
- Not changed here: `vercel-deploy.yml` and the Vercel project (retired only after the switch, runbook section 4), the
  privacy page's "Vercel hosts this website" line (changed in the retirement PR, when it stops being true).

## Tradeoffs

- One task (Spot) means a Spot interruption or a deploy is a short 503 on Dev; ISR and the gone-tour list are
  per-process, which with one task is the whole site (the purge notice reaches the only task).
- CloudFront caches static chunks and the photo proxy only; pages stay uncached at the edge (the Vercel CDN cached
  them by `s-maxage`). The task serves pages from its ISR cache, so the cost is one small origin hop per page view, not
  a render. Caching pages at CloudFront would need a CloudFront invalidation in the purge route (a paid call per path
  and an AWS grant on the task) — not worth it on Dev.
- *Superseded:* WAF token: `challenge.js` is a third-party script on the planner (AWS WAF's own host, the web ACL's integration
  URL); it is what AWS designs for single-page apps and replaces BotID's same-origin script. Each token fetch is a
  billed challenge attempt (cents at Dev volume).
- Home, `/destinations` and `sitemap.xml` render per request in the image (a few tens of ms of CPU on the 0.5 vCPU
  task, catalog data cached); pages are not cached at CloudFront either, so this is the cost of the image build having
  no catalog. Revisit with BuildKit secret mounts if the task's CPU shows it.

## Should know

- Live order (orchestrator): root PR, Infra PR + bootstrap apply + Terraform Apply, repository settings
  (`TP_WEB_ENABLED`, `TP_WEB_CLOUDFRONT_DNS`, secret `TP_MAPBOX_PUBLIC_TOKEN`; no WAF variable since the S272 follow-up), this PR, scale to 1, re-run the deploy,
  then the runbook's checks and the DNS switch as its own approved step.
- Local proof (S272): `NEXT_OUTPUT=standalone next build` and the standalone server with the ECS env: `/api/health`
  reports the given sha, `/privacy`, `/` and `/plan` 200 with all headers and `X-Robots-Tag: noindex`, the old search
  and narrate spellings 404 `use_protected_path`, `/api/browse/search` passes the gate (500 only because no backend ran).
- Local proof (S272 review pass): the standalone build lists `/`, `/destinations` and `/sitemap.xml` as `ƒ` (on
  request), the integration URL is inlined into one client chunk, and the standalone server answers the three 200.

## S272 follow-up — full page loads into the planner, no JavaScript SDK (lane l16)

### Decisions

- **The token comes from the Challenge on the planner's own document load.** Rule `TripPlannerChallengePlanner` of
  the ALB web ACL challenges a GET with `sec-fetch-dest: document` on a path that ends with `/plan` or contains
  `/plan/`; the interstitial solves silently and sets `aws-waf-token` for the host (4 h). So every in-site way into the
  planner must be a document load:
  - `ui/SiteLink.tsx`: the site's internal link. A planner href (`lib/plannerNav.ts` `isPlannerHref`, the same test
    as the WAF rule) renders a plain `<a>`; anything else stays a `next/link`. `ui/Button.tsx` `ButtonLink` goes
    through it, so every CTA (tour "Customise this trip" / "Ask", destination and `CtaBand` buttons, `not-found`,
    My trips) is covered without a per-call-site rule. The header, footer, `WaysIn`, home, destination and tour pages
    use `SiteLink` instead of `next/link` (consistent: one component, not a per-link prop).
  - `components/site/HomeSearch.tsx` "Plan my trip": `window.location.assign` instead of `router.push`.
  - Safety net, `PlannerApp` on mount (`reloadIntoPlannerDocument`): when the page shows a planner URL but the
    document was loaded for another page (performance navigation entry), it replaces itself with a document load of
    the same URL, once (after it the document IS the planner, so no loop). Covers the `/{tenant}` server redirect
    followed client-side, back / forward, and any link a later change forgets.
- **The 202 handling stays, simpler (`lib/wafChallenge.ts`, replaces `lib/wafToken.ts`).** A model call answered by
  the Challenge (`isWafChallenge`) is never read as the app's answer; it asks for one planner reload per 5 minutes
  (`reloadForWafToken`, sessionStorage), otherwise the `botCheck` notice. No token refresh and no second send (there
  is no SDK to refresh with).
- **Deploy:** no `TP_WAF_INTEGRATION_URL` variable, build arg or `challenge.js` check. The verify asserts `202` +
  `x-amzn-waf-action: challenge` without a token on `GET /api/browse/search`, `POST /api/trip/narrate`,
  `POST /api/trip/send` and the document load (`sec-fetch-dest: document`) of `/plan` and `/aa-tripplanner/plan`;
  a tour page loaded as a document answers `200`; the older spellings answer 404 `use_protected_path`. *Fix pass
  (review S272):* the first version accepted `200` for the `/plan` document load, but a request with no token can
  only get `200` when the rule does not match, which is exactly the failure that leaves every traveller challenged
  with no way to get a token: now only the `202` passes (the plain `/plan` without `sec-fetch-dest` keeps `200`).
- **Merged `origin/main`** into this branch (it was 3 commits behind): `api.ts` keeps main's `tenantFetch` on search
  inside `throughWafEdge`; `/api/trip` keeps main's BotID check on narrate + send (the edge gate covers both since
  the fix pass).

### Tradeoffs

- A planner link is a full page load (no client-side transition, no prefetch of the planner): a short white flash and
  the planner bundle fetched again from the CloudFront cache. Acceptable for one entry per visit; catalog navigation
  stays client-side.
- The reload safety net costs one extra load only when the planner was reached client-side, which the links no longer
  do.

### Should know

- Off the edge (Vercel, local, CI) links behave the same (document loads), no request is ever challenged.
- **"Send to advisor" is a protected path on ECS too (fix pass, review S272).** On Vercel BotID guards
  `POST /api/trip/send`; the image builds BotID off, so the first version left it with only the 3000 / 5 min per-IP
  rule and the Lambda's limits while it stores a traveller's contact for an agency. Now the web ACL challenges and
  rate-limits it with the model calls (Infra `local.tp_web_protected_paths`), and the edge gate answers the older
  spelling `POST /api/trip {op: "send"}` 404 (`lib/edgeGate.ts` `PROTECTED_PATHS`, one gate call for narrate and
  send). A challenged send is answered as the bot-check refusal in `sendToAdvisor` (`leadResultMessage`: "Please
  reload the page and try again"); no automatic reload, which would drop the lead form.
