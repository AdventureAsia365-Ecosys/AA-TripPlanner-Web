// S294 hardening: every JSON answer of the BFF and the proxy names its charset ("application/json; charset=utf-8"),
// so no client has to guess the encoding. The Lambdas do the same in backend/shared/observability.py.
// lib/jsonResponse.test.ts also checks that every NextResponse.json in app/ and proxy.ts goes through jsonInit.

export const JSON_CONTENT_TYPE = "application/json; charset=utf-8";

/** The ResponseInit of a JSON answer: the caller's status and headers, with the JSON content type and its charset. */
export function jsonInit(init: ResponseInit = {}): ResponseInit {
  const headers = new Headers(init.headers);
  headers.set("content-type", JSON_CONTENT_TYPE);
  return { ...init, headers };
}
