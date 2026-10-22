#!/usr/bin/env bash
# =============================================================================
# Package the two TripPlanner Lambdas into dist/tripplanner/{browse,assembly}.zip
# for AA-CIS-Infra's accounts/aa365/tripplanner.tf to deploy.
#
# Both Lambdas run the SAME backend/ package; they differ only in handler
# entrypoint (backend.browse.handler.handler vs backend.assembly.handler.handler),
# so the two zips have identical content. We build once and copy.
#
# Runtime deps (requirements.txt, minus dev/test) are installed for the Lambda
# runtime platform (python3.12, manylinux) so native wheels (asyncpg, pgvector)
# match the Lambda environment — not the build host.
#
# Usage (run from repo root or backend/):
#   bash backend/package_lambdas.sh
# Output:
#   dist/tripplanner/browse.zip
#   dist/tripplanner/assembly.zip
# =============================================================================
set -euo pipefail

# Resolve repo root (this script lives in backend/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

DIST_DIR="${REPO_ROOT}/dist/tripplanner"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

PY_VERSION="3.12"

echo "==> Installing runtime dependencies into build dir (${BUILD_DIR})"
# Only the runtime block of requirements.txt (stop at the '# Dev / test' line).
RUNTIME_REQS="$(mktemp)"
awk '/# Dev \/ test/{exit} {print}' backend/requirements.txt > "${RUNTIME_REQS}"

python3 -m pip install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version "${PY_VERSION}" \
  --only-binary=:all: \
  --target "${BUILD_DIR}" \
  -r "${RUNTIME_REQS}"

echo "==> Copying backend/ package into build dir"
# The handlers import `backend.*`, so the package must sit at the zip root.
cp -r backend "${BUILD_DIR}/backend"

# AA-871: the commit the zip was built from, for the Sentry release and the git_sha log field
# (backend/shared/observability.py git_sha(); the Lambda environment is Terraform's, not this workflow's).
GIT_SHA_VALUE="${GITHUB_SHA:-$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)}"
printf '%s\n' "${GIT_SHA_VALUE}" > "${BUILD_DIR}/backend/GIT_SHA"

# AA-891: the release version next to it (the `version` log field, observability.release_version()): the
# release-please manifest of the commit being packaged, so a build between two releases reports the last release
# it contains (git_sha tells the exact commit).
APP_VERSION_VALUE="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["."])' \
  "${REPO_ROOT}/.release-please-manifest.json" 2>/dev/null || echo unknown)"
printf '%s\n' "${APP_VERSION_VALUE}" > "${BUILD_DIR}/backend/VERSION"
echo "==> Packaged version ${APP_VERSION_VALUE} (${GIT_SHA_VALUE})"

# Drop tests, caches, local-only files, and the packaging script itself from
# the deployment artifact.
find "${BUILD_DIR}/backend" -type d -name '__pycache__' -prune -exec rm -rf {} +
rm -rf "${BUILD_DIR}/backend/tests"
rm -f  "${BUILD_DIR}/backend/.env" "${BUILD_DIR}/backend/.env.example"
rm -f  "${BUILD_DIR}/backend/package_lambdas.sh"
rm -f  "${BUILD_DIR}/backend/pytest.ini"

echo "==> Creating ${DIST_DIR}"
mkdir -p "${DIST_DIR}"
rm -f "${DIST_DIR}/browse.zip" "${DIST_DIR}/assembly.zip"

echo "==> Zipping"
( cd "${BUILD_DIR}" && zip -q -r "${DIST_DIR}/browse.zip" . )
# Identical content for the assembly Lambda (different handler entrypoint only).
cp "${DIST_DIR}/browse.zip" "${DIST_DIR}/assembly.zip"

echo "==> Done:"
ls -lh "${DIST_DIR}"/*.zip
