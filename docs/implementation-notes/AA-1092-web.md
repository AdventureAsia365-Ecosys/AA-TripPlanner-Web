# AA-1092 — the BFF half of the dedicated tenant-assertion key (web PR)

The full notes (threat note, decisions, rollout) are in `docs/implementation-notes/AA-1092.md`, shipped with the
backend PR. This file covers only what the web PR changes and when it may ship.

## Decisions

- **Separate PR, after the Lambdas** (review round 2): one PR touching `backend/**` and `frontend/**` starts Deploy
  Lambdas and Deploy Web Dev in parallel. Once the secret is filled, a web task that signs with the dedicated key
  while the Lambdas still run the pre-AA-1092 code (edge-derived key only) would get 401 on every `/{slug}` planner,
  My Trips and claim call. The backend PR ships first; this one after.

## Changed

- `frontend/lib/tenantAssertion.ts`: `assertionKeyMaterial` / `assertionKeyMaterialFromEnv` (the web task env
  `TP_TENANT_ASSERTION_KEY` when set and not the placeholder, else the edge key; shorter than 32 = nothing, 503).
- `frontend/lib/tenantProxy.ts`, `frontend/lib/accountProxy.ts` (`AccountCall.assertionKey`, required) and the four
  BFF routes (`/api/trip`, `/api/browse`, `/api/me`, `/api/auth/verify`); `frontend/.env.local.example`.
- Tests: `frontend/lib/tenantAssertion.test.ts` (the dedicated cross-language vector, the fallback, the floor) and
  `frontend/lib/accountProxy.test.ts`.

## Tradeoffs

- Without the env value the BFF keeps the edge key: merging this PR before the Infra fill changes nothing on Dev.

## Should know

- Merge only after: the backend PR's Deploy Lambdas + Post-deploy verify are green, the Infra apply mapped
  `TP_TENANT_ASSERTION_KEY`, the Infra tool filled the secret, and its `deploy_not_before` (write + 10 minutes, the
  Lambdas' secret cache) has passed. The Lambdas also re-read the secret once on a signature miss (at most every 30 s
  per container), so a deploy that came early is refused for seconds, not minutes.
- A web deploy after the Infra apply but before the fill fails to start its task (circuit breaker rolls back).
