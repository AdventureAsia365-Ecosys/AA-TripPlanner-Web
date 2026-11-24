import { NextRequest, NextResponse } from "next/server";
import { REQUEST_ID_HEADER, requestIdFor } from "@/lib/requestId";
import { tripHeaders as lambdaHeaders } from "@/lib/tripProxy";
import { clientIp } from "@/lib/clientIp";
import { TENANT_SLUG_HEADER, resolveTenant } from "@/lib/tenantSite";
import { tenantHeaders } from "@/lib/tenantProxy";
import { assertionKeyMaterialFromEnv } from "@/lib/tenantAssertion";
import { agencyPriceUpstream } from "@/lib/dayAddons";

// AA-1152 (B2 option C): GET /api/trip/agency-price?source_id= — the agency's own "from" price of one tour for its tour
// page (components/site/AgencyFromPrice.tsx). Never cached anywhere (no-store here and in the Lambda), so the agency's
// "Show prices on my planner" switch and a new list show on the next view while the page itself stays cached. The
// tenant assertion names the agency; no guest token (no trip is read). AA's own pages send no tenant: no price.
const TRIP_API_URL = process.env.TRIP_API_URL ?? "";
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";
const TENANT_ASSERTION_KEY = assertionKeyMaterialFromEnv(process.env);

function json(body: unknown, status: number, requestId: string): NextResponse {
  return NextResponse.json(body, { status, headers: { "cache-control": "no-store", [REQUEST_ID_HEADER]: requestId } });
}

export async function GET(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  if (!TRIP_API_URL) return json({ error: "TRIP_API_URL not configured" }, 503, requestId);
  const path = agencyPriceUpstream(req.nextUrl.searchParams.get("source_id"));
  if (!path) return json({ error: "invalid_request" }, 400, requestId);
  const slug = req.headers.get(TENANT_SLUG_HEADER);
  if (!slug) return json({ price: null }, 200, requestId);
  const tenant = await tenantHeaders(slug, TENANT_ASSERTION_KEY, resolveTenant);
  if (!tenant.ok) return json({ error: tenant.error }, tenant.status, requestId);
  const upstream = await fetch(`${TRIP_API_URL}${path}`, {
    method: "GET",
    headers: { ...lambdaHeaders(TRIPPLANNER_API_KEY, null, requestId, clientIp(req.headers)), ...tenant.headers },
    cache: "no-store",
  });
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json", "cache-control": "no-store", [REQUEST_ID_HEADER]: requestId },
  });
}
