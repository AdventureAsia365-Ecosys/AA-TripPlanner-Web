#!/usr/bin/env bash
# AA-TripPlanner-Web pre-push checks (AA-993 H1, root ADR 0016 draft D5): run the CI backend / migration-lint /
# frontend / docs checks locally before a push, with the same commands and pins as .github/workflows/ci.yml and
# docs-lint.yml.
#
# Usage (from anywhere inside the repo):
#   scripts/pre-push.sh              fast mode (default): only the checks the diff vs origin/main needs
#   scripts/pre-push.sh --full       every local check; the backend step is the exact CI command (pytest -v)
#   scripts/pre-push.sh --ci         reserved for H2 (CI calls this script): like --full, never skips by diff,
#                                    and a missing optional tool (lychee) is a failure, not a warning
#   scripts/pre-push.sh --plan       print the checks it would run and exit (no tool is invoked)
#   scripts/pre-push.sh --self-test  test the decision logic on fixture diffs (no tool is invoked)
# Environment: AA_PREPUSH=full (same as --full), AA_PREPUSH_BASE=<ref> (default origin/main),
#   AA_PREPUSH_VENV=<venv dir> (Python 3.12 env with backend/requirements.txt installed at its pins),
#   AA_PREPUSH_PG_DSN=<superuser DSN of a throwaway local PostgreSQL 15 + pgvector> (runs the migration replays).
# Bypass once: git push --no-verify. Uninstall: git config --unset core.hooksPath.
#
# Not run locally (CI only): the PostgreSQL 15 replays and catalog-view SQL tests (unless AA_PREPUSH_PG_DSN is set
# for the replays), gitleaks, PR title. Never runs `next build` (CI "Frontend (lint + build)" / Build).
#
# Errors: `set -uo pipefail` without -e on purpose, so one run reports every failing check instead of stopping at the
# first. It stays fail-closed: run_step records each non-zero exit, a missing tool or env goes through fail_setup, and
# the script exits 1 whenever FAILED is non-empty.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "pre-push: not inside a git repo" >&2; exit 2; }
BYPASS_HINT="push without the checks (once): git push --no-verify"

# ---------------------------------------------------------------------------------------------------------------
# Decision logic (pure: file list in, check ids out). --self-test exercises only this part.
# ---------------------------------------------------------------------------------------------------------------

