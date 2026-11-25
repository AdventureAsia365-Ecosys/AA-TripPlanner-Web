import { NextResponse } from "next/server";
import { healthBody } from "@/lib/health";
import { jsonInit } from "@/lib/jsonResponse";

// AA-891: GET /api/health -> {"status":"ok","service","version","git_sha"}. No upstream call: it says the web
// app answers and which release / commit it runs (the Lambdas log the same two fields on their lines).
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(healthBody(process.env), jsonInit({
    headers: { "cache-control": "no-store" },
  }));
}
