# Mapbox setup (AA-TripPlanner-Web)

This project uses Mapbox for the browser map only. (Until AA-712 slice 6a the backend also
forward-geocoded place names with a second, server-side token; geocoding now runs in CIS,
ADR 0007, so the backend no longer needs a Mapbox token.)

| Where | What it does | Token type | Exposed to browser? |
|---|---|---|---|
| `frontend/` (Mapbox GL JS) | Renders the map, tiles, clustering | **Public** token, URL-restricted | Yes (unavoidable) |

Mapbox's free tier covers map loads within monthly limits, which is enough for this MVP.

## 1. Create a Mapbox account
1. Go to https://account.mapbox.com/auth/signup/ and sign up (email +
   password). No credit card is required to start on the free tier.
2. Verify your email and log in — you land on the account dashboard at
   https://account.mapbox.com/.

## 2. Get the token for the frontend map (public token)
1. On the dashboard, find the **Default public token** (it starts with
   `pk.`). You can use it as-is for local dev.
2. For anything beyond local dev, create a dedicated public token instead
   of reusing the default:
   - Go to **Tokens** → **Create a token**.
   - Name it e.g. `tripplanner-frontend`.
   - Under **Public scopes**, the defaults (styles:read, fonts:read,
     datasets:read, vision:read) are enough for GL JS. Leave secret
     scopes unchecked.
   - Under **URL restrictions**, add your domains once they exist
     (e.g. `http://localhost:3000`, and later the Vercel/production
     domain). A URL-restricted public token only works for requests
     coming from those origins, which limits abuse if the token leaks.
   - Note: you **cannot** add URL restrictions or secret scopes to the
     *default* public token — that's why we make a dedicated one for
     production. (per Mapbox docs)
3. This `pk.` token is safe to ship to the browser **only** with URL
   restrictions set. It still goes in a `NEXT_PUBLIC_*` env var (see
   below) because GL JS runs client-side.

## 3. Where the token goes

### Frontend (Vercel / local)
`frontend/.env.local` (git-ignored):
```
NEXT_PUBLIC_MAPBOX_TOKEN=pk.your_public_token_here
```
In production these are set in the Vercel project's Environment
Variables, not committed.

## 4. Free-tier limits to keep an eye on
- Map loads (GL JS) have a monthly free allowance; beyond that they are
  billed. For this MVP the volume is tiny, so cost should stay at zero.
- Place geocoding is no longer done here: CIS geocodes places (ADR 0007,
  AA-712) and this app reads the result.

---
Sources (Mapbox official docs, content rephrased for compliance):
- [Access tokens overview](https://docs.mapbox.com/help/dive-deeper/access-tokens/)
- [Public vs secret tokens](https://docs.mapbox.com/help/faq/what-is-the-difference-between-a-public-token-and-a-secret-token/)
- [How to use Mapbox securely (URL restrictions)](https://docs.mapbox.com/help/troubleshooting/how-to-use-mapbox-securely/)
- [Accounts and tokens](https://docs.mapbox.com/accounts/guides/tokens/)
