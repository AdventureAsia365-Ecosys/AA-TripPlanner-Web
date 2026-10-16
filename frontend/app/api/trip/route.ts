import { NextRequest, NextResponse } from "next/server";
import {
  GUEST_TOKEN_HEADER,
  MINTED_TOKEN_HEADER,
  guestCookieName,
  isGuestToken,
  isSameOrigin,
  mapMintedDenial,
} from "@/lib/guestToken";

// BFF proxy to Lambda B (Trip Assembly). Keeps TRIP_API_URL server-side.
// Maps a single { op } POST body onto the Lambda's REST verbs/paths.
const TRIP_API_URL = process.env.TRIP_API_URL ?? "";
// Shared secret the Lambdas verify (X-TripPlanner-Key). Server-side only.
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";

// AA-827: mutations carry the guest token (HttpOnly cookie) to the Lambda, which checks it against
// the session's stored sha256. Reads stay token-free for now. The token is never logged or echoed.
function tripHeaders(guestToken?: string | null): Record<string, string> {
  const h: Record<string, string> = { "content-type": "application/json" };
  if (TRIPPLANNER_API_KEY) h["x-tripplanner-key"] = TRIPPLANNER_API_KEY;
  if (guestToken) h[GUEST_TOKEN_HEADER] = guestToken;
  return h;
}

// The cookie, or the token middleware.ts minted on this very request (no cookie came in; `minted`).
// The BFF never sets the cookie itself: middleware.ts does, as a raw Set-Cookie header.
function requestGuestToken(req: NextRequest): { token: string | null; minted: boolean } {
  const cookie = req.cookies.get(guestCookieName())?.value;
  if (isGuestToken(cookie)) return { token: cookie, minted: false };
  const minted = req.headers.get(MINTED_TOKEN_HEADER);
  return isGuestToken(minted) ? { token: minted, minted: true } : { token: null, minted: false };
}

// GET /api/trip?trip_id=... — fetch the current itinerary for a trip, used to
// restore a guest's trip after a page reload (trip_id persisted client-side).
export async function GET(req: NextRequest) {
  if (!TRIP_API_URL) {
    return NextResponse.json(
      { error: "TRIP_API_URL not configured" },
      { status: 503 },
    );
  }
  const tripId = encodeURIComponent(req.nextUrl.searchParams.get("trip_id") ?? "");
  if (!tripId) {
    return NextResponse.json({ error: "trip_id required" }, { status: 400 });
  }
  // resource=suggestions -> next-to-pin recommendations for this trip (with optional
  // activity/intensity filters); coverage -> the AA leg chain (AA-674); activities -> what
  // AA tours do at one stop (AA-674).
  const q = req.nextUrl.searchParams;
  const resource = q.get("resource");
  let path = `/trip/${tripId}`;
  if (resource === "suggestions") {
    const f = new URLSearchParams();
    for (const k of ["activity", "intensity_level"]) {
      const v = q.get(k);
      if (v) f.set(k, v);
    }
    path = `/trip/${tripId}/suggestions${f.toString() ? `?${f}` : ""}`;
  } else if (resource === "coverage") {
    path = `/trip/${tripId}/coverage`;
  } else if (resource === "activities") {
    const dest = encodeURIComponent(q.get("destination_id") ?? "");
    if (!dest) return NextResponse.json({ error: "destination_id required" }, { status: 400 });
    path = `/trip/${tripId}/stops/${dest}/activities`;
  }
  const upstream = await fetch(`${TRIP_API_URL}${path}`, {
    method: "GET",
    headers: tripHeaders(),
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

export async function POST(req: NextRequest) {
  // AA-827: a cross-site browser POST never reaches the Lambda (the cookie is SameSite=Lax anyway).
  // middleware.ts already refuses it before minting; this is defence in depth.
  if (!isSameOrigin(req)) {
    return NextResponse.json({ error: "cross_origin" }, { status: 403 });
  }
  if (!TRIP_API_URL) {
    return NextResponse.json(
      { error: "TRIP_API_URL not configured" },
      { status: 503 },
    );
  }
  const b = await req.json();
  const tripId = encodeURIComponent(b.trip_id ?? "");
  let method = "POST";
  let path = "";
  let payload: Record<string, unknown> = {};

  switch (b.op) {
    case "add":
      path = `/trip/${tripId}/components`;
      payload = { session_id: b.session_id, component_id: b.component_id };
      break;
    case "remove":
      method = "DELETE";
      path = `/trip/${tripId}/components/${encodeURIComponent(b.component_id)}`;
      payload = { session_id: b.session_id };
      break;
    case "reorder":
      method = "PATCH";
      path = `/trip/${tripId}/reorder`;
      payload = {
        session_id: b.session_id,
        ordered_component_ids: b.ordered_component_ids,
      };
      break;
    case "send":
      path = `/trip/${tripId}/send-to-advisor`;
      payload = { session_id: b.session_id, customer: b.customer };
      break;
    case "narrate":
      path = `/trip/${tripId}/narrate`;
      payload = { session_id: b.session_id, mode: b.mode };
      break;
    case "apply_route":
      path = `/trip/${tripId}/apply-route`;
      payload = { session_id: b.session_id, component_ids: b.component_ids };
      break;
    case "add_day":
      path = `/trip/${tripId}/days`;
      payload = { session_id: b.session_id, kind: b.kind, after_component_id: b.after_component_id };
      break;
    case "remove_day":
      method = "DELETE";
      path = `/trip/${tripId}/days/${encodeURIComponent(b.day_id ?? "")}`;
      payload = { session_id: b.session_id };
      break;
    default:
      return NextResponse.json({ error: "unknown op" }, { status: 400 });
  }

  const guest = requestGuestToken(req);
  const upstream = await fetch(`${TRIP_API_URL}${path}`, {
    method,
    headers: tripHeaders(guest.token),
    body: JSON.stringify(payload),
  });
  const text = await upstream.text();
  // A refused token that was minted on this request -> 401 cookies_required (see mapMintedDenial).
  const mapped = mapMintedDenial(upstream.status, text, guest.minted);
  return new NextResponse(mapped ? mapped.body : text, {
    status: mapped ? mapped.status : upstream.status,
    headers: { "content-type": "application/json", "cache-control": "no-store", vary: "Cookie" },
  });
}
