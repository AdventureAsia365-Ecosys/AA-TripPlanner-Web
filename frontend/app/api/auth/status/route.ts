import { NextRequest } from "next/server";
import { requestIdFor } from "@/lib/requestId";
import { refresh } from "@/lib/cognito";
import { currentAccess } from "@/lib/authSession";
import { authEnv, callContext } from "@/lib/authRequest";
import { authJson } from "@/lib/authResponse";

// AA-908 — GET /api/auth/status: {signed_in, email, email_verified} for the header link and the send form. Always 200
// (a signed-out visitor is not an error). Refreshes the access token when it is about to expire.
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const requestId = requestIdFor(req.headers);
  const env = authEnv(process.env);
  const access = await currentAccess(env.sessionSecret, req.cookies,
    (rt) => refresh(rt, callContext(env, req.headers)));
  return authJson(
    access.accessToken
      ? { signed_in: true, email: access.email, email_verified: access.emailVerified }
      : { signed_in: false, available: Boolean(env.sessionSecret) },
    200, requestId, access.setCookies);
}
