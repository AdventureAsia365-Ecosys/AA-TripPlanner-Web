// AA-987: a tour CIS retires, pauses or restores leaves (or comes back to) the public site within a minute, not
// after the 30-min catalog TTL.
//
// CIS sends one signed notice per tour lifecycle event (CIS job `tripplanner_purge`, an outbox row of
// gold_aa_internal.tour_lifecycle_event = catalog.v_tour_lifecycle_outbox) to POST /api/revalidate/catalog. This
// module is the whole decision of that route, pure so `npm test` runs it (the route passes the Next / Vercel calls in):
//
//   1. verify the HMAC-SHA256 signature `X-AA-Signature: t=<unix s>,v1=<hex>` over `${t}.${raw body}` with
//      CATALOG_REVALIDATE_SECRET (constant-time compare; a timestamp more than SIGNATURE_TOLERANCE_S away is refused,
//      so a captured notice cannot be replayed later);
//   2. check the body (contract catalog.tour.lifecycle.v1: `events[]` with event_id, source_id, event);
//   3. purge: the Next data cache + ISR pages that read the catalog (`revalidateTag(CATALOG_CACHE_TAG, { expire: 0 })`
//      plus revalidatePath of the home, tour and destination routes, for a cached 404 that made no tagged fetch) and
//      the Vercel CDN entries of the browse BFF tagged CATALOG_CACHE_TAG (dangerouslyDeleteByTag: the next request
//      refetches instead of being served the stale list once more).
//
// Idempotent: every step drops a cache, so a replay or a CIS retry gives the same state; nothing is recorded here.
// A purge that fails answers 502 and CIS retries; if every retry fails, the time-based TTLs still bound the lag
// (30 min) as before AA-987. The tour page's 410 comes from proxy.ts (lib/goneTours.ts), whose per-instance list is
// at most GONE_MAX_STALE_MS old; this route cannot reach other instances' memory.
//
// No next/* or @vercel/* import here.

import { createHmac, timingSafeEqual } from "node:crypto";

export const CATALOG_CACHE_TAG = "tp-catalog";
export const SIGNATURE_HEADER = "x-aa-signature";
export const SIGNATURE_TOLERANCE_S = 300;
export const MAX_BODY_BYTES = 64 * 1024;
export const MAX_EVENTS = 100;
export const CONTRACT = "catalog.tour.lifecycle.v1";
export const LIFECYCLE_EVENTS = new Set(["retired", "paused", "restored", "superseded"]);

// Route patterns (revalidatePath with type "page" revalidates every page of the dynamic route).
export const PURGE_PATHS: ReadonlyArray<readonly [string, "page" | "layout" | undefined]> = [
  ["/", undefined],
  ["/tours/[slug]", "page"],
  ["/destinations/[country]", "page"],
];

export type SignatureCheck = { ok: true } | { ok: false; reason: "missing" | "malformed" | "stale" | "mismatch" };

/** Verifies `t=<unix seconds>,v1=<hex>` against `${t}.${body}` (the CIS side: tripplanner_purge_job.sign). */
export function verifySignature(
  secret: string,
  header: string | null | undefined,
  body: string,
  nowS: number,
  toleranceS: number = SIGNATURE_TOLERANCE_S,
): SignatureCheck {
  if (!header) return { ok: false, reason: "missing" };
  let t: string | null = null;
  let v1: string | null = null;
  for (const part of header.split(",")) {
    const i = part.indexOf("=");
    if (i <= 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k === "t") t = v;
    else if (k === "v1") v1 = v;
  }
  if (!t || !/^\d{1,12}$/.test(t) || !v1 || !/^[0-9a-f]{64}$/.test(v1)) return { ok: false, reason: "malformed" };
  if (Math.abs(nowS - Number(t)) > toleranceS) return { ok: false, reason: "stale" };
  const expected = createHmac("sha256", secret).update(`${t}.`).update(body).digest();
  const given = Buffer.from(v1, "hex");
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) return { ok: false, reason: "mismatch" };
  return { ok: true };
}

export interface LifecycleEvent {
  event_id: number;
  source_id: string;
  event: string;
}

export type ParsedNotice = { ok: true; events: LifecycleEvent[] } | { ok: false; error: string };

/** The fields this route needs from the notice; anything else in the body is ignored (additive contract). */
export function parseNotice(body: string): ParsedNotice {
  let data: unknown;
  try {
    data = JSON.parse(body);
  } catch {
    return { ok: false, error: "invalid_json" };
  }
  if (!data || typeof data !== "object") return { ok: false, error: "invalid_body" };
  const { contract, events } = data as { contract?: unknown; events?: unknown };
  if (contract !== CONTRACT) return { ok: false, error: "unknown_contract" };
  if (!Array.isArray(events) || events.length === 0 || events.length > MAX_EVENTS) {
    return { ok: false, error: "invalid_events" };
  }
  const out: LifecycleEvent[] = [];
  for (const e of events) {
    if (!e || typeof e !== "object") return { ok: false, error: "invalid_events" };
    const { event_id, source_id, event } = e as Record<string, unknown>;
    if (!Number.isSafeInteger(event_id) || (event_id as number) <= 0) return { ok: false, error: "invalid_event_id" };
    if (typeof source_id !== "string" || source_id.length === 0 || source_id.length > 64) {
      return { ok: false, error: "invalid_source_id" };
    }
    if (typeof event !== "string" || !LIFECYCLE_EVENTS.has(event)) return { ok: false, error: "invalid_event" };
    out.push({ event_id: event_id as number, source_id, event });
  }
  return { ok: true, events: out };
}

