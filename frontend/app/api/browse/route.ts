import { NextRequest, NextResponse } from "next/server";
import { REQUEST_ID_HEADER, requestIdFor, upstreamLogLine } from "@/lib/requestId";
import { tourRouteBffAnswer } from "@/lib/tourRoute";
import { addCacheTag } from "@vercel/functions";
import { CATALOG_CACHE_TAG, browseCacheHeaders } from "@/lib/catalogPurge";
import { aiLimitHeaders } from "@/lib/tripProxy";
import { refresh, travellerIp } from "@/lib/cognito";
import { currentAccess } from "@/lib/authSession";
import { authEnv, callContext } from "@/lib/authRequest";

// BFF proxy to Lambda A (Map/Browse). Keeps BROWSE_API_URL server-side so
// the Lambda URL is never exposed to the browser. Maps a single
// ?resource= entrypoint onto the Lambda's REST paths.
const BROWSE_API_URL = process.env.BROWSE_API_URL ?? "";
// Shared secret the Lambdas verify (X-TripPlanner-Key). Server-side only —
// never exposed to the browser. Omitted header when unset (local dev).
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";

function upstreamHeaders(extra: Record<string, string> = {}): HeadersInit {
  const h: Record<string, string> = { ...extra };
  if (TRIPPLANNER_API_KEY) h["x-tripplanner-key"] = TRIPPLANNER_API_KEY;
  return h;
}

function passthroughFilters(src: URLSearchParams, dst: URLSearchParams) {
  for (const k of ["activity", "intensity_level", "country", "season"]) {
    const v = src.get(k);
    if (v) dst.set(k, v);
  }
}

export async function GET(req: NextRequest) {
  // AA-871: one id per request, sent to the Lambda and echoed on the response.
  const requestId = requestIdFor(req.headers);
  if (!BROWSE_API_URL) {
    return NextResponse.json(
      { error: "BROWSE_API_URL not configured" },
      { status: 503 },
    );
  }
  const q = req.nextUrl.searchParams;
  const resource = q.get("resource");
  const filters = new URLSearchParams();
  passthroughFilters(q, filters);

  let upstreamPath: string;
  if (resource === "tiles") {
    const tileId = q.get("tile_id") ?? "";
    upstreamPath = `/browse/tiles/${encodeURIComponent(tileId)}?${filters}`;
  } else if (resource === "destination") {
    const id = q.get("id") ?? "";
    upstreamPath = `/browse/destinations/${encodeURIComponent(id)}`;
  } else if (resource === "search") {
    filters.set("q", q.get("q") ?? "");
    upstreamPath = `/browse/search?${filters}`;
  } else if (resource === "shapes") {
    // AA-847 slice 6d: line geometry by id (CDN-cacheable upstream).
    const r = new URLSearchParams({ ids: q.get("ids") ?? "", level: q.get("level") ?? "lo" });
    upstreamPath = `/browse/shapes?${r}`;
  } else if (resource === "countries") {
    upstreamPath = `/browse/countries`;
  } else if (resource === "routes") {
    // AA-674 / PR-11: whole-route proposals from real AA tours.
    const r = new URLSearchParams({ country: q.get("country") ?? "", days: q.get("days") ?? "" });
    upstreamPath = `/browse/routes?${r}`;
  } else if (resource === "by-country") {
    const country = q.get("country") ?? "";
    upstreamPath = `/browse/by-country?country=${encodeURIComponent(country)}`;
  } else if (resource === "catalog-tours") {
    // AA-812: tour cards from the CIS catalog views (planner list view).
    const r = new URLSearchParams();
    for (const k of ["country", "min_days", "max_days", "month", "sort", "limit", "offset"]) {
      const v = q.get(k);
      if (v) r.set(k, v);
    }
    upstreamPath = `/browse/catalog/tours${r.toString() ? `?${r}` : ""}`;
  } else if (resource === "tour-route") {
    // AA-812: one tour's whole route ("Customise this trip").
    // source_id (stable) lets the Lambda resolve the current tour_id after a CIS re-upload.
    const r = new URLSearchParams();
    for (const k of ["tour_id", "source_id"]) {
      const v = q.get(k);
      if (v) r.set(k, v);
    }
    upstreamPath = `/browse/tour-route?${r}`;
  } else {
    return NextResponse.json({ error: "unknown resource" }, { status: 400 });
  }

  // AA-1015: search calls the embedding model, so it carries the traveller's address and signed-in state for the
  // per-IP limit (30 a minute, twice that signed in). Only search: the other reads are CDN-cached and call no model.
  let limitHeaders: Record<string, string> = {};
  let setCookies: string[] = [];
  if (resource === "search") {
    const env = authEnv(process.env);
    const access = await currentAccess(env.sessionSecret, req.cookies, (rt) => refresh(rt, callContext(env, req.headers)));
    limitHeaders = aiLimitHeaders(travellerIp(req.headers), access.accessToken !== null);
    setCookies = access.setCookies;
  }
  const started = Date.now();
  const upstream = await fetch(`${BROWSE_API_URL}${upstreamPath}`, {
    headers: upstreamHeaders({ accept: "application/json", [REQUEST_ID_HEADER]: requestId, ...limitHeaders }),
  });
  let body = await upstream.text();
  console.log(
    upstreamLogLine({ route: "browse", op: resource ?? "unknown", status: upstream.status, ms: Date.now() - started, request_id: requestId }),
  );
  // Upstream cache-control decides what is cacheable (tiles/detail cacheable, search not). AA-987: a cacheable answer
  // is kept by the Vercel CDN for the Lambda's TTL under tag tp-catalog (purged on a CIS lifecycle event) and by the
  // browser for 60 s at most (lib/catalogPurge.browseCacheHeaders).
  let cacheControl = upstream.headers.get("cache-control") ?? "no-store";
  let status = upstream.status;
  if (resource === "tour-route") {
    // AA-837 (D11): "no planner route for this tour" is a 200 `{ match: "none", tour }` for the
    // browser (an expected answer, not a red console error); the Lambda keeps its 404.
    const answer = tourRouteBffAnswer(upstream.status, body);
    if (answer.rewritten) {
      ({ status, body } = answer);
      cacheControl = "no-store";
    }
  }
  const cacheHeaders: Record<string, string> =
    status === 200 ? browseCacheHeaders(cacheControl) : { "cache-control": cacheControl };
  if (cacheHeaders["vercel-cache-tag"]) {
    // The documented API for the same tag as the `Vercel-Cache-Tag` header (Vercel docs, @vercel/functions
    // addCacheTag); both are kept so the purge reaches the entry whichever one the CDN reads. A no-op outside Vercel.
    try {
      await addCacheTag(CATALOG_CACHE_TAG);
    } catch {
      // tagging is best effort: the header still carries the tag, and the CDN TTL bounds the lag
    }
  }
  const res = new NextResponse(body, {
    status,
    headers: {
      "content-type": "application/json",
      ...cacheHeaders,
      [REQUEST_ID_HEADER]: requestId,
    },
  });
  // A refreshed (or cleared) traveller session rides on the search answer (no-store), as on /api/trip.
  for (const c of setCookies) res.headers.append("set-cookie", c);
  return res;
}
