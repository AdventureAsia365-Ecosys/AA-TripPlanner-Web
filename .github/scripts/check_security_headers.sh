#!/usr/bin/env bash
# AA-879 — fail when a deployed page lacks the six security headers of frontend/next.config.mjs
# (same list as the CIS admin, the CIS portal and AA-Booking, AA-876 / AA-885). Based on AA-Booking's script.
#
# Usage: check_security_headers.sh <deployment base URL> [path]   (path defaults to /login; TripPlanner passes /)
#
# Plain curl, 3 tries 10 s apart, response headers only. With RESOLVE=<host>:443:<ip> the curl pins the host to that
# address (the CloudFront distribution, .github/workflows/deploy-web-dev.yml). A missing header fails, and so does no
# answer at all. (The Vercel Deployment Protection branches, bypass header and `vercel curl`, left with the Vercel
# project, AA-1038: every target is now the public host or the image started in the runner.)
set -uo pipefail
BASE="${1:?deployment base URL}"
PATH_PART="${2:-/login}"

fetch_headers() {
  local out="$1"
  local pin=()
  [ -n "${RESOLVE:-}" ] && pin=(--resolve "$RESOLVE")
  curl -sS -o /dev/null -D "$out" --max-time 30 "${pin[@]}" "${BASE}${PATH_PART}" 2>/dev/null
}

TMP="$(mktemp)"
MISSING="(not fetched)"
for attempt in 1 2 3; do
  : >"$TMP"
  fetch_headers "$TMP" || true
  HEADERS="$(tr -d '\r' <"$TMP")"
  STATUS="$(head -n1 <<<"$HEADERS")"
  MISSING=""
  grep -qiE "^content-security-policy:.*frame-ancestors 'none'" <<<"$HEADERS" || MISSING="$MISSING csp-frame-ancestors"
  grep -qiE '^x-content-type-options: *nosniff' <<<"$HEADERS" || MISSING="$MISSING nosniff"
  grep -qiE '^x-frame-options: *DENY' <<<"$HEADERS" || MISSING="$MISSING x-frame-options"
  grep -qiE '^referrer-policy: *strict-origin-when-cross-origin' <<<"$HEADERS" || MISSING="$MISSING referrer-policy"
  grep -qiE '^strict-transport-security:.*max-age=63072000' <<<"$HEADERS" || MISSING="$MISSING hsts"
  grep -qiE '^permissions-policy:' <<<"$HEADERS" || MISSING="$MISSING permissions-policy"
  echo "attempt $attempt: GET ${PATH_PART} -> ${STATUS:-no response}"
  [ -z "$MISSING" ] && break
  [ "$attempt" -lt 3 ] && sleep 10
done
rm -f "$TMP"

if [ -z "$MISSING" ]; then
  echo "${BASE}${PATH_PART} carries all six security headers."
  exit 0
fi
echo "::error::${BASE}${PATH_PART} is missing security headers:${MISSING}"
exit 1
