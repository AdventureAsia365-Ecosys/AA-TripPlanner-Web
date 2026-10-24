import { NextRequest, NextResponse } from "next/server";
import {
  MINTED_TOKEN_HEADER,
  guestCookieName,
  isGuestToken,
  isSameOrigin,
  mapMintedDenial,
} from "@/lib/guestToken";
import { REQUEST_ID_HEADER, requestIdFor, upstreamLogLine } from "@/lib/requestId";
import { parseTripBody, tripHeaders as lambdaHeaders, tripReadAnswer, tripReadPath } from "@/lib/tripProxy";
import { refresh } from "@/lib/cognito";
import { currentAccess, type Access } from "@/lib/authSession";
import { authEnv, callContext } from "@/lib/authRequest";

// BFF proxy to Lambda B (Trip Assembly). Keeps TRIP_API_URL server-side.
// Maps a single { op } POST body onto the Lambda's REST verbs/paths.
const TRIP_API_URL = process.env.TRIP_API_URL ?? "";
// Shared secret the Lambdas verify (X-TripPlanner-Key). Server-side only.
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";

// AA-827 / AA-909: mutations and the four trip reads carry the guest token (HttpOnly cookie) to the
// Lambda, which checks it against the stored sha256 (reads: of the session that owns the trip, else
// 404). The token is never logged or echoed.
// AA-871: plus the request id, which the Lambda binds to its log lines and echoes back.
// AA-908: plus the signed-in traveller's access token (refreshed when about to expire), so the account that owns a
// trip can open and edit it from any browser, and a signed-in edit links its session to the account.
function tripHeaders(guestToken: string | null, requestId: string, accessToken: string | null): Record<string, string> {
  return lambdaHeaders(TRIPPLANNER_API_KEY, guestToken, requestId, accessToken);
}

async function travellerAccess(req: NextRequest): Promise<Access> {
  const env = authEnv(process.env);
  return currentAccess(env.sessionSecret, req.cookies, (rt) => refresh(rt, callContext(env, req.headers)));
}

// A refreshed (or cleared) session rides on this no-store answer.
function withCookies(res: NextResponse, access: Access): NextResponse {
  for (const c of access.setCookies) res.headers.append("set-cookie", c);
  return res;
}

const READ_RESOURCES = new Set(["suggestions", "coverage", "activities"]);

function logUpstream(op: string, status: number, started: number, requestId: string): void {
  console.log(upstreamLogLine({ route: "trip", op, status, ms: Date.now() - started, request_id: requestId }));
}

// The cookie, or the token proxy.ts minted on this very request (no cookie came in; `minted`).
// The BFF never sets the cookie itself: proxy.ts does, as a raw Set-Cookie header.
function requestGuestToken(req: NextRequest): { token: string | null; minted: boolean } {
  const cookie = req.cookies.get(guestCookieName())?.value;
  if (isGuestToken(cookie)) return { token: cookie, minted: false };
  const minted = req.headers.get(MINTED_TOKEN_HEADER);
  return isGuestToken(minted) ? { token: minted, minted: true } : { token: null, minted: false };
}

// GET /api/trip?trip_id=... — the trip reads: the current itinerary (restore after a page reload,
// trip_id persisted client-side), suggestions, coverage and the activities at one stop.
export async function GET(req: NextRequest) {
  if (!TRIP_API_URL) {
    return NextResponse.json(
      { error: "TRIP_API_URL not configured" },
      { status: 503 },
    );
  }
  const requestId = requestIdFor(req.headers);
  // resource=suggestions | coverage | activities, or none for the itinerary (lib/tripProxy.ts).
  const read = tripReadPath(req.nextUrl.searchParams);
  if ("error" in read) {
    return NextResponse.json({ error: read.error }, { status: 400 });
  }
  // AA-909: a read without the owner's token is a 404 from the Lambda (a fresh trip with no draft
  // too); the client treats it as an empty trip. No cookies_required mapping on reads: a 404 never
  // resets the client's ids, so there is nothing to protect. AA-837: the itinerary read's 404 goes
  // to the browser as a 200 empty trip (tripReadAnswer), so a first visit logs no console error.
  const access = await travellerAccess(req);
  const started = Date.now();
  const upstream = await fetch(`${TRIP_API_URL}${read.path}`, {
    method: "GET",
    headers: tripHeaders(requestGuestToken(req).token, requestId, access.accessToken),
  });
  const text = await upstream.text();
  const resource = req.nextUrl.searchParams.get("resource");
  const op = resource && READ_RESOURCES.has(resource) ? resource : "itinerary"; // never echo free text
  logUpstream(`read:${op}`, upstream.status, started, requestId);
  const answer = tripReadAnswer(op, upstream.status, text);
  return withCookies(new NextResponse(answer.body, {
    status: answer.status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      vary: "Cookie",
      [REQUEST_ID_HEADER]: requestId,
    },
  }), access);
}

export async function POST(req: NextRequest) {
  // AA-827: a cross-site browser POST never reaches the Lambda (the cookie is SameSite=Lax anyway).
  // proxy.ts already refuses it before minting; this is defence in depth.
  if (!isSameOrigin(req)) {
    return NextResponse.json({ error: "cross_origin" }, { status: 403 });
  }
  if (!TRIP_API_URL) {
    return NextResponse.json(
      { error: "TRIP_API_URL not configured" },
      { status: 503 },
    );
  }
  // A body that is not a JSON object is a 400, not an unhandled 500 (seen in the AA-871 Sentry check).
  const parsed = parseTripBody(await req.text());
  if (!parsed) {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const b = parsed as Record<string, any>;
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
  const requestId = requestIdFor(req.headers);
  const access = await travellerAccess(req);
  const started = Date.now();
  const upstream = await fetch(`${TRIP_API_URL}${path}`, {
    method,
    headers: tripHeaders(guest.token, requestId, access.accessToken),
    body: JSON.stringify(payload),
  });
  const text = await upstream.text();
  logUpstream(String(b.op), upstream.status, started, requestId);
  // A refused token that was minted on this request -> 401 cookies_required (see mapMintedDenial).
  const mapped = mapMintedDenial(upstream.status, text, guest.minted);
  return withCookies(new NextResponse(mapped ? mapped.body : text, {
    status: mapped ? mapped.status : upstream.status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      vary: "Cookie",
      [REQUEST_ID_HEADER]: requestId,
    },
  }), access);
}
