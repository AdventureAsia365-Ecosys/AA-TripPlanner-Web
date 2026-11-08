# AA-1038 — R5 hosting, final step: retire the Vercel project of TripPlanner

Root runbook `docs/ops/runbooks/tripplanner-web-ecs-cutover.md` section 4 step 1. Since the S272 cut-over
`tripplanner.lumiguides.it.com` is served by the ECS service `aa-tripplanner-dev-web` behind CloudFront and the three
TripPlanner rules of the ALB web ACL. This PR removes everything that only made sense on Vercel; the orchestrator
deletes the Vercel project and the repo secret `VERCEL_TOKEN` after merge.

## Decisions

- **The app on ECS behaves exactly as before.** On ECS BotID was already off (`TP_BOT_CHECK=off` in the image), the
  Vercel CDN calls were no-ops and the Vercel-only response headers had no effect behind CloudFront. Removing them
  changes no answer: the edge gate (`TP_EDGE_GATE=on`) and the web ACL still guard search, narration and send; the
  "botCheck" notice still shows for a 202 challenge and a challenged send still answers `bot_check_failed` to the form.
- **`lib/botCheck.ts` stays as the home of the protected paths** (`SEARCH_PATH`, `NARRATE_PATH`, `SEND_PATH`,
  `BOT_CHECK_FAILED`, `isBotRefusal`): AA-CIS-Infra `tripplanner_web_edge.tf` and its tests name that file. The BotID
  parts (`BOT_PROTECT`, `botCheckOn`, `botCheckRefusal`, the build flag `NEXT_PUBLIC_TP_BOT_CHECK` / `TP_BOT_CHECK`,
  `instrumentation-client.ts`) are gone. `edgeGate.test.ts` now checks the edge list against the `*_PATH` constants
  declared in `botCheck.ts` instead of `BOT_PROTECT`.
- **Catalog purge without a CDN step.** `handlePurge` no longer takes `deleteCdnTag` and the answer's `purged` drops
  `cdn_tags` (CIS only stores `purged` in the job result). `browseCacheHeaders` keeps only `cache-control` (browser
  60 s); CloudFront caches no `/api/*` answer and no page.
- **Vercel env fallbacks dropped:** `/api/health` git_sha no longer falls back to `VERCEL_GIT_COMMIT_SHA`; Sentry no
  longer reads `VERCEL_ENV` / `VERCEL_GIT_COMMIT_SHA` (the image sets `SENTRY_RELEASE`, the task sets
  `SENTRY_ENVIRONMENT`). `siteUrl()` defaults to the public host (the image passes the same value at build).
- **CI moved to the ECS deploy.** `vercel-deploy.yml` deleted. `deploy-web-dev.yml` gains `release`
  (post-deploy-verify.yml app web: health git_sha + release-please version on the public host, in `result`) and
  `visual` (visual.yml on the public host, report-only). `visual.yml` and `post-deploy-verify.yml` point at
  `https://tripplanner.lumiguides.it.com`; the visual run has no bypass secret, no preview target and no PR comment
  (no per-PR deployment exists), so it needs only `contents: read`. `check_security_headers.sh` loses the Vercel
  protection branches (bypass header, `vercel curl`, `ALLOW_PROTECTED`).
- **Privacy page:** hosting line Vercel -> Amazon Web Services (Amazon CloudFront and its website firewall); the
  storage section names CloudFront instead of Vercel; a "Security cookie" entry for the `aws-waf-token` the planner now
  gets (about 4 hours, no personal details), because the page lists every cookie.

## Changed

- Removed: `.github/workflows/vercel-deploy.yml`, `frontend/vercel.json`, `frontend/instrumentation-client.ts`,
  `docs/vercel-setup.md`; packages `botid`, `@vercel/functions` and the `@vercel/cli-config` override (lockfile
  regenerated, 235 lines fewer).
- Code: `frontend/lib/botCheck.ts`, `lib/catalogPurge.ts`, `lib/health.ts`, `lib/sentryLite.ts`, `lib/catalog.ts`,
  `app/api/browse/route.ts`, `app/api/trip/route.ts`, `app/api/revalidate/catalog/route.ts`, `proxy.ts`,
  `next.config.mjs`, `Dockerfile`, `app/privacy/page.tsx`; comments in the libs that described Vercel as the host.
- CI: `deploy-web-dev.yml`, `visual.yml`, `post-deploy-verify.yml`, `check_security_headers.sh`, rows and script
  comments, `dependabot.yml`, PR template; `e2e/visual/visual.spec.ts` (no bypass).
- Tests: `lib/botCheck.test.ts` (rewritten: no Vercel package, import, flag or header left), `edgeGate.test.ts`,
  `catalogPurge.test.ts`, `health.test.ts`, `sentryLite.test.ts`, `photoResolve.test.ts`, `siteMode.test.ts`,
  `clientIp.test.ts`; `.github/scripts/test_check_security_headers.py`, `test_post_deploy_verify.py`
  (`DeployWebDevWorkflowTest`), `test_visual_workflow.py`, `test_release_please.py`.
- Docs: `CONTEXT.md`, `README.md`, `.claude/CLAUDE.md`, `.kiro/steering/{tech,structure}.md`,
  `docs/architecture-overview.md`, `docs/mapbox-setup.md`, `docs/smoke-test-runbook.md`, `docs/roadmap.md`.

## Tradeoffs

- No per-PR deployment any more: the PR check is the image started in the runner (`preview` job), as since the
  cut-over. A visual run of a PR would need a running copy; not added (report-only today, cost).
- Historical reports (`docs/bao-cao-hien-trang-vi.md`, `docs/change-report-mvp-completion.md`,
  `docs/task-assessment.md`, older implementation notes) keep their Vercel wording: they record what was true then.

## Should know

- Merge after the AA-CIS-Infra PR of this step (record always on the distribution) and the AA-CIS-App PR (purge
  default). After merge `Deploy Web Dev` runs on main: expect deploy + verify + release green, visual report-only.
- The repo secret `VERCEL_TOKEN` (and `VERCEL_AUTOMATION_BYPASS_SECRET`, if set) is no longer read by any workflow:
  the orchestrator deletes it with the Vercel project.
- The visual run captures planner pages behind the web ACL Challenge in a real browser; if a planner shot shows the
  interstitial, give `e2e/visual/visual.spec.ts` a wait for the planner's own root (follow-up, report-only run).
