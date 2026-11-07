# AA-1038 — R5 hosting slice: the TripPlanner frontend on ECS behind CloudFront + a CLOUDFRONT-scope web ACL

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
  (Infra) rate-limits per viewer IP and Challenges the two model paths; a browser gets its WAF token from a silent
  Challenge on its first document navigation, so `fetch` calls to the model paths carry it.
- **Edge gate (`lib/edgeGate.ts`, task env `TP_EDGE_GATE=on`).** A WAF rule sees the URI path only, so the older
  spellings of the model calls (`GET /api/browse?resource=search`, `POST /api/trip {op:"narrate"}`) would pass the edge
  unchallenged. With the gate on they answer 404 `use_protected_path` before any upstream call. The search route no
  longer rewrites its pathname to `/api/browse` (the handler reads only the query), so the handler can tell the two
  apart. Off on Vercel, local and CI (BotID covers both spellings there).
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
  (+ test), `frontend/lib/edgeGate.ts` (+ test).
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
- WAF token lifetime: a traveller who stays on the planner longer than the token immunity time (Infra variable,
  4 hours) gets a challenged narration call (shown as the planner's error state) until the next page load. The AWS WAF
  JS integration (token refresh in the page) is the follow-up if that shows up.

## Should know

- Live order (orchestrator): root PR, Infra PR + bootstrap apply + Terraform Apply, repository settings
  (`TP_WEB_ENABLED`, `TP_WEB_CLOUDFRONT_DNS`, secret `TP_MAPBOX_PUBLIC_TOKEN`), this PR, scale to 1, re-run the deploy,
  then the runbook's checks and the DNS switch as its own approved step.
- Local proof (S272): `NEXT_OUTPUT=standalone next build` and the standalone server with the ECS env: `/api/health`
  reports the given sha, `/privacy`, `/` and `/plan` 200 with all headers and `X-Robots-Tag: noindex`, the old search
  and narrate spellings 404 `use_protected_path`, `/api/browse/search` passes the gate (500 only because no backend ran).
