// AA-908 — what every traveller-auth BFF route needs from its request and environment. Server-only by convention;
// no Next imports, so `npm test` runs it.

import { travellerIp, type CallContext, type HeaderSource } from "./cognito.ts";

// TP_SESSION_SECRET seals the session cookies (lib/seal.ts); TP_COGNITO_BFF_KEY marks the BFF's Cognito calls for
// the web ACL (AA-CIS-Infra secret tripplanner/<env>/cognito-bff-key). Both are Vercel env values (Production and
// Preview), set by pipe, never typed into a file.
export const MIN_SECRET_CHARS = 32;

export interface AuthEnv {
  sessionSecret: string; // "" = sign-in is not configured here (the routes answer 503 sign_in_unavailable)
  bffKey: string;
}

export function authEnv(env: Record<string, string | undefined>): AuthEnv {
  const secret = (env.TP_SESSION_SECRET ?? "").trim();
  return {
    sessionSecret: secret.length >= MIN_SECRET_CHARS ? secret : "",
    bffKey: (env.TP_COGNITO_BFF_KEY ?? "").trim(),
  };
}

export function callContext(env: AuthEnv, headers: HeaderSource, fetchImpl?: typeof fetch): CallContext {
  return { bffKey: env.bffKey, travellerIp: travellerIp(headers), fetchImpl };
}

// One JSON line per auth step (Vercel logs): the step and its outcome only, never an email, code or token.
export function authLogLine(step: string, outcome: string, requestId: string): string {
  return JSON.stringify({ evt: "traveller_signin", step, outcome, request_id: requestId });
}

// A same-site path to land on after sign-in ("/trips" by default). Never another origin or a protocol-relative URL.
export function safeNext(value: unknown, fallback: string = "/trips"): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return fallback;
  }
  return value.length <= 200 ? value : fallback;
}
