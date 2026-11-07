# syntax=docker/dockerfile:1
# AA-1038 R5 hosting (root ADR 0019 d23, S265 tech-lead decision) — TripPlanner web image: the Next.js standalone server,
# run as the `web` container of the ECS Dev service aa-tripplanner-dev-web (AA-CIS-Infra
# modules/environment/tripplanner_web.tf: Fargate Spot ARM64, port 3000, health check /api/health) behind CloudFront and
# a CLOUDFRONT-scope web ACL (accounts/aa365/tripplanner_web_edge.tf). Built natively on an arm64 runner
# (.github/workflows/deploy-web-dev.yml). Same shape as the AA-Booking admin and AA-Partner web images.
FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Build-time values only. NEXT_PUBLIC_* are inlined into the browser bundle by `next build`, so they are build args:
# the Mapbox PUBLIC token (pk..., URL-restricted, shipped to every browser by design) and the canonical site URL.
# Every server value (BROWSE_API_URL, TRIP_API_URL, TRIPPLANNER_API_KEY, the session / revalidate keys) is read from
# the task's environment at run time; no secret is ever a build arg.
ARG NEXT_PUBLIC_MAPBOX_TOKEN=""
ARG NEXT_PUBLIC_SITE_URL="https://tripplanner.lumiguides.it.com"
# BotID needs the Vercel edge: off in this image (lib/botCheck.ts); the CloudFront web ACL guards search and narration.
ENV NEXT_OUTPUT=standalone \
    TP_BOT_CHECK=off \
    NEXT_PUBLIC_MAPBOX_TOKEN=${NEXT_PUBLIC_MAPBOX_TOKEN} \
    NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}
RUN NODE_OPTIONS=--max-old-space-size=2048 npm run build

FROM node:22-alpine AS runtime
ARG GIT_SHA=unknown
ARG APP_VERSION=unknown
# GET /api/health reports both (lib/health.ts); the deploy checks git_sha against the commit it built. Sentry tags its
# events with the same commit (lib/sentryLite.ts; Vercel set VERCEL_GIT_COMMIT_SHA instead).
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    GIT_SHA=${GIT_SHA} \
    APP_VERSION=${APP_VERSION} \
    SENTRY_RELEASE=${GIT_SHA} \
    # Keep-alive must outlive the ALB idle timeout (100 s); Node's 5 s default makes the ALB reuse a closed
    # connection and answer 502 (lesson S231, AA-741). Read by the Next standalone server.
    KEEP_ALIVE_TIMEOUT=105000
# The server needs node only: drop npm, npx and corepack from the runtime image (smaller attack surface).
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
      /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
WORKDIR /app
# Owned by the runtime user: the ISR cache (catalog pages, revalidated by the CIS purge notice) is written under .next.
COPY --from=build --chown=10001:10001 /app/.next/standalone ./
COPY --from=build --chown=10001:10001 /app/.next/static ./.next/static
COPY --from=build --chown=10001:10001 /app/public ./public
USER 10001
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
# Fargate sets HOSTNAME to the task's own hostname at run time, overriding the ENV above, and the Next standalone
# server binds to HOSTNAME: it would listen on the private IP only and the 127.0.0.1 health check would fail (lesson
# S249, AA-935). Force all interfaces at start, without a shell.
CMD ["node", "-e", "process.env.HOSTNAME='0.0.0.0'; require('./server.js')"]
