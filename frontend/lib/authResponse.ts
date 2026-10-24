// AA-908 — response helpers of the traveller-auth BFF routes (app/api/auth/*, app/api/me). Server-only.
// Every answer is no-store and varies on Cookie: these routes set and read the session cookies, and a Set-Cookie
// must never ride on a cached response.
import { NextResponse } from "next/server";
import { REQUEST_ID_HEADER } from "@/lib/requestId";

export function authJson(
  body: unknown,
  status: number,
  requestId: string,
  setCookies: string[] = [],
): NextResponse {
  const res = NextResponse.json(body, {
    status,
    headers: { "cache-control": "no-store", vary: "Cookie", [REQUEST_ID_HEADER]: requestId },
  });
  for (const c of setCookies) res.headers.append("set-cookie", c);
  return res;
}
