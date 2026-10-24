import { NextRequest } from "next/server";
import { requestIdFor, upstreamLogLine } from "@/lib/requestId";
import { refresh } from "@/lib/cognito";
import { clearSessionCookies, currentAccess } from "@/lib/authSession";
import { authEnv, callContext } from "@/lib/authRequest";
import { authJson } from "@/lib/authResponse";
import { tripHeaders } from "@/lib/tripProxy";

// AA-908 — GET /api/me: who is signed in, and their trips (the assembly Lambda's GET /me/trips: trip and session
// ids, TripPlanner status draft / sent, stops, days, dates). 401 {"error":"sign_in_required"} when signed out, or
// when the Lambda refuses the token (the session cookies are then cleared).
export const dynamic = "force-dynamic";

const TRIP_API_URL = process.env.TRIP_API_URL ?? "";
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";

export async function GET(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  const env = authEnv(process.env);
  const access = await currentAccess(env.sessionSecret, req.cookies,
    (rt) => refresh(rt, callContext(env, req.headers)));
  if (!access.accessToken) {
    return authJson({ error: "sign_in_required" }, 401, requestId, access.setCookies);
  }
  if (!TRIP_API_URL) return authJson({ error: "TRIP_API_URL not configured" }, 503, requestId, access.setCookies);
  const started = Date.now();
  const upstream = await fetch(`${TRIP_API_URL}/me/trips`, {
    method: "GET",
    headers: tripHeaders(TRIPPLANNER_API_KEY, null, requestId, access.accessToken),
  });
  console.log(upstreamLogLine({ route: "trip", op: "me_trips", status: upstream.status, ms: Date.now() - started,
    request_id: requestId }));
  if (upstream.status === 401) {
    return authJson({ error: "sign_in_required" }, 401, requestId, clearSessionCookies());
  }
  if (!upstream.ok) {
    return authJson({ error: "upstream_error" }, 502, requestId, access.setCookies);
  }
  const body = await upstream.json();
  return authJson({ signed_in: true, email: access.email, email_verified: access.emailVerified,
    trips: Array.isArray(body?.trips) ? body.trips : [] }, 200, requestId, access.setCookies);
}