export interface PurgeDeps {
  secret: string;
  nowS: number;
  revalidateTag: (tag: string) => void;
  revalidatePath: (path: string, type?: "page" | "layout") => void;
  deleteCdnTag: (tag: string) => Promise<void>;
  log: (line: string) => void;
}

export interface PurgeAnswer {
  status: number;
  body: Record<string, unknown>;
}

/** One notice → the HTTP answer. Never throws. */
export async function handlePurge(
  signature: string | null,
  body: string,
  deps: PurgeDeps,
): Promise<PurgeAnswer> {
  const started = Date.now();
  const log = (fields: Record<string, unknown>) => deps.log(JSON.stringify({ evt: "catalog_purge", ...fields }));
  // Trimmed like the CIS side (parse_secret): an env set by piping (`echo ... | vercel env add`) carries a trailing
  // newline, and an untrimmed key would refuse every notice with 401 (NonRetryable on CIS = the purge never runs).
  const secret = deps.secret.trim();
  if (!secret) {
    log({ result: "not_configured" });
    return { status: 503, body: { error: "not_configured" } };
  }
  if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES) {
    log({ result: "too_large" });
    return { status: 413, body: { error: "too_large" } };
  }
  const sig = verifySignature(secret, signature, body, deps.nowS);
  if (!sig.ok) {
    log({ result: "unauthorized", reason: sig.reason });
    return { status: 401, body: { error: "unauthorized" } };
  }
  const notice = parseNotice(body);
  if (!notice.ok) {
    log({ result: "invalid", error: notice.error });
    return { status: 400, body: { error: notice.error } };
  }
  const events = notice.events;
  const summary = {
    events: events.length,
    event_ids: events.map((e) => e.event_id),
    kinds: [...new Set(events.map((e) => e.event))],
  };
  try {
    deps.revalidateTag(CATALOG_CACHE_TAG);
    for (const [path, type] of PURGE_PATHS) deps.revalidatePath(path, type);
  } catch (e) {
    log({ result: "error", step: "next", error: errName(e), ...summary, ms: Date.now() - started });
    return { status: 502, body: { error: "purge_failed", step: "next" } };
  }
  try {
    await deps.deleteCdnTag(CATALOG_CACHE_TAG);
  } catch (e) {
    log({ result: "error", step: "cdn", error: errName(e), ...summary, ms: Date.now() - started });
    return { status: 502, body: { error: "purge_failed", step: "cdn" } };
  }
  const purged = { tags: [CATALOG_CACHE_TAG], paths: PURGE_PATHS.map(([p]) => p), cdn_tags: [CATALOG_CACHE_TAG] };
  log({ result: "ok", ...summary, ms: Date.now() - started });
  return { status: 200, body: { ok: true, ...summary, purged } };
}

function errName(e: unknown): string {
  return e instanceof Error ? e.name : "unknown";
}

// ── browse BFF cache headers ─────────────────────────────────────────────────────────────────────────────────────

// The browser keeps a catalog answer at most this long (a purge cannot reach a browser cache); the Vercel CDN keeps
// it for the Lambda's TTL and drops it on a purge (tag CATALOG_CACHE_TAG).
export const BROWSER_MAX_AGE_S = 60;

/**
 * The cache headers of a /api/browse answer, from the Lambda's cache-control. A public answer with a max-age is
 * split: `cache-control` (browser) capped at BROWSER_MAX_AGE_S, `vercel-cdn-cache-control` (CDN only) with the
 * Lambda's TTL, and the CDN cache tag. Anything else (no-store, private, none) passes through unchanged.
 */
export function browseCacheHeaders(upstream: string | null | undefined): Record<string, string> {
  const passthrough = { "cache-control": upstream || "no-store" };
  const cc = (upstream ?? "").toLowerCase();
  const m = /(?:^|[,\s])max-age=(\d+)/.exec(cc);
  if (!m || !/(?:^|[,\s])public(?:$|[,\s])/.test(cc) || /no-store|private|no-cache/.test(cc)) return passthrough;
  const ttl = Number(m[1]);
  if (ttl <= 0) return passthrough;
  return {
    "cache-control": `public, max-age=${Math.min(ttl, BROWSER_MAX_AGE_S)}`,
    "vercel-cdn-cache-control": `public, max-age=${ttl}`,
    "vercel-cache-tag": CATALOG_CACHE_TAG,
  };
}