# classify_file <path> -> space-separated groups
classify_file() {
  local f="$1" groups=""
  case "$f" in
    backend/requirements.txt) groups+=" deps unit_all" ;;
    backend/pytest.ini) groups+=" unit_all" ;;
    backend/__init__.py | backend/tests/__init__.py | backend/tests/conftest.py) groups+=" unit unit_all" ;;
    # Modules, plus the contract copies, fixtures and scripts (package_lambdas.sh) the backend tests load by file name.
    backend/*) groups+=" unit" ;;
    # Read by backend/tests/test_observability.py (found by the basename grep in select_unit_tests).
    .release-please-manifest.json) groups+=" unit" ;;
  esac
  case "$f" in
    migrations/*) groups+=" migrations" ;;
    frontend/*.md) ;;
    frontend/*) groups+=" fe" ;;
    # Read by frontend/lib/health.test.ts, which CI runs with `npm test` on every PR.
    .release-please-manifest.json | .github/workflows/vercel-deploy.yml) groups+=" fe" ;;
    tools/docs/*) groups+=" docs docs_tools" ;;
    scripts/pre-push.sh | scripts/install-hooks.sh | .githooks/*) groups+=" hook" ;;
    # Marker (not a path): the diff deletes or renames a file, which can break a Markdown link.
    :deleted-or-renamed) groups+=" links" ;;
  esac
  case "$f" in
    *.md | docs/*) groups+=" docs" ;;
  esac
  echo "$groups"
}

# plan_checks <mode> [files...] -> one check id per line, in run order. mode: fast | full | ci
# resource-ids and ci-script-tests always run (0.3 s and 3 s, stdlib only). Fast mode never plans unit-full: a shared
# fixture, pytest config or dependency change plans unit-changed and main() prints a warning (see unit_all_files).
plan_checks() {
  local mode="$1"; shift
  local g all=" " f
  if [[ "$mode" != "fast" ]]; then
    all=" hook deps unit_full migrations docs docs_tools fe "
  else
    for f in "$@"; do
      [[ -z "$f" ]] && continue
      for g in $(classify_file "$f"); do
        [[ "$all" == *" $g "* ]] || all+="$g "
      done
    done
    if [[ "$all" == *" unit_all "* ]] && [[ "$all" != *" unit "* ]]; then all+="unit "; fi
  fi
  echo "resource-ids"
  echo "ci-script-tests"
  [[ "$all" == *" hook "* ]] && echo "hook-self-test"
  [[ "$all" == *" deps "* ]] && echo "pip-audit"
  if [[ "$all" == *" unit_full "* ]]; then echo "unit-full"
  elif [[ "$all" == *" unit "* ]]; then echo "unit-changed"; fi
  [[ "$all" == *" migrations "* ]] && echo "migration-lint"
  if [[ "$all" == *" docs "* ]]; then
    [[ "$all" == *" docs_tools "* ]] && echo "docs-tools-tests"
    echo "docs-frontmatter"; echo "docs-adr"; echo "docs-aws-multiline"; echo "docs-links"
  elif [[ "$all" == *" links "* ]]; then
    echo "docs-links"
  fi
  if [[ "$all" == *" fe "* ]]; then echo "fe-lint"; echo "fe-typecheck"; echo "fe-test"; fi
  return 0
}

# unit_all_files [files...] -> the changed files that a targeted test selection cannot cover (shared fixtures, the
# dependency set, pytest config), one per line.
unit_all_files() {
  local f
  for f in "$@"; do
    [[ "$(classify_file "$f")" == *" unit_all"* ]] && echo "$f"
  done
  return 0
}

# changed_files <repo root> <base> -> NUL-separated paths changed on HEAD since the merge base, unquoted (non-ASCII
# names stay as they are). Fails (non-zero) when git cannot compute the diff, e.g. no merge base.
changed_files() {
  git -C "$1" -c core.quotePath=false diff --name-only -z "$2...HEAD"
}

# deletes_or_renames <repo root> <base> -> exit 0 when the diff deletes or renames a file.
deletes_or_renames() {
  [[ "$(git -C "$1" diff --name-only -z --diff-filter=DR "$2...HEAD" | head -c 1 | wc -c)" -gt 0 ]]
}

# select_unit_tests <backend dir> [files...] -> test files under backend/tests (relative to backend/), one per line.
# Changed test files, tests/test_<stem>.py / test_<stem>_*.py of a changed module, the tests importing it (a test
# module imported by other tests included: backend.tests.test_assembly), and the tests whose text names a changed
# file by basename (contract copies under tests/contracts, catalog_fixture.py).
select_unit_tests() {
  local bdir="$1"; shift
  local f rel stem mod pkg leaf t base
  {
    for f in "$@"; do
      [[ -z "$f" ]] && continue
      base="$(basename "$f")"
      if [[ "$base" != "__init__.py" && -d "$bdir/tests" ]]; then
        grep -lF -- "$base" "$bdir"/tests/test_*.py 2>/dev/null | sed "s#^$bdir/##"
      fi
      case "$f" in
        backend/tests/test_*.py) rel="${f#backend/}"; [[ -f "$bdir/$rel" ]] && echo "$rel" ;;
      esac
      case "$f" in
        backend/*.py)
          mod="${f%.py}"; mod="${mod//\//.}"; stem="$(basename "$f" .py)"
          [[ "$stem" == "__init__" ]] && { mod="${mod%.__init__}"; stem="${mod##*.}"; }
          pkg="${mod%.*}"; leaf="${mod##*.}"
          for t in "$bdir"/tests/test_"$stem".py "$bdir"/tests/test_"$stem"_*.py; do
            [[ -f "$t" ]] && echo "tests/${t##*/}"
          done
          [[ -d "$bdir/tests" ]] && grep -lE "(^|[^A-Za-z0-9_.])${mod//./\\.}([^A-Za-z0-9_]|$)|from ${pkg//./\\.} import .*\b${leaf}\b" \
            "$bdir"/tests/test_*.py 2>/dev/null | sed "s#^$bdir/##"
          ;;
      esac
    done
  } | sort -u
}

