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
  } else if (resource === "countries") {
    upstreamPath = `/browse/countries`;
  } else if (resource === "by-country") {
    const country = q.get("country") ?? "";
    upstreamPath = `/browse/by-country?country=${encodeURIComponent(country)}`;
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
