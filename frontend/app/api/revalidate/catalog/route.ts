import { revalidatePath, revalidateTag } from "next/cache";
import { NextRequest, NextResponse } from "next/server";
import { dangerouslyDeleteByTag } from "@vercel/functions";
import { SIGNATURE_HEADER, handlePurge } from "@/lib/catalogPurge";

// AA-987: CIS posts one signed notice per tour lifecycle event (retired / paused / restored / superseded); this route
// drops every TripPlanner cache that could still show the old state. All the logic (signature, body, which caches)
// is lib/catalogPurge.ts; this file only wires the Next.js and Vercel calls in.
//
// Secret: Vercel env CATALOG_REVALIDATE_SECRET (Production + Preview), the same value as the CIS Secrets Manager
// secret aa-cis/<env>/tripplanner-revalidate-hmac. Unset = 503 not_configured (CIS retries, then gives up; the
// 30-min TTLs still apply).
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const answer = await handlePurge(req.headers.get(SIGNATURE_HEADER), body, {
    secret: process.env.CATALOG_REVALIDATE_SECRET ?? "",
    nowS: Math.floor(Date.now() / 1000),
    // expire: 0 = the next request renders fresh (no stale page served once more after a retirement).
    revalidateTag: (tag) => revalidateTag(tag, { expire: 0 }),
    revalidatePath: (path, type) => revalidatePath(path, type),
    // The browse BFF answers carry `vercel-cache-tag: tp-catalog` (lib/catalogPurge.browseCacheHeaders). A no-op
    // outside Vercel (local dev, CI).
    deleteCdnTag: (tag) => dangerouslyDeleteByTag(tag),
    log: (line) => console.log(line),
  });
  return NextResponse.json(answer.body, { status: answer.status, headers: { "cache-control": "no-store" } });
}
