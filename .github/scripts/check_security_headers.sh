#!/usr/bin/env bash
# AA-879 — fail when a deployed page lacks the six security headers of frontend/next.config.mjs
# (same list as the CIS admin, the CIS portal and AA-Booking, AA-876 / AA-885). Based on AA-Booking's script,
# plus the ALLOW_PROTECTED switch below (TripPlanner only, for now).
#
# Usage: check_security_headers.sh <deployment base URL> [path]   (path defaults to /login; TripPlanner passes /)
#
# How the page is fetched (Preview deployments sit behind Vercel Deployment Protection):
#   - BYPASS set        → curl with the x-vercel-protection-bypass header;
#   - else VERCEL_TOKEN → `vercel curl` (CLI pinned by VERCEL_CLI; team from VERCEL_ORG_ID), which
#                         reads the project's own protection-bypass token through the API with the
#                         team token; never add --debug: it prints that token in the (public) log;
#   - else              → plain curl (public hosts only).
# 3 tries, 10 s apart. Response headers only; nothing secret is printed (the CLI's stderr is only grepped).
#
# Protected answer: when the request is stopped by Vercel Deployment Protection (HTTP 401/403, a redirect to
# the Vercel login, or the CLI reporting that no automation bypass token exists), the app's own headers were
# never seen. By default that FAILS. With ALLOW_PROTECTED=1 it only warns and exits 0 — for a project that has
# no "Protection Bypass for Automation" yet. A page the app itself answered is always checked: a missing
# header fails whatever ALLOW_PROTECTED says, and so does no answer at all.
set -uo pipefail
BASE="${1:?deployment base URL}"
PATH_PART="${2:-/login}"
VERCEL_CLI="${VERCEL_CLI:-vercel@60.0.1}"
ALLOW_PROTECTED="${ALLOW_PROTECTED:-0}"

fetch_headers() {
  local out="$1"
  if [ -n "${BYPASS:-}" ]; then
    curl -sS -o /dev/null -D "$out" --max-time 30 -H "x-vercel-protection-bypass: ${BYPASS}" "${BASE}${PATH_PART}" 2>"$out.err"
  elif [ -n "${VERCEL_TOKEN:-}" ]; then
    # `vercel curl` forwards unknown flags (even --token / --scope) to curl, so the CLI reads the
    # token and team from the environment (VERCEL_TOKEN, VERCEL_ORG_ID) instead.
    VERCEL_TOKEN="$VERCEL_TOKEN" npx --yes "$VERCEL_CLI" curl "$PATH_PART" --deployment "$BASE" --yes \
      -- -sS -o /dev/null -D "$out" --max-time 30 >/dev/null 2>"$out.err"
  else
    curl -sS -o /dev/null -D "$out" --max-time 30 "${BASE}${PATH_PART}" 2>"$out.err"
  fi
}

# 0 when the answer came from Vercel Deployment Protection rather than from the app.
is_protected() {
  local headers="$1" errfile="$2" code
  code="$(awk 'NR==1 {print $2}' <<<"$headers")"
  case "$code" in
    401 | 403) return 0 ;;
    301 | 302 | 303 | 307 | 308)
      grep -qiE '^location: *[^ ]*(vercel\.com/(login|sso)|/sso-api|_vercel/sso)' <<<"$headers" && return 0 ;;
  esac
  [ -z "$headers" ] && grep -qi 'bypass' "$errfile" 2>/dev/null && return 0
  return 1
}

TMP="$(mktemp)"
MISSING="(not fetched)"
PROTECTED=0
for attempt in 1 2 3; do
  : >"$TMP"
  : >"$TMP.err"
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
  PROTECTED=0
  if is_protected "$HEADERS" "$TMP.err"; then
    PROTECTED=1
    break # protection does not go away on a retry
  fi
  [ "$attempt" -lt 3 ] && sleep 10
done
rm -f "$TMP" "$TMP.err"

if [ -z "$MISSING" ]; then
  echo "${BASE}${PATH_PART} carries all six security headers."
  exit 0
fi
if [ "$PROTECTED" = 1 ]; then
  if [ "$ALLOW_PROTECTED" = 1 ]; then
    echo "::warning::${BASE}${PATH_PART} answered with Vercel Deployment Protection (${STATUS:-no response}), so its security headers were NOT checked. Enable \"Protection Bypass for Automation\" on the Vercel project, then drop ALLOW_PROTECTED (AA-879)."
    exit 0
  fi
  echo "::error::${BASE}${PATH_PART} answered with Vercel Deployment Protection (${STATUS:-no response}): the protection bypass did not work, so the security headers could not be checked."
  exit 1
fi
echo "::error::${BASE}${PATH_PART} is missing security headers:${MISSING}"
exit 1
