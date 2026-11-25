// AA-1111 (S294 ZAP triage) — the one error shape of the BFF (/api/*): a 5xx is `{"error": "<code>", "request_id":
// "<id>"}` with the x-request-id header, never exception text, a stack, a framework or version name or an internal
// path. Pure (no next/* import), so `npm test` runs it; route handlers return the standard `Response` it builds.
//
//   * upstreamErrorBody: a Lambda (or API Gateway) answer of 500 or more keeps only its `error` when that is a short
//     code; anything else (API Gateway's own `{"message": ...}`, a `detail`, text that is not JSON) is `upstream_error`.
//     Answers below 500 pass unchanged: they are the Lambdas' own codes and validation messages the planner reads.
//   * withApiErrors: a route handler that throws (a Lambda that cannot be reached, a bug) answers 500 `internal` and
//     logs one `bff_error` line (route, error class, request id), never the message (it can carry request values).
import { JSON_CONTENT_TYPE } from "./jsonResponse.ts";
import { REQUEST_ID_HEADER, requestIdFor, type HeaderSource } from "./requestId.ts";

// A code the Lambdas send ("internal", "narration_failed", "tenant tours unavailable"): lower case, digits, "_" or a
// space, at most 49 characters. Anything else is not passed on.
export const ERROR_CODE_RE = /^[a-z][a-z0-9_ ]{0,48}$/;
export const UPSTREAM_ERROR = "upstream_error";
export const INTERNAL_ERROR = "internal";
export const NOT_CONFIGURED = "not_configured";

export function errorBody(code: string, requestId: string): string {
  return JSON.stringify({ error: ERROR_CODE_RE.test(code) ? code : INTERNAL_ERROR, request_id: requestId });
}

function upstreamCode(text: string): string {
  try {
    const body: unknown = JSON.parse(text);
    const code = (body as { error?: unknown } | null)?.error;
    return typeof code === "string" && ERROR_CODE_RE.test(code) ? code : UPSTREAM_ERROR;
  } catch {
    return UPSTREAM_ERROR;
  }
}

/** The body the BFF sends for an upstream answer: unchanged below 500, else the generic shape. */
export function upstreamErrorBody(status: number, text: string, requestId: string): string {
  return status < 500 ? text : errorBody(upstreamCode(text), requestId);
}

export function errorResponse(code: string, status: number, requestId: string): Response {
  return new Response(errorBody(code, requestId), {
    status,
    headers: { "content-type": JSON_CONTENT_TYPE, "cache-control": "no-store", [REQUEST_ID_HEADER]: requestId },
  });
}

/** One JSON log line per caught error: names only (the message can carry request values). */
export function errorLogLine(route: string, err: unknown, requestId: string): string {
  const name = err instanceof Error && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(err.name) ? err.name : "Error";
  return JSON.stringify({ evt: "bff_error", route, error: name, request_id: requestId });
}

type Handler<R extends { headers: HeaderSource }, A extends unknown[]> = (req: R, ...rest: A) => Promise<Response>;

/** Wrap a route handler: a throw answers 500 `internal` with the request id instead of Next's bare 500. */
export function withApiErrors<R extends { headers: HeaderSource }, A extends unknown[]>(
  route: string,
  handler: Handler<R, A>,
  log: (line: string) => void = (line) => console.error(line),
): Handler<R, A> {
  return async (req, ...rest) => {
    try {
      return await handler(req, ...rest);
    } catch (err) {
      const requestId = requestIdFor(req.headers);
      log(errorLogLine(route, err, requestId));
      return errorResponse(INTERNAL_ERROR, 500, requestId);
    }
  };
}
