#!/usr/bin/env bash
# AA-993 V4 — what the expected commit ships, for .github/workflows/post-deploy-verify.yml: the release-please
# manifest version of EXPECTED_SHA (the same file vercel-deploy.yml passes as APP_VERSION and package_lambdas.sh
# writes into the Lambda zip). Read from that commit, not from the checked-out tree, so a dispatch on main for an
# older SHA compares against what that SHA shipped.
#
# Env: EXPECTED_SHA (full 40-character SHA; an invalid value is never echoed and nothing runs with it).
# Writes version=X.Y.Z to $GITHUB_OUTPUT (stdout when unset). Exit 1 on an invalid SHA, an unknown commit or no
# X.Y.Z version in the manifest.
set -euo pipefail
EXPECTED_SHA="${EXPECTED_SHA:-}"
if ! [[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::expected_sha must be a full 40-character commit SHA (value not printed)"
  exit 1
fi
if ! git cat-file -e "${EXPECTED_SHA}^{commit}" 2>/dev/null; then
  git fetch --no-tags --depth 1 origin "$EXPECTED_SHA" >/dev/null 2>&1 || true
fi
if ! git cat-file -e "${EXPECTED_SHA}^{commit}" 2>/dev/null; then
  echo "::error::commit ${EXPECTED_SHA} is not in this repository"
  exit 1
fi
VERSION="$(git show "${EXPECTED_SHA}:.release-please-manifest.json" 2>/dev/null | jq -r '."." // empty' 2>/dev/null || true)"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "::error::no X.Y.Z version in .release-please-manifest.json at ${EXPECTED_SHA}"
  exit 1
fi
echo "version=${VERSION}" >> "${GITHUB_OUTPUT:-/dev/stdout}"
echo "Expected ${EXPECTED_SHA}: version ${VERSION}"
