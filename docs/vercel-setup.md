# Vercel setup — AA-TripPlanner-Web (frontend)

> **S241 change:** deployments are no longer made by the Git integration.
> `frontend/vercel.json` sets `git.deploymentEnabled: false`, and
> `.github/workflows/vercel-deploy.yml` runs `vercel deploy` from the repo root
> (the Root Directory setting below still applies) only when `frontend/**`
> changes. It needs the repo secret `VERCEL_TOKEN`. Reason: the Hobby team cap
> (~100 deployments / 24 h, all projects, canceled ones included).

- PR touching `frontend/**` → **Preview** deployment (URL in one PR comment).
- Merge to `main` touching `frontend/**` → **Production** deployment.
- Anything else: Actions → Vercel Deploy → Run workflow (preview, or production from `main`).

Project: `aa-tripplanner` (Vercel), connected to
`github.com/AdventureAsia365-Ecosys/AA-TripPlanner-Web`.

## Required dashboard settings (one-time)

This is a monorepo — the Next.js app lives in `frontend/`, not the repo
root. Set these in the Vercel project:

1. **Settings → Build and Deployment → Root Directory** = `frontend`
   - Without this, Vercel builds from the repo root and won't find the
     Next.js app (build fails / nothing to deploy).
   - Framework preset auto-detects as **Next.js** once the root is set.
   - Build command / output can stay on defaults (`next build`).

2. **Settings → Environments → Environment Variables** (add for
   Production AND Preview):
   - `NEXT_PUBLIC_MAPBOX_TOKEN` = your public Mapbox token (`pk...`)
     - Exposed to the browser by design; restrict it by URL in Mapbox to
       the Vercel domain(s) once known.
   - `BROWSE_API_URL` = backend Lambda A URL (leave empty until the
     backend is deployed — the map still loads, BFF returns 503 for data)
   - `TRIP_API_URL` = backend Lambda B URL (same; empty until deployed)

3. **(Optional) Domain**: attach `tripplanner.lumiguides.it.com` (or a
   chosen subdomain) under Settings → Domains. Keeps this app on the
   shared root domain while staying a standalone project (per steering:
   own project, not a route inside AA-CIS-App).

## What "works" at each stage
- **Now (no backend URLs)**: the app builds and deploys; the map renders
  if `NEXT_PUBLIC_MAPBOX_TOKEN` is set, but destination pins/trip actions
  return errors because the BFF has no backend to call. Good for UI/UX
  review.
- **After backend deploy**: set `BROWSE_API_URL` / `TRIP_API_URL` and
  redeploy → full data + trip flow.

## Triggering the first deploy
A PR or merge that touches `frontend/**` triggers a build (see above). To
force one without a code change: Actions → Vercel Deploy → Run workflow, or
Vercel dashboard → Deployments → ⋯ → Redeploy.

## Verifying
- Build log should show `next build` running from `frontend/`.
- The Preview URL loads the map-first UI.
- If the map shows "Set NEXT_PUBLIC_MAPBOX_TOKEN…", add the env var and
  redeploy.
