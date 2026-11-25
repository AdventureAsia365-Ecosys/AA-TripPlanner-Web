import { NextRequest, NextResponse } from "next/server";
import { MINTED_TOKEN_HEADER, guestCookieName, isGuestToken, isSameOrigin, mapMintedDenial } from "@/lib/guestToken";
import { REQUEST_ID_HEADER, requestIdFor } from "@/lib/requestId";
import { NOT_CONFIGURED, errorResponse, upstreamErrorBody, withApiErrors } from "@/lib/apiError";
import { parseTripBody, tripHeaders as lambdaHeaders } from "@/lib/tripProxy";
import { clientIp } from "@/lib/clientIp";
import { TENANT_SLUG_HEADER, resolveTenant } from "@/lib/tenantSite";
import { tenantHeaders } from "@/lib/tenantProxy";
import { assertionKeyMaterialFromEnv } from "@/lib/tenantAssertion";
import { addonsUpstream } from "@/lib/dayAddons";

// AA-1152: the AA add-ons of each day card (lib/dayAddons.ts). GET /api/trip/addons?trip_id= lists them; POST picks one
// and DELETE unpicks it ({trip_id, session_id, day, component_id}). Same rules as /api/trip (app/api/trip/route.ts): the
// guest token and the tenant assertion go to the Lambda, a cross-site mutation is refused, proxy.ts mints the cookie.
const TRIP_API_URL = process.env.TRIP_API_URL ?? "";
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";
const TENANT_ASSERTION_KEY = assertionKeyMaterialFromEnv(process.env);

function guestToken(req: NextRequest): { token: string | null; minted: boolean } {
  const cookie = req.cookies.get(guestCookieName())?.value;
  if (isGuestToken(cookie)) return { token: cookie, minted: false };
  const minted = req.headers.get(MINTED_TOKEN_HEADER);
  return isGuestToken(minted) ? { token: minted, minted: true } : { token: null, minted: false };
}

function json(body: unknown, status: number, requestId: string): NextResponse {
  return NextResponse.json(body, { status, headers: { "cache-control": "no-store", [REQUEST_ID_HEADER]: requestId } });
}

async function forward(req: NextRequest, method: "GET" | "POST" | "DELETE", body: Record<string, unknown>): Promise<Response> {
  const requestId = requestIdFor(req.headers);
  if (!TRIP_API_URL) return errorResponse(NOT_CONFIGURED, 503, requestId);
  const tripId = method === "GET" ? req.nextUrl.searchParams.get("trip_id") : String(body.trip_id ?? "");
  const upstreamReq = addonsUpstream(method, tripId, body);
  if (!upstreamReq) return json({ error: "invalid_request" }, 400, requestId);
  const tenant = await tenantHeaders(req.headers.get(TENANT_SLUG_HEADER), TENANT_ASSERTION_KEY, resolveTenant);
  if (!tenant.ok) return json({ error: tenant.error }, tenant.status, requestId);
  const guest = guestToken(req);
  const upstream = await fetch(`${TRIP_API_URL}${upstreamReq.path}`, {
    method: upstreamReq.method,
    headers: { ...lambdaHeaders(TRIPPLANNER_API_KEY, guest.token, requestId, clientIp(req.headers)), ...tenant.headers },
    body: upstreamReq.payload ? JSON.stringify(upstreamReq.payload) : undefined,
  });
  const text = await upstream.text();
  const mapped = method === "GET" ? null : mapMintedDenial(upstream.status, text, guest.minted);
  const status = mapped ? mapped.status : upstream.status;
  return new NextResponse(upstreamErrorBody(status, mapped ? mapped.body : text, requestId), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", vary: "Cookie", [REQUEST_ID_HEADER]: requestId },
  });
}

// AA-1111: a throw answers 500 {"error": "internal", "request_id"} (lib/apiError.ts), never Next's bare 500.
export const GET = withApiErrors("trip_addons", async (req: NextRequest) => forward(req, "GET", {}));

async function mutate(req: NextRequest, method: "POST" | "DELETE"): Promise<Response> {
  if (!isSameOrigin(req)) return NextResponse.json({ error: "cross_origin" }, { status: 403 });
  const parsed = parseTripBody(await req.text());
  if (!parsed) return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  return forward(req, method, parsed as Record<string, unknown>);
}

export const POST = withApiErrors("trip_addons", async (req: NextRequest) => mutate(req, "POST"));

export const DELETE = withApiErrors("trip_addons", async (req: NextRequest) => mutate(req, "DELETE"));
