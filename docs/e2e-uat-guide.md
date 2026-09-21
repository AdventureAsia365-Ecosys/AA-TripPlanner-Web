# E2E + UAT guide — AA-TripPlanner-Web (local)

Step-by-step to run the whole product locally and do user-acceptance
testing. Assumes you're on the WSL host with the repo at
`/home/nghiep/projects/AA-Ecosys/apps/AA-TripPlanner-Web`.

## 0. One-time env hygiene
Edit `.env` files with **LF** line endings (not CRLF). If you edited them
on Windows, normalize once:
```bash
sed -i 's/\r$//' backend/.env frontend/.env.local
```

### backend/.env — required keys
```
TRIPPLANNER_DATABASE_URL=postgresql://<real-user>:<real-pass>@localhost:<tunnel-port>/<dbname>
MAPBOX_GEOCODING_TOKEN=<your token>        # ✅ verified working
BEDROCK_ROLE_NAME=<cross-account role name>
BEDROCK_REGION=us-west-2
# BEDROCK_ACCT_PRIMARY / FALLBACK / MODEL_* have sensible defaults
ADVISOR_NOTIFY_EMAIL=pqnghiep1354@gmail.com
REQUIRE_REGISTRATION_BEFORE_HANDOFF=true
```
> The current `TRIPPLANNER_DATABASE_URL` is still the placeholder
> (`user:password@localhost:5432/aa_db`). Replace it with the real
> tunnel DSN before anything DB-backed will work.

### frontend/.env.local — required keys
```
NEXT_PUBLIC_MAPBOX_TOKEN=<pk. token>       # ✅ set
BROWSE_API_URL=http://localhost:8001
TRIP_API_URL=http://localhost:8002
```

## 1. Open the DB tunnel
Bring up your SSH/SSM tunnel to the shared RDS so `localhost:<port>`
reaches it. Verify:
```bash
set -a; . backend/.env; set +a
.venv/bin/python - <<'PY'
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(dsn=os.environ["TRIPPLANNER_DATABASE_URL"], timeout=10)
    print("OK", (await c.fetchval("select version()")).split(",")[0]); await c.close()
asyncio.run(m())
PY
```
Expect `OK PostgreSQL ...`.

## 2. Apply migrations (HUMAN-GATED — confirm before running)
Order matters: 002 before 001.
```bash
set -a; . backend/.env; set +a
psql "$TRIPPLANNER_DATABASE_URL" -f migrations/002_shared_destinations.sql
psql "$TRIPPLANNER_DATABASE_URL" -f migrations/001_tripplanner_schema.sql
```
Verify: `\dt tripplanner.*` → 5 tables; `\d shared.destinations` exists.

## 3. Run extraction (HUMAN-GATED — writes real data, spends quota)
Needs DB + Mapbox + Bedrock all working.
```bash
set -a; . backend/.env; set +a
PYTHONPATH=. .venv/bin/python -m backend.extraction.run
```
Expect: `Extraction complete: N components from M groups.`
Spot-check a few rows:
```bash
psql "$TRIPPLANNER_DATABASE_URL" -c \
"select source_tour_id, name, activity, source_day_index from tripplanner.itinerary_components limit 10;"
```

## 4. Start the two backend dev servers
Two terminals (each needs the env loaded):
```bash
# terminal A
set -a; . backend/.env; set +a
PYTHONPATH=. .venv/bin/python -m backend.browse.local_server     # :8001

# terminal B
set -a; . backend/.env; set +a
PYTHONPATH=. .venv/bin/python -m backend.assembly.local_server   # :8002
```
Smoke-check browse directly (tile over Vietnam, adjust to your data):
```bash
curl "http://localhost:8001/browse/tiles/21_105"
```

## 5. Start the frontend
```bash
npm --prefix frontend run dev      # http://localhost:3000
```

## 6. UAT click-path (acceptance)
1. Open http://localhost:3000 → map with destination pins (clustered
   where dense). No default country tab.
2. Pan/zoom → pins refresh for the visible 1° tiles.
3. Click an activity chip (e.g. trekking) → pins narrow.
4. Type in search → results re-rank by relevance within the filter.
5. Click a pin → popup lists that destination's components, each with
   Add.
6. Click Add on one → it appears in the right-hand trip panel with a
   Day badge; the popup shows "In your trip — Day N".
7. Add a second component from another destination → trip panel updates;
   day order follows the geographic sequence.
8. Drag a day to reorder → order persists (renarrate runs server-side).
9. Click "Send to advisor" → registration form appears (flag on) →
   enter name + email → submit → success message; check the advisor
   inbox (ADVISOR_NOTIFY_EMAIL) — note: email only actually sends once a
   real Sender transport (SES/SMTP) is wired; the default LoggingSender
   records but does not send.
10. Add another component after sending → still editable.

### Pass criteria
- [ ] Every component shown traces to a real active tour (grounding).
- [ ] Add/Remove updates the panel immediately.
- [ ] Reorder keeps the same components, only changes order.
- [ ] Guest trip survives registration (same trip, now owned by the customer).
- [ ] Trip > 25 days shows a non-blocking warning.

## Troubleshooting
- **Map blank / "token not set"** → NEXT_PUBLIC_MAPBOX_TOKEN missing in
  frontend/.env.local (restart `npm run dev` after editing).
- **BFF 503** → BROWSE_API_URL / TRIP_API_URL not set, or dev servers not
  running.
- **500 from :8001/:8002** → usually DB not reachable or migrations not
  applied; check the terminal running the dev server.
- **compose/renarrate empty** → Bedrock not reachable (IAM trust /
  BEDROCK_ROLE_NAME). The rest of the loop still works without it;
  narration just won't stream.
- **`command not found: ^M`** → a `.env` still has CRLF; re-run the sed
  from step 0.
