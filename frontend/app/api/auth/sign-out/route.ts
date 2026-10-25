import { NextRequest } from "next/server";
import { guestCookieName, isSameOrigin, isSecureCookieEnv } from "@/lib/guestToken";
import { requestIdFor } from "@/lib/requestId";
import { revoke } from "@/lib/cognito";
import { clearCookie, clearSessionCookies, readRefresh } from "@/lib/authSession";
import { authEnv, authLogLine, callContext } from "@/lib/authRequest";
import { authJson } from "@/lib/authResponse";

// AA-908 — POST /api/auth/sign-out: revoke the refresh token at Cognito (it also ends the access tokens issued from
// it), clear the session cookies, and drop this browser's guest cookie too. The guest sessions it holds were linked
// to the account at sign-in; without a new guest cookie the next person on this browser could still open them. The
// planner mints a fresh guest cookie on its next load, and the client forgets its trip ids.
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  if (!isSameOrigin(req)) return authJson({ error: "cross_origin" }, 403, requestId);
  const env = authEnv(process.env);
  const secure = isSecureCookieEnv();
  const cookies = [
    ...clearSessionCookies(secure),
    clearCookie("signin", secure),
    `${guestCookieName(secure)}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`,
  ];
  let outcome = "signed_out";
  const stored = env.sessionSecret ? readRefresh(env.sessionSecret, req.cookies) : null;
  if (stored) {
    try {
      await revoke(stored.rt, callContext(env, req.headers));
    } catch {
      outcome = "revoke_failed"; // the cookies are cleared anyway; the token expires on its own
    }
  }
  console.log(authLogLine("sign_out", outcome, requestId));
  return authJson({ signed_in: false }, 200, requestId, cookies);
}
