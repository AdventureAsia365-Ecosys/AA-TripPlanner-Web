// AA-871 — server-side error reporting to Sentry for the Next.js app, without the Sentry SDK.
//
// Why not @sentry/nextjs: it wraps the build (withSentryConfig, source-map upload, client + edge +
// server configs) and pulls in OpenTelemetry; this app only needs server errors with no PII. So
// instrumentation.ts hands Next's `onRequestError` here, and this file posts one event to the
// Sentry envelope endpoint with fetch (works in the Node and Edge runtimes). Off unless SENTRY_DSN
// is set (server-only env, never NEXT_PUBLIC_). No browser errors, no tracing, no source maps (the
// frames point at the built server chunks).
//
// Privacy (same rules as the Lambdas and AA-Booking): no headers, cookies, body, query string or
// user; the exception message and stack are scrubbed (e-mails, + phone numbers, Bearer tokens,
// JWTs, key-like query values). Pure apart from `fetch`, so `npm test` runs it.

export interface Dsn {
  envelopeUrl: string;
  publicKey: string;
  dsn: string;
}

export function parseDsn(raw: string | undefined): Dsn | null {
  if (!raw) return null;
  try {
    const u = new URL(raw.trim());
    const projectId = u.pathname.replace(/^\/+|\/+$/g, "").split("/").pop();
    if (u.protocol !== "https:" || !u.username || !projectId) return null;
    const prefix = u.pathname.replace(/\/?[^/]+\/?$/, "");
    return {
      envelopeUrl: `https://${u.host}${prefix}/api/${projectId}/envelope/`,
      publicKey: u.username,
      dsn: `https://${u.username}@${u.host}${prefix}/${projectId}`,
    };
  } catch {
    return null;
  }
}

const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g;
const PHONE = /(?<![\w+])\+\d[\d\s().-]{6,18}\d/g;
const BEARER = /\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi;
const JWT = /\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*/g;
const SECRET_PARAM =
  /\b((?:api_?key|apikey|access_token|refresh_token|id_token|token|key|password|secret|code)=)[^&\s'"]+/gi;

export function scrubText(text: string): string {
  return text
    .replace(JWT, "[jwt]")
    .replace(BEARER, (_m, kind: string) => `${kind} [Filtered]`)
    .replace(SECRET_PARAM, (_m, k: string) => `${k}***`)
    .replace(EMAIL, "[email]")
    .replace(PHONE, "[phone]");
}

interface Frame {
  function?: string;
  filename?: string;
  lineno?: number;
  colno?: number;
  in_app?: boolean;
}

// V8 stack lines -> Sentry frames, oldest first.
export function framesFrom(stack: string | undefined): Frame[] {
  const frames: Frame[] = [];
  for (const line of (stack ?? "").split("\n").slice(1)) {
    const m = /^\s*at (?:(.+?) \()?(.+?):(\d+):(\d+)\)?\s*$/.exec(line);
    if (!m) continue;
    const filename = m[2];
    frames.push({
      function: m[1] ?? "<anonymous>",
      filename,
      lineno: Number(m[3]),
      colno: Number(m[4]),
      in_app: !filename.includes("node_modules") && !filename.startsWith("node:"),
    });
  }
  return frames.reverse();
}

export interface RequestInfo {
  path: string;
  method: string;
  headers: Record<string, string | string[] | undefined>;
}

export interface ErrorContext {
  routerKind?: string;
  routePath?: string;
  routeType?: string;
  renderSource?: string;
}

const VALID_ID = /^[A-Za-z0-9._:-]{1,128}$/;

export function buildEvent(
  err: Error & { digest?: string },
  request: RequestInfo,
  context: ErrorContext,
  env: Record<string, string | undefined>,
  now: Date = new Date(),
): Record<string, unknown> {
  const rid = request.headers["x-request-id"];
  const requestId = typeof rid === "string" && VALID_ID.test(rid) ? rid : undefined;
  const tags: Record<string, string> = { service: "aa-tripplanner-web" };
  if (requestId) tags.request_id = requestId;
  if (context.routePath) tags.route = context.routePath;
  if (context.routeType) tags.route_type = context.routeType;
  if (context.routerKind) tags.router = context.routerKind;
  if (err.digest) tags.digest = err.digest;
  return {
    event_id: crypto.randomUUID().replace(/-/g, ""),
    timestamp: now.getTime() / 1000,
    platform: "node",
    level: "error",
    logger: "nextjs.onRequestError",
    server_name: "aa-tripplanner-web",
    // SENTRY_ENVIRONMENT names the platform environment ("dev" today: Vercel Production serves Dev, like the
    // Lambdas' ENVIRONMENT); without it, the Vercel target (production / preview / development).
    environment: env.SENTRY_ENVIRONMENT || env.VERCEL_ENV || "development",
    release: env.SENTRY_RELEASE || env.VERCEL_GIT_COMMIT_SHA || "unknown",
    tags,
    // Path only: the query string can hold search text or ids.
    request: { method: request.method, url: request.path.split("?")[0] },
    exception: {
      values: [
        {
          type: err.name || "Error",
          value: scrubText(String(err.message ?? "")),
          stacktrace: { frames: framesFrom(err.stack ? scrubText(err.stack) : undefined) },
        },
      ],
    },
  };
}

export function envelope(event: Record<string, unknown>, dsn: Dsn, now: Date = new Date()): string {
  const header = { event_id: event.event_id, sent_at: now.toISOString(), dsn: dsn.dsn };
  return `${JSON.stringify(header)}\n${JSON.stringify({ type: "event" })}\n${JSON.stringify(event)}\n`;
}

export async function reportRequestError(
  err: unknown,
  request: RequestInfo,
  context: ErrorContext,
  env: Record<string, string | undefined> = process.env,
  send: typeof fetch = fetch,
): Promise<boolean> {
  const dsn = parseDsn(env.SENTRY_DSN);
  if (!dsn) return false;
  const error = err instanceof Error ? err : new Error(String(err));
  const event = buildEvent(error, request, context, env);
  try {
    const res = await send(dsn.envelopeUrl, {
      method: "POST",
      headers: {
        "content-type": "application/x-sentry-envelope",
        "x-sentry-auth": `Sentry sentry_version=7, sentry_key=${dsn.publicKey}, sentry_client=aa-tripplanner-web/1.0`,
      },
      body: envelope(event, dsn),
      signal: AbortSignal.timeout(2000),
    });
    return res.ok;
  } catch {
    return false; // reporting must never turn into a second error
  }
}
