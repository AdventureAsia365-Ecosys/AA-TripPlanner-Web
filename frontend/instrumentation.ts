// AA-871 — Next.js instrumentation hook: server-side errors (route handlers, Server Components,
// proxy.ts) go to Sentry when SENTRY_DSN is set. Dependency-free reporter in lib/sentryLite.ts
// (see there for why not @sentry/nextjs and what is scrubbed). Nothing to register at start-up.
import { reportRequestError, type ErrorContext, type RequestInfo } from "./lib/sentryLite";

export function register(): void {}

export async function onRequestError(err: unknown, request: RequestInfo, context: ErrorContext): Promise<void> {
  await reportRequestError(err, request, context);
}
