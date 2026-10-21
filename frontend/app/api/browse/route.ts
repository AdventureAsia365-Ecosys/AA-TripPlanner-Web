import { NextRequest, NextResponse } from "next/server";

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

  const upstream = await fetch(`${BROWSE_API_URL}${upstreamPath}`, {
    headers: upstreamHeaders({ accept: "application/json" }),
  });
  const body = await upstream.text();
  // Preserve upstream cache-control (tiles/detail cacheable, search not).
  const cacheControl = upstream.headers.get("cache-control") ?? "no-store";
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": "application/json", "cache-control": cacheControl },
  });
}
