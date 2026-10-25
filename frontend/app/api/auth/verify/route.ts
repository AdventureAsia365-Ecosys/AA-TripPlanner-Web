import { NextRequest } from "next/server";
import { guestCookieName, isGuestToken, isSameOrigin } from "@/lib/guestToken";
import { requestIdFor, upstreamLogLine } from "@/lib/requestId";
import { errorCode, refresh, verify } from "@/lib/cognito";
import { clearCookie, pendingCookie, readPending, sessionCookies, settleSignUpTokens } from "@/lib/authSession";
import { authEnv, authLogLine, callContext } from "@/lib/authRequest";
import { authJson } from "@/lib/authResponse";
import { tripHeaders } from "@/lib/tripProxy";

// AA-908 — POST /api/auth/verify {code}: check the email code of the pending step (__Host-tp_signin). On success the
// tokens go into the sealed session cookies, and the assembly Lambda's POST /me/claim links this browser's guest
// trips (the guest cookie) to the account. A sign-up confirmed without a sign-in session answers
// {step: "code", resent: true, mode: "signin"}: a sign-in code has been mailed. A sign-in refused for what looks like
// an abandoned sign-up answers {step: "code", resent: true, mode: "signup"}: a new sign-up code has been mailed.
export const dynamic = "force-dynamic";

const TRIP_API_URL = process.env.TRIP_API_URL ?? "";
const TRIPPLANNER_API_KEY = process.env.TRIPPLANNER_API_KEY ?? "";
const CODE = /^\d{6,8}$/;

export async function POST(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  if (!isSameOrigin(req)) return authJson({ error: "cross_origin" }, 403, requestId);
  const env = authEnv(process.env);
  if (!env.sessionSecret) return authJson({ error: "sign_in_unavailable" }, 503, requestId);
  const pending = readPending(env.sessionSecret, req.cookies);
  if (!pending) return authJson({ error: "code_expired" }, 400, requestId, [clearCookie("signin")]);
  let code = "";
  try {
    code = String((await req.json())?.code ?? "").trim();
  } catch {
    code = "";
  }
  if (!CODE.test(code)) return authJson({ error: "code_invalid" }, 400, requestId);

  let result;
  try {
    result = await verify(pending, code, callContext(env, req.headers));
  } catch (e) {
    const err = errorCode(e);
    console.log(authLogLine("verify", err, requestId));
    // A wrong code keeps the step (the traveller may type it again); anything else starts over.
    const status = err === "try_later" ? 429 : err === "code_invalid" || err === "code_expired" ? 400 : 502;
    return authJson({ error: err }, status, requestId, err === "code_invalid" ? [] : [clearCookie("signin")]);
  }
  if ("next" in result) {
    console.log(authLogLine("verify", result.next.mode === "signup" ? "signup_code_resent" : "signin_code_sent",
      requestId));
    return authJson({ step: "code", resent: true, mode: result.next.mode }, 200, requestId,
      [pendingCookie(env.sessionSecret, result.next)]);
  }

  // A sign-up's first token may predate the confirmed email (lib/authSession.ts settleSignUpTokens).
  let signedIn = result.tokens;
  if (pending.mode === "signup") {
    const settled = await settleSignUpTokens(result.tokens, (rt) => refresh(rt, callContext(env, req.headers)));
    if (settled.refreshed) console.log(authLogLine("verify", "signup_token_refreshed", requestId));
    signedIn = settled.tokens;
  }
  const cookies = [...sessionCookies(env.sessionSecret, signedIn), clearCookie("signin")];
  console.log(authLogLine("verify", pending.mode === "signup" ? "signed_up" : "signed_in", requestId));

  // Link this browser's guest trips. Best effort: a failed claim never fails the sign-in (the next signed-in edit
  // links its session anyway, handler._guard).
  const guest = req.cookies.get(guestCookieName())?.value;
  let claimed: number | null = null;
  if (TRIP_API_URL) {
    const started = Date.now();
    try {
      const upstream = await fetch(`${TRIP_API_URL}/me/claim`, {
        method: "POST",
        headers: tripHeaders(TRIPPLANNER_API_KEY, isGuestToken(guest) ? guest : null, requestId,
          signedIn.accessToken),
        body: "{}",
      });
      console.log(upstreamLogLine({ route: "trip", op: "me_claim", status: upstream.status,
        ms: Date.now() - started, request_id: requestId }));
      if (upstream.ok) claimed = Number((await upstream.json())?.claimed ?? 0);
    } catch {
      console.log(upstreamLogLine({ route: "trip", op: "me_claim", status: 0, ms: Date.now() - started,
        request_id: requestId }));
    }
  }
  return authJson({ signed_in: true, claimed }, 200, requestId, cookies);
}
