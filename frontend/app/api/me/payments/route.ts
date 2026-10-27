import { NextRequest } from "next/server";
import { requestIdFor, upstreamLogLine } from "@/lib/requestId";
import { refresh } from "@/lib/cognito";
import { currentAccess } from "@/lib/authSession";
import { authEnv, callContext } from "@/lib/authRequest";
import { authJson } from "@/lib/authResponse";
import { bookingHeaders, bookingPaymentsUrl, pickPayments, upstreamFailure } from "@/lib/myPayments";

// AA-682 S5 — GET /api/me/payments: the signed-in traveller's payment requests from AA-Booking
// (GET /booking/customer/v1/payments, contract booking.customer.v1). The BFF forwards the traveller's own Cognito
// access token (the same one /api/me sends to the assembly Lambda); Booking maps it to its customer and returns only
// that customer's sent requests. Only the contract's fields are forwarded (lib/myPayments.ts pickPayments).
// 401 {"error":"sign_in_required"} only when there is no session. Booking refusing the token (401/403) is
// 502 {"error":"booking_refused"}: the section renders only after /api/me succeeded with the same session, so the
// refusal is Booking-side (client id or issuer mismatch, clock skew) and must neither clear the session nor show
// "Your session has ended" (a sign-in loop). Any other failure is 502 upstream_error. Both show Try again (lib/
// myPayments.ts upstreamFailure). Read only, no-store.
export const dynamic = "force-dynamic";

const UPSTREAM_TIMEOUT_MS = 8000;

export async function GET(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  const env = authEnv(process.env);
  const access = await currentAccess(env.sessionSecret, req.cookies,
    (rt) => refresh(rt, callContext(env, req.headers)));
  if (!access.accessToken) {
    return authJson({ error: "sign_in_required" }, 401, requestId, access.setCookies);
  }
  const url = bookingPaymentsUrl(process.env);
  if (!url) return authJson({ error: "BOOKING_API_URL invalid" }, 503, requestId, access.setCookies);
  const started = Date.now();
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: "GET",
      headers: bookingHeaders(access.accessToken, requestId),
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    console.log(upstreamLogLine({ route: "booking", op: "my_payments", status: 0, ms: Date.now() - started,
      request_id: requestId }));
    return authJson({ error: "upstream_error" }, 502, requestId, access.setCookies);
  }
  console.log(upstreamLogLine({ route: "booking", op: "my_payments", status: upstream.status,
    ms: Date.now() - started, request_id: requestId }));
  if (!upstream.ok) {
    const failure = upstreamFailure(upstream.status);
    return authJson({ error: failure.error }, failure.status, requestId, access.setCookies);
  }
  let body: unknown;
  try {
    body = await upstream.json();
  } catch {
    return authJson({ error: "upstream_error" }, 502, requestId, access.setCookies);
  }
  return authJson({ invoices: pickPayments(body) }, 200, requestId, access.setCookies);
}