# ---------------------------------------------------------------------------------------------------------------
# --self-test
# ---------------------------------------------------------------------------------------------------------------
self_test() {
  local fails=0 n=0
  expect() { # expect <name> <expected ids, space-separated> <mode> [files...]
    local name="$1" want="$2" mode="$3"; shift 3
    local got; got="$(plan_checks "$mode" "$@" | tr '\n' ' ' | sed 's/ $//')"
    n=$((n + 1))
    if [[ "$got" == "$want" ]]; then echo "ok   $name"; else echo "FAIL $name"; echo "     want: $want"; echo "     got:  $got"; fails=$((fails + 1)); fi
  }
  local BASE_IDS="resource-ids ci-script-tests"
  local DOCS="docs-frontmatter docs-adr docs-aws-multiline docs-links"
  local FE="fe-lint fe-typecheck fe-test"
  local ALL="$BASE_IDS hook-self-test pip-audit unit-full migration-lint docs-tools-tests $DOCS $FE"
  expect "empty diff: resource IDs + CI script tests only" "$BASE_IDS" fast
  expect "browse module" "$BASE_IDS unit-changed" fast backend/browse/handler.py
  expect "test file" "$BASE_IDS unit-changed" fast backend/tests/test_routes.py
  expect "contract copy" "$BASE_IDS unit-changed" fast backend/tests/contracts/catalog.tour.v1.2.json
  expect "conftest -> targeted unit + warning (never the full suite in fast mode)" "$BASE_IDS unit-changed" fast \
    backend/tests/conftest.py
  expect "pytest.ini -> targeted unit + warning" "$BASE_IDS unit-changed" fast backend/pytest.ini
  expect "requirements -> pip-audit + targeted unit + warning" "$BASE_IDS pip-audit unit-changed" fast \
    backend/requirements.txt
  expect "Lambda packaging script -> targeted unit (test_observability reads it)" "$BASE_IDS unit-changed" fast \
    backend/package_lambdas.sh
  expect "release manifest -> targeted unit + frontend tests (both read it)" "$BASE_IDS unit-changed $FE" fast \
    .release-please-manifest.json
  expect "Vercel deploy workflow -> frontend tests (health.test.ts reads it)" "$BASE_IDS $FE" fast \
    .github/workflows/vercel-deploy.yml
  expect "migration -> migration lint" "$BASE_IDS migration-lint" fast migrations/021_example.sql
  expect "migration test -> migration lint" "$BASE_IDS migration-lint" fast migrations/tests/migration_lint.py
  expect "frontend -> lint + tsc + node tests, never build" "$BASE_IDS $FE" fast frontend/app/page.tsx
  expect "frontend README -> docs only" "$BASE_IDS $DOCS" fast frontend/README.md
  expect "markdown -> docs lint" "$BASE_IDS $DOCS" fast CONTEXT.md
  expect "markdown with a space in the name" "$BASE_IDS $DOCS" fast "docs/a b.md"
  expect "non-ASCII markdown name" "$BASE_IDS $DOCS" fast "docs/Tài liệu.md"
  expect "docs tools -> their unit tests too" "$BASE_IDS docs-tools-tests $DOCS" fast tools/docs/check_frontmatter.py
  expect "workflow / CI script -> CI script tests (always on)" "$BASE_IDS" fast .github/workflows/ci.yml \
    .github/scripts/post_deploy_verify.sh
  expect "hook files -> hook self-test" "$BASE_IDS hook-self-test" fast scripts/pre-push.sh .githooks/pre-push
  expect "delete or rename -> link check" "$BASE_IDS docs-links" fast :deleted-or-renamed
  expect "mixed diff" "$BASE_IDS unit-changed migration-lint $DOCS $FE" fast \
    backend/assembly/handler.py docs/implementation-notes/AA-993.md frontend/lib/trip.ts migrations/021_x.sql
  expect "--full ignores the diff" "$ALL" full
  expect "--ci never skips by diff" "$ALL" ci README.md

  local got
  got="$(unit_all_files backend/browse/handler.py backend/tests/conftest.py backend/requirements.txt backend/pytest.ini \
    | tr '\n' ' ' | sed 's/ $//')"
  n=$((n + 1))
  if [[ "$got" == "backend/tests/conftest.py backend/requirements.txt backend/pytest.ini" ]]; then echo "ok   unit_all warning files"
  else echo "FAIL unit_all warning files"; echo "     got:  $got"; fails=$((fails + 1)); fi

  # Failure records survive a '|' in the display command (docs-links pipes git ls-files into lychee).
  local rid rdir rshown rci
  IFS=$SEP read -r rid rdir rshown rci <<<"$(fail_record docs-links . "git ls-files -z '*.md' | xargs -0 -r lychee" \
    "docs-lint.yml / Internal links (offline)")"
  n=$((n + 1))
  if [[ "$rid|$rdir|$rshown|$rci" == "docs-links|.|git ls-files -z '*.md' | xargs -0 -r lychee|docs-lint.yml / Internal links (offline)" ]]
  then echo "ok   failure record round-trips a command containing '|'"
  else echo "FAIL failure record round-trip"; echo "     got: [$rid] [$rdir] [$rshown] [$rci]"; fails=$((fails + 1)); fi

  # Test selection on a fixture tree (no pytest run).
  local tmp; tmp="$(mktemp -d)"
  mkdir -p "$tmp/tests"
  : >"$tmp/tests/test_routes.py"; : >"$tmp/tests/test_routes_extra.py"
  echo "from backend.browse import handler as h" >"$tmp/tests/test_browse.py"
  echo "from backend.browse.filters import BrowseFilters" >"$tmp/tests/test_filters_user.py"
  echo "import backend.browse.filtersx" >"$tmp/tests/test_not_this.py"
  echo "from backend.tests.test_assembly import FakeConn, _session" >"$tmp/tests/test_assembly_reads.py"
  : >"$tmp/tests/test_assembly.py"
  echo 'CONTRACT = HERE / "contracts" / "inquiry.submitted.v1.4.json"' >"$tmp/tests/test_handoff_contract.py"
  echo "from backend import config" >"$tmp/tests/test_auth.py"
  sel() { select_unit_tests "$tmp" "$@" | tr '\n' ' ' | sed 's/ $//'; }
  check_sel() { # check_sel <name> <want> [files...]
    local name="$1" want="$2"; shift 2
    local got; got="$(sel "$@")"
    n=$((n + 1))
    if [[ "$got" == "$want" ]]; then echo "ok   $name"; else echo "FAIL $name"; echo "     want: $want"; echo "     got:  $got"; fails=$((fails + 1)); fi
  }
  check_sel "select: tests named after the module" "tests/test_routes.py tests/test_routes_extra.py" backend/browse/routes.py
  check_sel "select: tests importing the module (from pkg import leaf)" "tests/test_browse.py" backend/browse/handler.py
  check_sel "select: tests importing the module (dotted)" "tests/test_filters_user.py" backend/browse/filters.py
  check_sel "select: top-level module (from backend import config)" "tests/test_auth.py" backend/config.py
  check_sel "select: changed test file + tests importing it" "tests/test_assembly.py tests/test_assembly_reads.py" \
    backend/tests/test_assembly.py
  check_sel "select: contract copy named in a test" "tests/test_handoff_contract.py" \
    backend/tests/contracts/inquiry.submitted.v1.4.json
  check_sel "select: module without tests -> nothing" "" backend/shared/nothing_here.py
  # Not by file name (every package has one), but importing backend.tests.<x> runs backend/tests/__init__.py.
  check_sel "select: package __init__.py -> tests importing the package, not by file name" \
    "tests/test_assembly_reads.py" backend/tests/__init__.py
  check_sel "select: package __init__ -> tests of the package name" "" backend/extraction/__init__.py
  rm -rf "$tmp"

  # The diff reader on a scratch repo: unquoted non-ASCII and spaced names, a failing diff is reported.
  tmp="$(mktemp -d)"
  local g=(git -C "$tmp" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false -c core.hooksPath=/dev/null)
  local files=()
  if "${g[@]}" init -q -b main 2>/dev/null && "${g[@]}" commit -q --allow-empty -m base &&
    mkdir -p "$tmp/docs" && : >"$tmp/docs/a b.md" && : >"$tmp/docs/Tài.md" && "${g[@]}" add -A &&
    "${g[@]}" commit -q -m docs; then
    mapfile -d '' -t files < <(changed_files "$tmp" main~1)
    got="$(plan_checks fast "${files[@]}" | tr '\n' ' ' | sed 's/ $//')"
    n=$((n + 1))
    if [[ ${#files[@]} -eq 2 && "$got" == "$BASE_IDS $DOCS" ]]; then echo "ok   diff reader: non-ASCII and spaced names"
    else echo "FAIL diff reader: non-ASCII and spaced names"; echo "     files: ${files[*]}"; echo "     got:  $got"; fails=$((fails + 1)); fi
    n=$((n + 1))
    if changed_files "$tmp" no-such-ref >/dev/null 2>&1; then echo "FAIL diff reader: a bad base must fail"; fails=$((fails + 1))
    else echo "ok   diff reader: a bad base fails"; fi
    n=$((n + 1))
    "${g[@]}" mv "docs/a b.md" docs/c.md && "${g[@]}" commit -q -m mv
    if deletes_or_renames "$tmp" main~1 && ! deletes_or_renames "$tmp" main~2; then echo "ok   diff reader: deletes/renames"
    else echo "FAIL diff reader: deletes/renames"; fails=$((fails + 1)); fi
  else
    n=$((n + 1)); echo "FAIL diff reader: could not build the scratch repo"; fails=$((fails + 1))
  fi
  rm -rf "$tmp"

  echo "self-test: $((n - fails))/$n passed"
  [[ $fails -eq 0 ]]
}

# ---------------------------------------------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------------------------------------------

# pin_mismatches <python> -> prints the backend/requirements.txt pins that python does not satisfy ("" = all match);
# exit 1 when any is missing or at another version. CI installs exactly these pins (ci.yml / Install dependencies).
pin_mismatches() {
  "$1" - "$REPO_ROOT/backend/requirements.txt" <<'PYEOF' 2>/dev/null
import re, sys
from importlib import metadata
bad = []
with open(sys.argv[1], encoding="utf-8") as fh:
    for line in fh:
        m = re.match(r"\s*([A-Za-z0-9_.-]+)(\[[^\]]*\])?==([^\s;#]+)", line.split("#", 1)[0])
        if not m:
            continue
        name, want = m.group(1), m.group(3)
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            bad.append(f"{name} missing (CI {want})")
            continue
        if have != want:
            bad.append(f"{name} {have} (CI {want})")
if sys.version_info[:2] != (3, 12):
    bad.append(f"python {sys.version_info[0]}.{sys.version_info[1]} (CI 3.12)")
print("; ".join(bad))
sys.exit(1 if bad else 0)
PYEOF
}

# The backend Python: AA_PREPUSH_VENV, this checkout's .venv or backend/.venv, then the main checkout's (worktrees
# share it), then python3 on PATH. The first one that matches every backend/requirements.txt pin wins; when none
# does, BACKEND_PY stays empty and BACKEND_PY_WHY says what the first usable candidate lacks.
BACKEND_PY=""
BACKEND_PY_WHY="no Python found"
resolve_backend_python() {
  local main_root cand why first=""
  main_root="$(cd "$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
  for cand in "${AA_PREPUSH_VENV:+$AA_PREPUSH_VENV/bin/python}" "$REPO_ROOT/.venv/bin/python" \
    "$REPO_ROOT/backend/.venv/bin/python" "$main_root/.venv/bin/python" "$main_root/backend/.venv/bin/python" \
    "$(command -v python3 || true)"; do
    [[ -n "$cand" && -x "$cand" ]] || continue
    if why="$(pin_mismatches "$cand")"; then BACKEND_PY="$cand"; return 0; fi
    if [[ -z "$first" ]]; then
      first="$cand"; BACKEND_PY_WHY="$cand: ${why:-cannot read the pins}"
      # A stale venv is updated in place; the system python3 is not (PEP 668), a new venv is created instead.
      [[ "$cand" != "$(command -v python3 || true)" ]] && BACKEND_FIX="$cand -m pip install -r backend/requirements.txt"
    fi
  done
  return 1
}
BACKEND_FIX="python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt (or set AA_PREPUSH_VENV)"

# last_failed_tests <backend dir> -> backend test files recorded as failed in pytest's cache that still exist,
# relative to backend/, one per line.
last_failed_tests() {
  [[ -n "$BACKEND_PY" && -s "$1/.pytest_cache/v/cache/lastfailed" ]] || return 0
  "$BACKEND_PY" - "$1" <<'PYEOF' 2>/dev/null || true
import json, os, sys
root = sys.argv[1]
try:
    with open(os.path.join(root, ".pytest_cache/v/cache/lastfailed"), encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    sys.exit(0)
seen = set()
for key in data if isinstance(data, dict) else ():
    if not isinstance(key, str) or not key.startswith("tests/"):
        continue
    path = key.split("::", 1)[0]
    if path.endswith(".py") and path not in seen and os.path.isfile(os.path.join(root, path)):
        seen.add(path)
        print(path)
PYEOF
}

# Python for tools/docs (needs tools/docs/requirements.txt: jsonschema, PyYAML).
resolve_docs_python() {
  local cand
  for cand in "$BACKEND_PY" "$(command -v python3 || true)"; do
    [[ -n "$cand" && -x "$cand" ]] || continue
    if "$cand" -c "import jsonschema, yaml" >/dev/null 2>&1; then echo "$cand"; return 0; fi
  done
  return 1
}

# ---------------------------------------------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------------------------------------------
FAILED=()
WARNED=()
# FAILED entries: id, dir, display command, CI reference joined by the ASCII unit separator, which no command contains
# (a '|' does: docs-links pipes git ls-files into lychee).
SEP=$'\x1f'
fail_record() { printf '%s' "$1$SEP$2$SEP$3$SEP$4"; }
LYCHEE_HINT="install lychee v0.24.2 (the version lychee-action v2.9.0 runs in CI): a release binary from https://github.com/lycheeverse/lychee/releases, or cargo install lychee --version 0.24.2"

# run_step <check id> <dir relative to repo root> <ci reference> <display command> -- <argv...>
run_step() {
  local id="$1" dir="$2" ci="$3" shown="$4"; shift 5
  local t0 rc
  t0=$(date +%s)
  echo
  echo "==> [$id] (cd ${dir:-.} && $shown)"
  (cd "$REPO_ROOT/$dir" && "$@")
  rc=$?
  if [[ $rc -eq 0 ]]; then
    echo "    ok ($(( $(date +%s) - t0 ))s)"
  else
    echo "    FAILED (exit $rc, $(( $(date +%s) - t0 ))s)"
    FAILED+=("$(fail_record "$id" "${dir:-.}" "$shown" "$ci")")
  fi
}

# fail_setup <check id> <ci reference> <what is missing> <how to fix>
fail_setup() {
  echo
  echo "==> [$1] cannot run: $3"
  echo "    fix: $4"
  FAILED+=("$(fail_record "$1" . "$4" "$2")")
}

run_check() {
  local id="$1"
  case "$id" in
    resource-ids)
      run_step "$id" "" "ci.yml / Backend (pytest) / No literal AWS resource IDs" \
        "python3 .github/scripts/check_resource_ids.py" -- python3 .github/scripts/check_resource_ids.py ;;
    ci-script-tests)
      run_step "$id" "" "ci.yml / Backend (pytest) / CI script tests" \
        "python3 -m unittest discover -s .github/scripts -p 'test_*.py'" -- \
        python3 -m unittest discover -s .github/scripts -p 'test_*.py' ;;
    pip-audit)
      local want="pip-audit==2.10.1" apy
      apy="${BACKEND_PY:-$(command -v python3 || true)}"
      if [[ -z "$apy" ]] || ! "$apy" -m pip_audit --version 2>/dev/null | grep -qE "(^| )2\.10\.1\$"; then
        fail_setup "$id" "ci.yml / Security Audit / pip-audit" "$want not installed in ${apy:-any python3}" \
          "${apy:-python3} -m pip install $want"
        return
      fi
      run_step "$id" "" "ci.yml / Security Audit / pip-audit (Lambda requirements, failing)" \
        "pip-audit -r backend/requirements.txt" -- "$apy" -m pip_audit -r backend/requirements.txt ;;
    unit-full | unit-changed)
      local tests=() last=()
      if [[ "$id" == "unit-changed" ]]; then
        mapfile -t tests < <(select_unit_tests "$REPO_ROOT/backend" "${CHANGED[@]}")
        if [[ -n "$BACKEND_PY" ]]; then
          # Plus the test files that failed last time and still exist (pytest's cache), in the same run.
          mapfile -t last < <(last_failed_tests "$REPO_ROOT/backend")
          if [[ ${#last[@]} -gt 0 ]]; then
            echo "    + ${#last[@]} test file(s) that failed last time"
            mapfile -t tests < <(printf '%s\n' "${tests[@]}" "${last[@]}" | sed '/^$/d' | sort -u)
          fi
        fi
        if [[ ${#tests[@]} -eq 0 ]]; then
          echo
          echo "==> [$id] no backend test matches the changed modules (--full runs the CI command)"
          return
        fi
      fi
      if [[ -z "$BACKEND_PY" ]]; then
        fail_setup "$id" "ci.yml / Backend (pytest) / Run unit tests" \
          "no Python with backend/requirements.txt at its pins ($BACKEND_PY_WHY)" "$BACKEND_FIX"
        return
      fi
      if [[ "$id" == "unit-full" ]]; then
        run_step "$id" backend "ci.yml / Backend (pytest) / Run unit tests" \
          "PYTHONPATH=\$PWD/.. pytest -v --tb=short" -- env PYTHONPATH="$REPO_ROOT" "$BACKEND_PY" -m pytest -v --tb=short
      else
        run_step "$id" backend "ci.yml / Backend (pytest) / Run unit tests (changed modules only; CI runs all)" \
          "PYTHONPATH=\$PWD/.. pytest ${tests[*]} -q --tb=short" -- \
          env PYTHONPATH="$REPO_ROOT" "$BACKEND_PY" -m pytest "${tests[@]}" -q --tb=short
      fi ;;
    migration-lint)
      # The CI job installs asyncpg + pytest only; the backend env has both. Without a PostgreSQL the replay modules
      # skip, so locally this is the offline lint (migration_lint.py rules); AA_PREPUSH_PG_DSN runs the replays too.
      if [[ -z "$BACKEND_PY" ]]; then
        fail_setup "$id" "ci.yml / Migrations (lint, PG 15 replay)" \
          "no Python with backend/requirements.txt at its pins ($BACKEND_PY_WHY)" "$BACKEND_FIX"
        return
      fi
      local pg=() shown="pytest migrations/tests -q --tb=short -rs -p no:cacheprovider --ignore=migrations/tests/test_infra_runner.py"
      if [[ -n "${AA_PREPUSH_PG_DSN:-}" ]]; then
        pg=(--fail-on-skip --pg-dsn "$AA_PREPUSH_PG_DSN" --pg-major 15 --pg-require-vector)
        shown+=" --fail-on-skip --pg-dsn \$AA_PREPUSH_PG_DSN --pg-major 15 --pg-require-vector"
      else
        WARNED+=("$id: the PostgreSQL 15 replays were skipped (no AA_PREPUSH_PG_DSN); CI runs them")
      fi
      run_step "$id" "" "ci.yml / Migrations (lint, PG 15 replay)" "$shown" -- \
        "$BACKEND_PY" -m pytest migrations/tests -q --tb=short -rs -p no:cacheprovider \
        --ignore=migrations/tests/test_infra_runner.py "${pg[@]}" ;;
    docs-tools-tests | docs-frontmatter | docs-adr | docs-aws-multiline)
      # check_frontmatter.py (and the tools' tests, which import it) need jsonschema + PyYAML; the ADR and AWS text
      # checks are stdlib only and run on any python3.
      local dpy=""
      if [[ "$id" == "docs-frontmatter" || "$id" == "docs-tools-tests" ]] && ! dpy="$(resolve_docs_python)"; then
        fail_setup "$id" "docs-lint.yml" "no Python with jsonschema + PyYAML" \
          "python3 -m pip install -r tools/docs/requirements.txt"
        return
      fi
      [[ -z "$dpy" ]] && dpy="$(resolve_docs_python || command -v python3)"
      if [[ -z "$dpy" ]]; then
        fail_setup "$id" "docs-lint.yml" "no python3 on PATH" "install Python 3.12, then python3 -m pip install -r tools/docs/requirements.txt"
        return
      fi
      case "$id" in
        docs-tools-tests) run_step "$id" "" "docs-lint.yml / Unit tests of the lint tools" \
          "python -m unittest discover -s tools/docs -p 'test_*.py'" -- "$dpy" -m unittest discover -s tools/docs -p 'test_*.py' ;;
        docs-frontmatter) run_step "$id" "" "docs-lint.yml / Front-matter schema" \
          "python tools/docs/check_frontmatter.py" -- "$dpy" tools/docs/check_frontmatter.py ;;
        docs-adr) run_step "$id" "" "docs-lint.yml / ADR status and index" \
          "python tools/docs/check_adr_index.py docs/adr" -- "$dpy" tools/docs/check_adr_index.py docs/adr ;;
        docs-aws-multiline) run_step "$id" "" "docs-lint.yml / Single-line AWS CLI" \
          "python tools/docs/check_docs_text.py aws-multiline --all" -- "$dpy" tools/docs/check_docs_text.py aws-multiline --all ;;
      esac ;;
    docs-links)
      if ! command -v lychee >/dev/null 2>&1; then
        if [[ "$MODE" == "ci" ]]; then
          fail_setup "$id" "docs-lint.yml / Internal links (offline)" "lychee not installed" "$LYCHEE_HINT"
        else
          echo; echo "==> [$id] skipped: lychee not installed (CI checks internal links)"
          WARNED+=("$id: lychee not installed; $LYCHEE_HINT")
        fi
        return
      fi
      if ! lychee --version 2>/dev/null | grep -qE "(^| )0\.24\.2\$"; then
        WARNED+=("$id: $(lychee --version 2>/dev/null | head -n 1) is not the CI version; $LYCHEE_HINT")
      fi
      # The tracked pages only (what the CI checkout has): a local './**/*.md' would also walk frontend/node_modules
      # and .venv.
      run_step "$id" "" "docs-lint.yml / Internal links (offline)" \
        "git ls-files -z '*.md' | xargs -0 -r lychee --config tools/docs/lychee.toml" -- \
        bash -c 'git ls-files -z "*.md" | xargs -0 -r lychee --config tools/docs/lychee.toml' ;;
    hook-self-test)
      run_step "$id" "" "this hook's own decision tests (not a CI step)" \
        "bash scripts/pre-push.sh --self-test" -- bash scripts/pre-push.sh --self-test ;;
    fe-lint | fe-typecheck | fe-test)
      if [[ ! -d "$REPO_ROOT/frontend/node_modules" ]]; then
        fail_setup "$id" "ci.yml / Frontend (lint + build)" "frontend/node_modules missing" "(cd frontend && npm ci)"
        return
      fi
      # npm test runs node --test --experimental-strip-types, which needs Node >= 22.6.
      local nv; nv="$(node --version 2>/dev/null)"; nv="${nv#v}"
      if [[ ! "$nv" =~ ^([0-9]+)\.([0-9]+) ]] || (( BASH_REMATCH[1] < 22 || (BASH_REMATCH[1] == 22 && BASH_REMATCH[2] < 6) )); then
        fail_setup "$id" "ci.yml / Frontend (lint + build)" "node ${nv:-not found} (npm test needs >= 22.6)" \
          "CI uses Node 22 (setup-node node-version 22): install Node 22.6 or later"
        return
      fi
      case "$id" in
        fe-lint) run_step "$id" frontend "ci.yml / Frontend (lint + build) / Lint" "npm run lint" -- npm run lint --silent ;;
        fe-typecheck) run_step "$id" frontend "ci.yml / Frontend (lint + build) / Type-check" "npm run typecheck" -- \
          npm run typecheck --silent ;;
        fe-test) run_step "$id" frontend "ci.yml / Frontend (lint + build) / Unit tests" "npm test" -- npm test --silent ;;
      esac ;;
    *) echo "pre-push: unknown check $id" >&2; FAILED+=("$(fail_record "$id" . "unknown check" -)") ;;
  esac
}

# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------
MODE="fast"
PLAN_ONLY=0
[[ "${AA_PREPUSH:-}" == "full" ]] && MODE="full"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) [[ "$MODE" != "ci" ]] && MODE="full" ;;
    --ci) MODE="ci" ;;
    --plan) PLAN_ONLY=1 ;;
    --self-test) self_test; exit $? ;;
    -h | --help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "pre-push: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

BASE="${AA_PREPUSH_BASE:-origin/main}"
CHANGED=()
PLAN_INPUT=()
if [[ "$MODE" == "fast" ]]; then
  if ! git -C "$REPO_ROOT" rev-parse --verify -q "$BASE" >/dev/null; then
    echo "pre-push: $BASE not found (git fetch origin?), running every check"
    MODE="full"
  else
    # A failing diff (no merge base: shallow clone, rewritten main) must not look like an empty diff.
    diff_out="$(mktemp)"
    if changed_files "$REPO_ROOT" "$BASE" >"$diff_out"; then
      mapfile -d '' -t CHANGED <"$diff_out"
      PLAN_INPUT=("${CHANGED[@]}")
      deletes_or_renames "$REPO_ROOT" "$BASE" && PLAN_INPUT+=(":deleted-or-renamed")
      mapfile -t unit_all < <(unit_all_files "${CHANGED[@]}")
      if [[ ${#unit_all[@]} -gt 0 ]]; then
        WARNED+=("shared fixture/pytest config/deps changed (${unit_all[*]}): CI runs the whole backend suite; run AA_PREPUSH=full git push to run it locally")
      fi
    else
      echo "pre-push: cannot diff $BASE...HEAD, running every check"
      MODE="full"
    fi
    rm -f "$diff_out"
  fi
fi

mapfile -t CHECKS < <(plan_checks "$MODE" "${PLAN_INPUT[@]}")
echo "pre-push ($MODE mode): ${#CHANGED[@]} file(s) changed vs $BASE; checks: ${CHECKS[*]}"
[[ $PLAN_ONLY -eq 1 ]] && exit 0
if [[ "$MODE" == "fast" && -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo "note: the working tree has uncommitted changes; the checks read the working tree, the push sends commits"
fi

# Only the checks that run Python on the backend env need it (resolving reads every pin: ~0.1 s per candidate).
if [[ " ${CHECKS[*]} " == *" unit-"* || " ${CHECKS[*]} " == *" migration-lint "* || " ${CHECKS[*]} " == *" pip-audit "* ]]; then
  resolve_backend_python || true
fi
T_START=$(date +%s)
for c in "${CHECKS[@]}"; do run_check "$c"; done
ELAPSED=$(( $(date +%s) - T_START ))

echo
for w in "${WARNED[@]}"; do echo "warning: $w"; done
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "pre-push: all ${#CHECKS[@]} check(s) passed in ${ELAPSED}s ($MODE mode)"
  exit 0
fi
echo "pre-push: ${#FAILED[@]} check(s) FAILED in ${ELAPSED}s ($MODE mode). Re-run the failing command to see it:"
for f in "${FAILED[@]}"; do
  IFS=$SEP read -r id dir shown ci <<<"$f"
  echo "  - [$id] CI: $ci"
  echo "      (cd $dir && $shown)"
done
echo "$BYPASS_HINT"
exit 1
