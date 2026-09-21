# Mapbox setup (AA-TripPlanner-Web)

This project uses Mapbox for two distinct things, and they need **two
different tokens** with different security postures:

| Where | What it does | Token type | Exposed to browser? |
|---|---|---|---|
| `frontend/` (Mapbox GL JS) | Renders the map, tiles, clustering | **Public** token, URL-restricted | Yes (unavoidable) |
| `backend/extraction/geocode.py` | Forward-geocodes place names during the offline pipeline | **Secret** token (or a public token kept server-side) | No — server-side only |

Mapbox's free tier covers both map loads and Geocoding within monthly
limits, which is enough for this MVP (31 tours → a few hundred geocode
calls, cached so each place is only geocoded once).

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

## 3. Get the token for the extraction pipeline (secret token)
The geocoding step runs offline on a trusted machine, never in the
browser, so it should use a token that is never exposed:
1. Go to **Tokens** → **Create a token**.
2. Name it e.g. `tripplanner-geocode`.
3. Public scopes default is fine for the Geocoding API. (Geocoding does
   not require a secret scope, but keeping this token server-side and
   out of the frontend bundle is the point — do not put it in a
   `NEXT_PUBLIC_*` var.)
4. Optionally create it as a **secret** token (starts with `sk.`) if you
   want scoped, revocable server credentials. A secret token is shown
   only once at creation — copy it immediately.

## 4. Where the tokens go

### Frontend (Vercel / local)
`frontend/.env.local` (git-ignored):
```
NEXT_PUBLIC_MAPBOX_TOKEN=pk.your_public_token_here
```
In production these are set in the Vercel project's Environment
Variables, not committed.

### Extraction pipeline (local / CI runner that runs the batch job)
`backend/.env` (git-ignored) or your shell environment:
```
MAPBOX_GEOCODING_TOKEN=sk.your_secret_token_here   # or a pk. token kept server-side
```
`geocode.py` reads this from the environment. It must never be imported
into any `NEXT_PUBLIC_*` variable or shipped to the client.

## 5. Free-tier limits to keep an eye on
- Map loads (GL JS) and Geocoding requests each have a monthly free
  allowance; beyond that they are billed. For this MVP the volume is
  tiny and cached, so cost should stay at zero.
- The geocode cache in `shared.destinations` (unique index on
  `lower(name)`) guarantees each place name is geocoded at most once —
  re-running extraction does not re-bill geocoding for known places.

## 6. Quick verification
Forward-geocode test (replace the token):
```bash
curl "https://api.mapbox.com/geocoding/v5/mapbox.places/Sapa.json?access_token=YOUR_TOKEN&limit=1"
```
A 200 with a `features[0].center` `[lng, lat]` pair means the token and
Geocoding access work.

---
Sources (Mapbox official docs, content rephrased for compliance):
- [Access tokens overview](https://docs.mapbox.com/help/dive-deeper/access-tokens/)
- [Public vs secret tokens](https://docs.mapbox.com/help/faq/what-is-the-difference-between-a-public-token-and-a-secret-token/)
- [How to use Mapbox securely (URL restrictions)](https://docs.mapbox.com/help/troubleshooting/how-to-use-mapbox-securely/)
- [Accounts and tokens](https://docs.mapbox.com/accounts/guides/tokens/)
