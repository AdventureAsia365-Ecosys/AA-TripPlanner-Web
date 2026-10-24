import { NextRequest } from "next/server";
import { isSameOrigin } from "@/lib/guestToken";
import { requestIdFor } from "@/lib/requestId";
import { errorCode, normaliseEmail, start } from "@/lib/cognito";
import { pendingCookie } from "@/lib/authSession";
import { authEnv, authLogLine, callContext } from "@/lib/authRequest";
import { authJson } from "@/lib/authResponse";

// AA-908 — POST /api/auth/start {email}: mail the traveller an email code (sign-up or sign-in, lib/cognito.ts) and
// keep the pending step in the sealed __Host-tp_signin cookie (10 minutes). The answer is the same whether the
// address has an account or not. No password exists anywhere in this flow (root ADR 0006 decisions 1 and 4).
export const dynamic = "force-dynamic";

const STATUS: Record<string, number> = {
  invalid_email: 400,
  try_later: 429,
  sign_up_unavailable: 503,
  delivery_failed: 502,
  unavailable: 502,
};

export async function POST(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  if (!isSameOrigin(req)) return authJson({ error: "cross_origin" }, 403, requestId);
  const env = authEnv(process.env);
  if (!env.sessionSecret) return authJson({ error: "sign_in_unavailable" }, 503, requestId);
  let email: string | null = null;
  try {
    email = normaliseEmail((await req.json())?.email);
  } catch {
    email = null;
  }
  if (!email) return authJson({ error: "invalid_email" }, 400, requestId);
  try {
    const pending = await start(email, callContext(env, req.headers));
    console.log(authLogLine("start", pending.mode, requestId));
    return authJson({ step: "code" }, 200, requestId, [pendingCookie(env.sessionSecret, pending)]);
  } catch (e) {
    const code = errorCode(e);
    console.log(authLogLine("start", code, requestId));
    return authJson({ error: code }, STATUS[code] ?? 502, requestId);
  }
}
