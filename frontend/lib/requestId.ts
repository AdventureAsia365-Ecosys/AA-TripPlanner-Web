// AA-871 — one request id per BFF request, forwarded to the Lambdas and echoed to the browser.
// Pure (no Next imports), so `npm test` runs it.
//
// The id is the caller's x-request-id when it is well formed (same rule as the Lambdas and AA-Booking:
// [A-Za-z0-9._:-]{1,128}), else a new UUID. The BFF sends it to the Lambda as x-request-id (the Lambda
// binds it to every log line and echoes it back) and returns it on its own response, so one id finds the
// request in the Vercel logs and in CloudWatch. Never log query text, the guest token or the API key.

export const REQUEST_ID_HEADER = "x-request-id";
const VALID = /^[A-Za-z0-9._:-]{1,128}$/;

export interface HeaderSource {
  get(name: string): string | null;
}

export function acceptRequestId(value: string | null | undefined): string | null {
  const v = (value ?? "").trim();
  return VALID.test(v) ? v : null;
}

export function requestIdFor(headers: HeaderSource): string {
  return acceptRequestId(headers.get(REQUEST_ID_HEADER)) ?? crypto.randomUUID();
}

// One JSON line per upstream call (Vercel function logs). Fields are ids and names only.
export interface UpstreamLine {
  route: "browse" | "trip" | "booking";
  op: string;
  status: number;
  ms: number;
  request_id: string;
}

export function upstreamLogLine(line: UpstreamLine): string {
  return JSON.stringify({ evt: "bff_upstream", ...line });
}
