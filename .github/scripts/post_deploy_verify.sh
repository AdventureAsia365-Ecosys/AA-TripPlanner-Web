#!/usr/bin/env bash
# AA-993 V4 (ADR 0016 D3) — post-deploy verify of one TripPlanner deploy unit, run by
# .github/workflows/post-deploy-verify.yml after every production web deploy (vercel-deploy.yml) and every Lambda
# deploy (deploy-lambdas.yml), and on dispatch. Red/green, no warnings: every check below must pass or the script
# exits 1. Same shape as the AA-Booking / AA-Partner script, with Lambda rows instead of ECS checks.
#
# Usage: post_deploy_verify.sh <app>     (<app> names the rows file .github/verify/<app>.rows: web | lambdas)
#
# Rows (one per line, '#' comments): name|kind|target|expected|jq assertion
#   kind public  target "GET <path>" on BASE; expected = HTTP code; the jq assertion runs on the body (empty = code
#                only). Variables: $sha (EXPECTED_SHA), $version (EXPECTED_VERSION). A literal {cb} in the path is
#                replaced by a fresh cache-buster on every try (the Vercel CDN would otherwise answer from cache).
#   kind lambda  target "<function name> <artifact key>"; expected = LastUpdateStatus (Successful). The function must
#                be Active, its update Successful, and its CodeSha256 must equal the code-sha256 metadata of the
#                artifact s3://ARTIFACTS_BUCKET/<key>, whose git-sha / app-version metadata must equal EXPECTED_SHA /
#                EXPECTED_VERSION (deploy-lambdas.yml writes the three on upload). So: the function runs exactly the
#                zip the deploy built from the expected commit.
# Each row gets VERIFY_TRIES tries, VERIFY_RETRY_DELAY seconds apart, after VERIFY_INITIAL_WAIT seconds.
#
# Env: EXPECTED_SHA, EXPECTED_VERSION, BASE; for lambda rows also AWS_REGION, ARTIFACTS_BUCKET. Optional ROWS_FILE,
#      VERIFY_TRIES (3), VERIFY_RETRY_DELAY (10), VERIFY_INITIAL_WAIT (10), GITHUB_STEP_SUMMARY.
#      Needs bash, curl, jq (+ aws for lambda rows).
# Public repo: never print the bucket name or anything from AWS unscrubbed (ARNs and 12-digit account ids are masked
# in every detail, in the log and in the summary); an invalid EXPECTED_SHA is not echoed.
# Exit 0 = every check passed, 1 = at least one failed (2 = called without a valid app name).
set -uo pipefail

APP="${1:-}"
if ! [[ "$APP" =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo "::error::usage: post_deploy_verify.sh <app> (app name not valid)"
  exit 2
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROWS_FILE="${ROWS_FILE:-${HERE}/../verify/${APP}.rows}"
TRIES="${VERIFY_TRIES:-3}"
DELAY="${VERIFY_RETRY_DELAY:-10}"
INITIAL_WAIT="${VERIFY_INITIAL_WAIT:-10}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ROWS=()   # "name<TAB>PASS|FAIL<TAB>detail"
FAILED=0
DETAIL=""

# ARNs and 12-digit account ids out of any text that reaches the log or the (public) job summary. Bounded by
# non-alphanumerics, so a 40-hex commit SHA that happens to hold 12 digits in a row is left alone; the last group of
# a UUID (the all-zero trip id of the assembly rows) is shielded with a 'U' for the duration and kept.
# AWS CLI error text gets the stricter scrub below (aws_err) at capture time; this one is the second net on every
# detail.
scrub() {
  sed -E 's/arn:aws[a-z-]*:[^ ]*/<arn>/g
          s/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-)([0-9]{12})/\1U\2/g
          s/(^|[^0-9A-Za-z])[0-9]{12}([^0-9A-Za-z]|$)/\1<acct>\2/g
          s/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-)U([0-9]{12})/\1\2/g'
}

record() {  # name, PASS|FAIL, detail
  local detail
  detail="$(printf '%s' "$3" | tr '\n' ' ' | scrub)"
  ROWS+=("$1"$'\t'"$2"$'\t'"$detail")
  if [ "$2" = "PASS" ]; then
    echo "verify [$1] OK: $detail"
  else
    echo "::error::verify [$1] $detail"
    FAILED=1
  fi
}

retry() {  # name, function, args... — the function sets DETAIL and returns 0 (pass) / 1 (fail)
  local name="$1"; shift
  local try
  for try in $(seq 1 "$TRIES"); do
    DETAIL=""
    if "$@"; then
      record "$name" PASS "$DETAIL"
      return 0
    fi
    if [ "$try" -lt "$TRIES" ]; then
      echo "verify [$name] try $try/$TRIES failed: $(printf '%s' "$DETAIL" | scrub)"
      sleep "$DELAY"
    fi
  done
  record "$name" FAIL "$DETAIL (after $TRIES tries)"
  return 1
}

short() { printf '%s' "${1:0:12}"; }

# The first 200 bytes of an AWS CLI stderr file, with ARNs and every 12-digit run masked (AA-993 review rule).
aws_err() { sed -E 's/arn:aws:[^ ]*/<arn>/g; s/[0-9]{12}/<acct>/g' "$1" | tr '\n' ' ' | head -c 200; }

# ── inputs ────────────────────────────────────────────────────────────────────────────────────────────────────────
rows_have_kind() { [ -f "$ROWS_FILE" ] && grep -Ev '^[[:space:]]*(#|$)' "$ROWS_FILE" | cut -d'|' -f2 | grep -qx "$1"; }

check_inputs() {  # sets BLOCKED=1 when no row can run at all
  local missing=() name required=(EXPECTED_SHA EXPECTED_VERSION BASE)
  rows_have_kind lambda && required+=(AWS_REGION ARTIFACTS_BUCKET)
  for name in "${required[@]}"; do
    [ -n "${!name:-}" ] || missing+=("$name")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    record inputs FAIL "not set: ${missing[*]}"
    BLOCKED=1
  else
    record inputs PASS "expected commit, version and base URL set$(rows_have_kind lambda && echo ', AWS region and artifacts bucket set')"
  fi
  if [ -n "${EXPECTED_SHA:-}" ] && ! [[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    record expected-sha FAIL "EXPECTED_SHA must be a full 40-character commit SHA (value not printed)"
    BLOCKED=1
  fi
  if [ -n "${EXPECTED_VERSION:-}" ] && ! [[ "$EXPECTED_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    record expected-version FAIL "EXPECTED_VERSION must be X.Y.Z (the release-please manifest; value not printed)"
    BLOCKED=1
  fi
}

# ── HTTP rows ─────────────────────────────────────────────────────────────────────────────────────────────────────
check_public() {  # target ("GET <path>"), expected http, jq assertion
  local method="${1%% *}" path="${1#* }" want="$2" expr="$3" out="$WORK/body.json" code url cb shown
  if [ "$method" != "GET" ]; then DETAIL="only GET rows are allowed, got $method"; return 1; fi
  cb="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-${RANDOM}"
  url="${BASE%/}${path//\{cb\}/$cb}"
  shown="${path//\{cb\}/<cb>}"
  rm -f "$out"
  code=$(curl -s --max-time 25 -o "$out" -w '%{http_code}' "$url" 2>/dev/null || true)
  DETAIL="GET $shown -> HTTP ${code:-none}"
  if [ "$code" != "$want" ]; then
    DETAIL="$DETAIL, expected $want"
    [ -s "$out" ] && DETAIL="$DETAIL, body $(head -c 200 "$out" | tr -d '\n')"
    return 1
  fi
  [ -n "$expr" ] || return 0
  if ! jq -e --arg sha "$EXPECTED_SHA" --arg version "$EXPECTED_VERSION" "$expr" "$out" >/dev/null 2>&1; then
    DETAIL="$DETAIL but the assertion is false: $expr; body $(head -c 300 "$out" | tr -d '\n')"
    return 1
  fi
  DETAIL="$DETAIL, assertion true"
}

# ── Lambda rows ───────────────────────────────────────────────────────────────────────────────────────────────────
check_lambda() {  # target ("<function> <artifact key>"), expected LastUpdateStatus
  local fn="${1%% *}" key="${1#* }" want="$2" cfg="$WORK/fn.json" obj="$WORK/obj.json"
  local state status code meta_sha meta_code meta_version
  if [ -z "$fn" ] || [ -z "$key" ] || [ "$fn" = "$key" ]; then
    DETAIL="a lambda row needs '<function> <artifact key>'"; return 1
  fi
  if ! aws lambda get-function-configuration --function-name "$fn" --region "$AWS_REGION" --output json \
       > "$cfg" 2> "$WORK/fn.err"; then
    DETAIL="get-function-configuration $fn failed: $(aws_err "$WORK/fn.err")"; return 1
  fi
  state=$(jq -r '.State // "none"' "$cfg")
  status=$(jq -r '.LastUpdateStatus // "none"' "$cfg")
  code=$(jq -r '.CodeSha256 // ""' "$cfg")
  DETAIL="$fn State=$state LastUpdateStatus=$status CodeSha256=${code:-none}"
  if [ "$state" != "Active" ] || [ "$status" != "$want" ]; then
    DETAIL="$DETAIL, expected Active / $want"; return 1
  fi
  if ! aws s3api head-object --bucket "$ARTIFACTS_BUCKET" --key "$key" --region "$AWS_REGION" --output json \
       > "$obj" 2> "$WORK/obj.err"; then
    DETAIL="$DETAIL; head-object of artifact $key failed: $(aws_err "$WORK/obj.err")"; return 1
  fi
  meta_sha=$(jq -r '.Metadata["git-sha"] // ""' "$obj")
  meta_code=$(jq -r '.Metadata["code-sha256"] // ""' "$obj")
  meta_version=$(jq -r '.Metadata["app-version"] // ""' "$obj")
  if [ "$meta_sha" != "$EXPECTED_SHA" ]; then
    if [ -z "$meta_sha" ]; then meta_sha="<no git-sha metadata>"; else meta_sha="$(short "$meta_sha")"; fi
    DETAIL="$DETAIL; artifact $key was built from ${meta_sha}, expected $(short "$EXPECTED_SHA")"
    return 1
  fi
  if [ "$meta_version" != "$EXPECTED_VERSION" ]; then
    DETAIL="$DETAIL; artifact $key has version ${meta_version:-<none>}, expected $EXPECTED_VERSION"; return 1
  fi
  if [ -z "$code" ] || [ "$meta_code" != "$code" ]; then
    DETAIL="$DETAIL; the function does not run artifact $key (its CodeSha256 is ${meta_code:-<none>})"; return 1
  fi
  DETAIL="$DETAIL = artifact $key of $(short "$EXPECTED_SHA") version $EXPECTED_VERSION"
}

run_rows() {
  local line name kind target want expr count=0
  if [ ! -f "$ROWS_FILE" ]; then record rows FAIL "rows file not found: .github/verify/${APP}.rows"; return; fi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    IFS='|' read -r name kind target want expr <<< "$line"
    count=$((count + 1))
    case "$kind" in
      public) retry "$name" check_public "$target" "$want" "$expr" ;;
      lambda) retry "$name" check_lambda "$target" "$want" ;;
      *) record "$name" FAIL "unknown row kind '$kind' (public or lambda)" ;;
    esac
  done < "$ROWS_FILE"
  [ "$count" -gt 0 ] || record rows FAIL "no rows in .github/verify/${APP}.rows"
}

# ── summary ───────────────────────────────────────────────────────────────────────────────────────────────────────
write_summary() {
  local out="${GITHUB_STEP_SUMMARY:-/dev/stdout}" verdict="PASS" row name status detail mark sha_shown
  [ "$FAILED" = "0" ] || verdict="FAIL"
  sha_shown="${EXPECTED_SHA:-unset}"
  [[ "$sha_shown" =~ ^([0-9a-f]{40}|unset)$ ]] || sha_shown="invalid"
  {
    echo "### TripPlanner ${APP} post-deploy verify: ${verdict}"
    echo
    echo "Expected commit \`${sha_shown}\`, version \`${EXPECTED_VERSION:-unset}\`."
    echo
    echo "| Check | Result | Detail |"
    echo "|---|---|---|"
    for row in "${ROWS[@]}"; do
      IFS=$'\t' read -r name status detail <<< "$row"
      mark="PASS"; [ "$status" = "PASS" ] || mark="**FAIL**"
      echo "| ${name} | ${mark} | ${detail//|/\\|} |"
    done
  } >> "$out"
}

# ── main ──────────────────────────────────────────────────────────────────────────────────────────────────────────
BLOCKED=0
TOOLS=(curl jq)
rows_have_kind lambda && TOOLS+=(aws)
for tool in "${TOOLS[@]}"; do
  command -v "$tool" >/dev/null 2>&1 || { record "tool-$tool" FAIL "$tool is not installed"; BLOCKED=1; }
done
check_inputs

if [ "$BLOCKED" = "0" ]; then
  [ "$INITIAL_WAIT" -gt 0 ] && sleep "$INITIAL_WAIT"
  run_rows
else
  # The inputs are wrong: nothing below can be checked, and the table says so.
  record checks FAIL "not run: fix the inputs above"
fi

write_summary
if [ "$FAILED" = "0" ]; then
  echo "All post-deploy checks passed."
  exit 0
fi
echo "Post-deploy verify FAILED (see the table in the job summary)."
exit 1
